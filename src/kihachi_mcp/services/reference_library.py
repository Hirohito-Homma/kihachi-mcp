"""One local SQLite library for personal, Freesound and Jamendo audio."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
import statistics
import threading
import uuid
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import urlparse

from kihachi_mcp.knowledge.genre_database import canonical_genre, find, match_genres
from kihachi_mcp.services.live_paths import bridge_state_dir
from kihachi_mcp.services.reference_analysis import (
    EXTENSIONS,
    VERSION,
    ReferenceError,
    analyze,
    capabilities,
)

MAX_BYTES = 256 * 1024 * 1024
KINDS = {"track", "stem", "loop", "one_shot"}
SAMPLE_ROLES = {"kick", "hats", "snare", "clap", "other"}
SOURCES = {"local", "freesound", "jamendo"}
_CUSTOM_GENRE = re.compile(r"[A-Za-z][A-Za-z0-9 &'/+-]{0,63}\Z")


def default_library_dir() -> Path:
    return Path(
        os.environ.get("KIHACHI_REFERENCE_DIR")
        or Path(bridge_state_dir()) / "references"
    )


def _text(data: dict, key: str, limit: int = 300) -> str:
    value = data.get(key, "")
    if not isinstance(value, str) or len(value) > limit:
        raise ReferenceError(f"{key} の文字数・形式を確認してください")
    return value.strip()


def validate_metadata(data: dict) -> dict:
    source, kind = _text(data, "source"), _text(data, "kind")
    if source not in SOURCES or kind not in KINDS:
        raise ReferenceError("出典と音源の種類を選択してください")
    sample_role = _text(data, "sample_role")
    if sample_role and (kind != "one_shot" or sample_role not in SAMPLE_ROLES):
        raise ReferenceError("ワンショットの役割を選択してください")
    if data.get("rights_confirmed") is not True:
        raise ReferenceError("音源の利用条件を確認してチェックしてください")
    title, license_name = _text(data, "title"), _text(data, "license")
    if not title or not license_name:
        raise ReferenceError("タイトルと利用条件を入力してください")
    url = _text(data, "source_url", 1000)
    if url:
        parsed = urlparse(url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.query
            or parsed.fragment
        ):
            raise ReferenceError(
                "出典は認証情報やクエリを含まないHTTPSの作品ページURLにしてください"
            )
    if source != "local":
        host = urlparse(url).hostname
        hosts = {
            "freesound": {"freesound.org", "www.freesound.org"},
            "jamendo": {"jamendo.com", "www.jamendo.com"},
        }
        if host not in hosts[source]:
            raise ReferenceError("出典サービスの作品ページURLが必要です")
    raw_genres = _text(data, "genres", 500)
    genres = []
    for name in re.split(r"[,、]", raw_genres):
        if not name.strip():
            continue
        genre = find(name.strip())
        matches = match_genres(name.strip()) if genre is None else ()
        if (
            genre is None
            and len(matches) == 1
            and matches[0].start == 0
            and matches[0].end == len(name.strip())
        ):
            genre = matches[0].genre
        if genre is None:
            if not _CUSTOM_GENRE.fullmatch(name.strip()):
                raise ReferenceError(
                    f"ジャンル「{name[:50]}」は一覧にありません。英数字の独自ジャンル名を入力してください"
                )
            slug = name.strip()
        else:
            slug = genre.slug
        if slug not in genres:
            genres.append(slug)
    bpm = data.get("confirmed_bpm")
    if bpm in ("", None):
        bpm = None
    else:
        try:
            bpm = float(bpm)
        except (TypeError, ValueError):
            raise ReferenceError("確認済みBPMは数値で入力してください") from None
        if not math.isfinite(bpm) or not 20 <= bpm <= 400:
            raise ReferenceError("確認済みBPMは20〜400で入力してください")
    return {
        "source": source,
        "kind": kind,
        "sample_role": sample_role,
        "title": title,
        "license": license_name,
        "creator": _text(data, "creator"),
        "source_url": url,
        "genres": genres,
        "notes": _text(data, "notes", 4000),
        "confirmed_bpm": bpm,
        "rights_confirmed": True,
        "license_verification": "user_declared",
    }


class ReferenceLibrary:
    """All imports are explicit. No external fetching, Live writes or model use."""

    def __init__(self, directory: Path | None = None, analyzer=analyze):
        self.directory = directory or default_library_dir()
        self.db_path = self.directory / "library.sqlite3"
        self._analyzer = analyzer
        self._lock = threading.Lock()

    def _connect(self) -> sqlite3.Connection:
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.directory, 0o700)
        audio_dir = self.directory / "audio"
        if audio_dir.is_dir():
            os.chmod(audio_dir, 0o700)
        connection = sqlite3.connect(self.db_path, timeout=15)
        os.chmod(self.db_path, 0o600)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1):
            connection.close()
            raise ReferenceError(
                "このDBは新しい版で作られています。対応するStudioを使用してください"
            )
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS assets (
                sha256 TEXT PRIMARY KEY, filename TEXT NOT NULL, bytes INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS analyses (
                sha256 TEXT NOT NULL REFERENCES assets(sha256), version TEXT NOT NULL,
                result TEXT NOT NULL, created_at TEXT NOT NULL,
                PRIMARY KEY (sha256, version)
            );
            CREATE TABLE IF NOT EXISTS entries (
                id TEXT PRIMARY KEY, sha256 TEXT NOT NULL REFERENCES assets(sha256),
                source TEXT NOT NULL, source_url TEXT NOT NULL, metadata TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE (sha256, source, source_url)
            );
            PRAGMA user_version=1;
        """)
        return connection

    def status(self) -> dict:
        return {
            "ok": True,
            **capabilities(),
            "directory": str(self.directory),
            "sources": ["local", "freesound", "jamendo"],
            "import_mode": "local_files_and_provider_downloads",
        }

    def import_file(
        self, path: Path, metadata: dict, provider_snapshot: dict | None = None
    ) -> dict:
        info = validate_metadata(metadata)
        if provider_snapshot is not None:
            info["provider_metadata"] = {
                **provider_snapshot,
                "retrieved_at": datetime.now(UTC).isoformat(),
            }
        path = path.expanduser().resolve()
        if not path.is_file() or path.suffix.lower() not in EXTENSIONS:
            raise ReferenceError("対応する音声ファイルのパスを指定してください")
        if not self._lock.acquire(blocking=False):
            raise ReferenceError("別の音源を解析中です。完了してから追加してください")
        staging = None
        new_asset = None
        try:
            audio_dir = self.directory / "audio"
            audio_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(audio_dir, 0o700)
            digest, size = hashlib.sha256(), 0
            with (
                path.open("rb") as source,
                NamedTemporaryFile(
                    dir=audio_dir, suffix=path.suffix.lower(), delete=False
                ) as target,
            ):
                staging = Path(target.name)
                while block := source.read(1024 * 1024):
                    size += len(block)
                    if size > MAX_BYTES:
                        raise ReferenceError("音源は256MB以内にしてください")
                    digest.update(block)
                    target.write(block)
            if not size:
                raise ReferenceError("空のファイルです")
            sha = digest.hexdigest()
            now = datetime.now(UTC).isoformat()
            with closing(self._connect()) as db:
                previous = db.execute(
                    "SELECT id FROM entries WHERE sha256=? AND source=? AND source_url=?",
                    (sha, info["source"], info["source_url"]),
                ).fetchone()
                analysis = db.execute(
                    "SELECT result FROM analyses WHERE sha256=? AND version=?",
                    (sha, VERSION),
                ).fetchone()
                result = (
                    json.loads(analysis[0]) if analysis else self._analyzer(staging)
                )
                asset = db.execute(
                    "SELECT filename FROM assets WHERE sha256=?", (sha,)
                ).fetchone()
                filename = asset[0] if asset else sha + path.suffix.lower()
                managed = audio_dir / filename
                if not managed.exists():
                    # Link atomically without overwriting a pre-existing stored asset.
                    os.link(staging, managed)
                    new_asset = managed
                with db:
                    db.execute(
                        "INSERT OR IGNORE INTO assets VALUES (?, ?, ?)",
                        (sha, filename, size),
                    )
                    db.execute(
                        "INSERT OR IGNORE INTO analyses VALUES (?, ?, ?, ?)",
                        (sha, VERSION, json.dumps(result, allow_nan=False), now),
                    )
                    entry_id = previous[0] if previous else uuid.uuid4().hex
                    if not previous:
                        db.execute(
                            "INSERT INTO entries VALUES (?, ?, ?, ?, ?, ?)",
                            (
                                entry_id,
                                sha,
                                info["source"],
                                info["source_url"],
                                json.dumps(info, ensure_ascii=False),
                                now,
                            ),
                        )
                new_asset = None
            return {
                "ok": True,
                "duplicate": previous is not None,
                "entry": self.get(entry_id),
            }
        finally:
            if staging:
                staging.unlink(missing_ok=True)
            if new_asset:
                new_asset.unlink(missing_ok=True)
            self._lock.release()

    def entries(self) -> list[dict]:
        with closing(self._connect()) as db:
            rows = db.execute(
                """SELECT e.*, a.result FROM entries e
                LEFT JOIN analyses a ON e.sha256=a.sha256 AND a.version=?
                ORDER BY e.created_at DESC, e.id""",
                (VERSION,),
            ).fetchall()
        return [
            {
                "id": row["id"],
                "sha256": row["sha256"],
                "created_at": row["created_at"],
                **json.loads(row["metadata"]),
                "analysis": json.loads(row["result"]) if row["result"] else None,
            }
            for row in rows
        ]

    def custom_genres(self) -> list[str]:
        return sorted(
            {
                genre
                for entry in self.entries()
                for genre in entry["genres"]
                if not find(canonical_genre(genre))
            }
        )

    def audio_path(self, entry_id: str) -> Path:
        entry = self.get(entry_id)
        with closing(self._connect()) as db:
            asset = db.execute(
                "SELECT filename FROM assets WHERE sha256=?", (entry["sha256"],)
            ).fetchone()
        if asset is None:
            raise ReferenceError("音源ファイルが見つかりません")
        filename = asset[0]
        if Path(filename).name != filename:
            raise ReferenceError("音源ファイルの保存情報が不正です")
        path = self.directory / "audio" / filename
        if not path.is_file() or path.is_symlink():
            raise ReferenceError("音源ファイルが見つかりません")
        return path

    def get(self, entry_id: str) -> dict:
        for entry in self.entries():
            if entry["id"] == entry_id:
                return entry
        raise ReferenceError("音源が見つかりません")

    def update(self, entry_id: str, metadata: dict) -> dict:
        current = self.get(entry_id)
        # Provenance identity stays stable; correct classifications and annotations.
        data = {
            **current,
            **metadata,
            "source": current["source"],
            "source_url": current["source_url"],
        }
        if isinstance(data.get("genres"), list):
            data["genres"] = ",".join(data["genres"])
        info = validate_metadata(data)
        if "provider_metadata" in current:
            info["provider_metadata"] = current["provider_metadata"]
        with closing(self._connect()) as db, db:
            db.execute(
                "UPDATE entries SET metadata=? WHERE id=?",
                (json.dumps(info, ensure_ascii=False), entry_id),
            )
        return {"ok": True, "entry": self.get(entry_id)}

    def compare(
        self, genre: str, kind: str, target_id: str = "", sample_role: str = ""
    ) -> dict:
        genre = canonical_genre(genre)
        if (
            not find(genre) and not _CUSTOM_GENRE.fullmatch(genre)
        ) or kind not in KINDS:
            raise ReferenceError("比較するジャンルと音源の種類を指定してください")
        if sample_role and (kind != "one_shot" or sample_role not in SAMPLE_ROLES):
            raise ReferenceError("ワンショットの役割を選択してください")
        target = self.get(target_id) if target_id else None
        if target and target["kind"] != kind:
            raise ReferenceError("比較対象と参考音源の種類をそろえてください")
        if target and genre not in {canonical_genre(name) for name in target["genres"]}:
            raise ReferenceError("比較対象にも同じジャンルを設定してください")
        if target and sample_role and target.get("sample_role", "") != sample_role:
            raise ReferenceError("比較対象と参考音源の役割をそろえてください")
        # Multiple source records for the same bytes must never inflate sample size.
        unique = {}
        for entry in self.entries():
            if (
                genre in {canonical_genre(name) for name in entry["genres"]}
                and entry["kind"] == kind
                and (not sample_role or entry.get("sample_role", "") == sample_role)
                and entry["analysis"]
            ):
                if target and entry["sha256"] == target["sha256"]:
                    continue
                unique.setdefault(entry["sha256"], entry)
        metrics = {}
        for name in (
            "integrated_lufs",
            "true_peak_dbtp",
            "rms_dbfs",
            "low_ratio",
            "mid_ratio",
            "high_ratio",
            "spectral_centroid_hz",
            "stereo_correlation",
        ):
            values = [
                entry["analysis"]["metrics"].get(name) for entry in unique.values()
            ]
            values = [v for v in values if v is not None and math.isfinite(v)]
            median = statistics.median(values) if values else None
            target_value = (
                target["analysis"]["metrics"].get(name)
                if target and target["analysis"]
                else None
            )
            metrics[name] = {
                "n": len(values),
                "median": median,
                "min": min(values) if values else None,
                "max": max(values) if values else None,
                "target": target_value,
                "delta": target_value - median
                if target_value is not None and median is not None
                else None,
            }
        bpms = [
            e["confirmed_bpm"]
            for e in unique.values()
            if e["confirmed_bpm"] is not None
        ]
        findings = []
        if target:
            observed = (
                ("integrated_lufs", "音量", "LUFS", 1.0),
                ("true_peak_dbtp", "True Peak", "dBTP", 1.0),
                ("low_ratio", "低域の比率", "ポイント", 100.0),
                ("mid_ratio", "中域の比率", "ポイント", 100.0),
                ("high_ratio", "高域の比率", "ポイント", 100.0),
                ("spectral_centroid_hz", "スペクトル重心", "Hz", 1.0),
                ("stereo_correlation", "左右相関", "", 1.0),
            )
            for key, label, unit, scale in observed:
                metric = metrics[key]
                if metric["n"] < 2 or metric["delta"] is None:
                    continue
                delta = metric["delta"] * scale
                precision = (
                    2
                    if key
                    in {"integrated_lufs", "true_peak_dbtp", "stereo_correlation"}
                    else 1
                )
                findings.append(
                    {
                        "metric": key,
                        "sample_count": metric["n"],
                        "difference": round(delta, 2),
                        "unit": unit,
                        "text": f"{label}: 対象と参考中央値の差は{delta:+.{precision}f}{unit}",
                    }
                )
        return {
            "ok": True,
            "genre": genre,
            "kind": kind,
            "sample_role": sample_role,
            "count": len(unique),
            "findings": findings,
            "metrics": metrics,
            "confirmed_bpm": {
                "n": len(bpms),
                "median": statistics.median(bpms) if bpms else None,
            },
            "warning": "登録音源だけの分布です。ジャンル全体の標準・品質評価ではありません",
            "generation_applied": False,
        }
