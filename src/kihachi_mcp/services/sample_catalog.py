"""Local sample index and truthful, deterministic search; audio is never copied."""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import threading
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

import soundfile as sf

from kihachi_mcp.services.reference_analysis import EXTENSIONS, ReferenceError
from kihachi_mcp.services.reference_library import MAX_BYTES, default_library_dir

MAX_FILES = 150_000
_ROLES = {
    "kick": re.compile(r"(?<![a-z])(kick|kicks|bd)(?![a-z])", re.IGNORECASE),
    "hat": re.compile(r"(?<![a-z])(hat|hats|hihat|hi-hat|hh)(?![a-z])", re.IGNORECASE),
    "snare": re.compile(r"(?<![a-z])(snare|snares|sd)(?![a-z])", re.IGNORECASE),
    "clap": re.compile(r"(?<![a-z])(clap|claps)(?![a-z])", re.IGNORECASE),
    "percussion": re.compile(r"(?<![a-z])(perc|percussion|conga|bongo|rim|shaker)(?![a-z])", re.IGNORECASE),
    "bass": re.compile(r"(?<![a-z])(bass|sub)(?![a-z])", re.IGNORECASE),
    "vocal": re.compile(r"(?<![a-z])(vocal|vox|voice|acapella)(?![a-z])", re.IGNORECASE),
    "fx": re.compile(r"(?<![a-z])(fx|sfx|impact|riser|sweep|noise)(?![a-z])", re.IGNORECASE),
    "loop": re.compile(r"(?<![a-z])(loop|toploop|top-loop)(?![a-z])", re.IGNORECASE),
}

_QUERY_TERMS = {
    "太い": ("fat", "thick", "heavy", "deep", "sub"),
    "重い": ("heavy", "deep", "hard", "sub", "fat"),
    "暗い": ("dark", "deep", "dub", "warm"),
    "短い": ("short", "tight", "dry"),
    "パンチ": ("punch", "punchy", "hard", "attack"),
    "ダブ": ("dub", "dubwise", "echo"),
    "トップ": ("top", "toploop", "top-loop"),
}
_BPM = re.compile(r"(?<!\d)([5-9]\d|1\d\d|2[0-4]\d)\s*(?:bpm)?(?!\d)", re.IGNORECASE)
_KEY = re.compile(r"(?<![a-z])([a-g](?:#|b)?)[ _-]?(maj(?:or)?|min(?:or)?|m)(?![a-z])", re.IGNORECASE)


def _role(path: str) -> str:
    for text in (Path(path).name, path):
        for role, pattern in _ROLES.items():
            if pattern.search(text):
                return role
    return "other"


def _kind(path: str) -> str | None:
    text = path.lower()
    if re.search(r"(?<![a-z])(loop|toploop|top-loop)(?![a-z])", text):
        return "loop"
    if re.search(r"(?<![a-z])(one[ _-]?shot|oneshot|single[ _-]?hit)(?![a-z])", text):
        return "one_shot"
    return None


def _filename_metadata(path: str) -> tuple[float | None, str | None, list[str]]:
    bpm_match, key_match = _BPM.search(path), _KEY.search(path)
    bpm = float(bpm_match.group(1)) if bpm_match else None
    key = None
    if key_match:
        tonic = key_match.group(1).upper().replace("B", "b")
        mode_text = key_match.group(2).lower()
        mode = "minor" if mode_text in {"m", "min", "minor"} else "major"
        key = f"{tonic} {mode}"
    tags = sorted(
        {
            token.lower()
            for token in re.findall(r"[A-Za-z][A-Za-z0-9#-]{1,31}", path)
            if token.lower() not in {"wav", "aif", "aiff", "flac", "mp3"}
        }
    )[:40]
    return bpm, key, tags


def _duration(path: Path) -> float | None:
    try:
        value = float(sf.info(path).duration)
    except (OSError, RuntimeError, ValueError):
        return None
    return round(value, 5) if value > 0 else None


def _sample_id(root: Path, relative: str) -> str:
    return hashlib.sha256(str(root / relative).encode("utf-8")).hexdigest()[:24]


class SampleCatalog:
    def __init__(self, directory: Path | None = None):
        self.directory = directory or default_library_dir()
        self.path = self.directory / "sample_catalog.sqlite3"
        self._lock = threading.Lock()

    def _connect(self):
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.directory, 0o700)
        db = sqlite3.connect(self.path, timeout=30)
        os.chmod(self.path, 0o600)
        db.row_factory = sqlite3.Row
        db.executescript("""
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS files (
                relative_path TEXT PRIMARY KEY, name TEXT NOT NULL,
                role TEXT NOT NULL, bytes INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS files_role_name ON files(role, name);
        """)
        columns = {row[1] for row in db.execute("PRAGMA table_info(files)")}
        additions = {
            "sample_id": "TEXT",
            "bpm": "REAL",
            "musical_key": "TEXT",
            "duration": "REAL",
            "sample_kind": "TEXT",
            "loudness": "REAL",
            "energy": "REAL",
            "brightness": "REAL",
            "transient": "REAL",
            "tags": "TEXT NOT NULL DEFAULT ''",
        }
        for name, sql_type in additions.items():
            if name not in columns:
                db.execute(f"ALTER TABLE files ADD COLUMN {name} {sql_type}")
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS files_sample_id ON files(sample_id)")
        return db

    def status(self) -> dict:
        with closing(self._connect()) as db:
            settings = {
                r[0]: r[1] for r in db.execute("SELECT key,value FROM settings")
            }
            count = db.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        return {
            "ok": True,
            "root": settings.get("root", ""),
            "count": count,
            "indexed_at": settings.get("indexed_at", ""),
            "audio_copied": False,
        }

    def index(self, directory: str) -> dict:
        if not isinstance(directory, str) or len(directory) > 1000:
            raise ReferenceError("サンプル集の絶対パスを指定してください")
        root = Path(directory).expanduser()
        if not root.is_absolute() or not root.is_dir():
            raise ReferenceError("サンプル集の絶対パスを指定してください")
        root = root.resolve()
        if not self._lock.acquire(blocking=False):
            raise ReferenceError("サンプル一覧を作成中です")
        try:
            rows = []
            for base, dirs, files in os.walk(root, followlinks=False):
                dirs[:] = [d for d in dirs if not (Path(base) / d).is_symlink()]
                for name in files:
                    path = Path(base) / name
                    if path.suffix.lower() not in EXTENSIONS or path.is_symlink():
                        continue
                    try:
                        size = path.stat().st_size
                    except OSError:
                        continue
                    if not 0 < size <= MAX_BYTES:
                        continue
                    relative = str(path.relative_to(root))
                    bpm, musical_key, tags = _filename_metadata(relative)
                    rows.append(
                        (
                            relative,
                            name,
                            _role(relative),
                            size,
                            _sample_id(root, relative),
                            bpm,
                            musical_key,
                            _duration(path),
                            _kind(relative),
                            None,
                            None,
                            None,
                            None,
                            " ".join(tags),
                        )
                    )
                    if len(rows) > MAX_FILES:
                        raise ReferenceError(
                            "音源が15万件を超えました。小さいフォルダを指定してください"
                        )
            with closing(self._connect()) as db, db:
                db.execute("DELETE FROM files")
                db.executemany(
                    """INSERT INTO files (
                        relative_path,name,role,bytes,sample_id,bpm,musical_key,
                        duration,sample_kind,loudness,energy,brightness,transient,tags
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    rows,
                )
                db.execute(
                    "INSERT OR REPLACE INTO settings VALUES ('root',?)", (str(root),)
                )
                db.execute(
                    "INSERT OR REPLACE INTO settings VALUES ('indexed_at',?)",
                    (datetime.now(UTC).isoformat(),),
                )
            return self.status()
        finally:
            self._lock.release()

    def search(
        self,
        role: str,
        query: str = "",
        limit: int = 50,
        *,
        tempo: float | None = None,
        key: str = "",
    ) -> dict:
        role = "hat" if role == "hats" else role
        if role not in {*_ROLES, "snare", "clap", "other", "all"}:
            raise ReferenceError("音源の役割を選んでください")
        if (
            not isinstance(query, str)
            or len(query) > 80
            or not 1 <= limit <= 100
            or (tempo is not None and not 20 <= float(tempo) <= 400)
            or not isinstance(key, str)
            or len(key) > 32
        ):
            raise ReferenceError("検索語は80文字以内にしてください")
        status = self.status()
        if not status["root"]:
            return {"ok": True, "results": [], "count": 0, "root": ""}
        root = Path(status["root"])
        conditions, params = [], []
        if role != "all":
            conditions.append("role=?")
            params.append(role)
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        with closing(self._connect()) as db:
            rows = db.execute(
                "SELECT * FROM files"
                + where
                + " ORDER BY relative_path",
                params,
            ).fetchall()
        query_lower = query.strip().lower()
        terms = set(re.findall(r"[a-z0-9#-]+", query_lower))
        for japanese, synonyms in _QUERY_TERMS.items():
            if japanese in query_lower:
                terms.update(synonyms)
        requested_key = key.strip().lower()

        def score(row) -> tuple[float, str]:
            haystack = f"{row['relative_path']} {row['tags']}".lower()
            value = sum(2.0 for term in terms if term in haystack)
            if tempo is not None and row["bpm"] is not None:
                value += max(0.0, 4.0 - abs(float(row["bpm"]) - float(tempo)) / 2)
            if requested_key and row["musical_key"]:
                value += 5.0 if str(row["musical_key"]).lower() == requested_key else 0.0
            return value, row["relative_path"]

        ranked = sorted(rows, key=lambda row: (-score(row)[0], score(row)[1]))
        if query_lower and not terms:
            ranked = []
        elif terms:
            ranked = [row for row in ranked if score(row)[0] > 0]
        count = len(ranked)
        ranked = ranked[:limit]
        return {
            "ok": True,
            "root": str(root),
            "count": count,
            "results": [
                {
                    "relative_path": r["relative_path"],
                    "name": r["name"],
                    "sample_id": r["sample_id"],
                    "role": r["role"],
                    "category": r["role"],
                    "bytes": r["bytes"],
                    "path": str(root / r["relative_path"]),
                    "bpm": r["bpm"],
                    "key": r["musical_key"],
                    "duration": r["duration"],
                    "sample_kind": r["sample_kind"],
                    "loudness": r["loudness"],
                    "energy": r["energy"],
                    "brightness": r["brightness"],
                    "transient": r["transient"],
                    "tags": r["tags"].split(),
                }
                for r in ranked
            ],
            "audio_copied": False,
        }
