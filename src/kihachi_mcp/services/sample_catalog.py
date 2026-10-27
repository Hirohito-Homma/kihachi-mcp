"""Local sample index and truthful, deterministic search; audio is never copied."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import threading
from contextlib import closing
from datetime import UTC, datetime, timedelta
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
            "learning_status": "TEXT NOT NULL DEFAULT ''",
            "learning_claimed_at": "TEXT",
            "learned_at": "TEXT",
            "learning_features": "TEXT NOT NULL DEFAULT '{}'",
        }
        for name, sql_type in additions.items():
            if name not in columns:
                db.execute(f"ALTER TABLE files ADD COLUMN {name} {sql_type}")
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS files_sample_id ON files(sample_id)")
        self._backfill_filename_metadata(db)
        return db

    @staticmethod
    def _backfill_filename_metadata(db: sqlite3.Connection) -> None:
        """Upgrade old filename-only indexes without reading or copying audio."""
        root_row = db.execute(
            "SELECT value FROM settings WHERE key='root'"
        ).fetchone()
        if not root_row:
            return
        root = Path(root_row[0])
        rows = db.execute(
            """SELECT relative_path, bpm, musical_key, sample_kind, tags
               FROM files WHERE sample_id IS NULL"""
        ).fetchall()
        if not rows:
            return
        updates = []
        for row in rows:
            bpm, musical_key, tags = _filename_metadata(row["relative_path"])
            updates.append(
                (
                    _sample_id(root, row["relative_path"]),
                    row["bpm"] if row["bpm"] is not None else bpm,
                    row["musical_key"] or musical_key,
                    row["sample_kind"] or _kind(row["relative_path"]),
                    row["tags"] or " ".join(tags),
                    row["relative_path"],
                )
            )
        with db:
            db.executemany(
                """UPDATE files SET sample_id=?, bpm=?, musical_key=?,
                   sample_kind=?, tags=? WHERE relative_path=?""",
                updates,
            )

    def status(self) -> dict:
        with closing(self._connect()) as db:
            settings = {
                r[0]: r[1] for r in db.execute("SELECT key,value FROM settings")
            }
            count, learned, failed = db.execute(
                """SELECT COUNT(*),
                          SUM(CASE WHEN learning_status='learned' THEN 1 ELSE 0 END),
                          SUM(CASE WHEN learning_status='failed' THEN 1 ELSE 0 END)
                   FROM files"""
            ).fetchone()
        return {
            "ok": True,
            "root": settings.get("root", ""),
            "count": count,
            "learning_completed": learned or 0,
            "learning_failed": failed or 0,
            "indexed_at": settings.get("indexed_at", ""),
            "audio_copied": False,
        }

    def audio_path(self, sample_id: str) -> Path:
        """Resolve only a currently indexed sample; never accept a caller path."""
        if not re.fullmatch(r"[a-f0-9]{24}", sample_id):
            raise ReferenceError("サンプルが見つかりません")
        status = self.status()
        if not status["root"]:
            raise ReferenceError("サンプルが見つかりません")
        with closing(self._connect()) as db:
            row = db.execute(
                "SELECT relative_path FROM files WHERE sample_id=?", (sample_id,)
            ).fetchone()
        if not row:
            raise ReferenceError("サンプルが見つかりません")
        root = Path(status["root"]).resolve()
        unresolved = root / row["relative_path"]
        if unresolved.is_symlink():
            raise ReferenceError("サンプルが見つかりません")
        path = unresolved.resolve()
        if not path.is_relative_to(root) or path.suffix.lower() not in EXTENSIONS:
            raise ReferenceError("サンプルが見つかりません")
        try:
            size = path.stat().st_size
        except OSError:
            raise ReferenceError("サンプルが見つかりません") from None
        if not path.is_file() or not 0 < size <= MAX_BYTES:
            raise ReferenceError("サンプルが見つかりません")
        return path

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
            learned_by_sample = {}
            if self.path.exists():
                with closing(self._connect()) as db:
                    learned_by_sample = {
                        row["sample_id"]: {
                            "learning_status": row["learning_status"],
                            "learned_at": row["learned_at"],
                            "learning_features": row["learning_features"],
                        }
                        for row in db.execute(
                            "SELECT sample_id,learning_status,learned_at,learning_features FROM files"
                        )
                        if row["sample_id"]
                    }
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
                    sample_id = _sample_id(root, relative)
                    learned = learned_by_sample.get(sample_id, {})
                    rows.append(
                        (
                            relative,
                            name,
                            _role(relative),
                            size,
                            sample_id,
                            bpm,
                            musical_key,
                            _duration(path),
                            _kind(relative),
                            None,
                            None,
                            None,
                            None,
                            " ".join(tags),
                            learned.get("learning_status", ""),
                            learned.get("learned_at"),
                            learned.get("learning_features", "{}"),
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
                        ,learning_status,learned_at,learning_features
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
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

    def next_learning_sample(self, *, claim: bool = False) -> dict | None:
        """Return the next unfinished sample, optionally claiming it atomically."""
        status = self.status()
        if not status["root"]:
            return None
        root = Path(status["root"]).resolve()
        with closing(self._connect()) as db:
            if claim:
                db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                """SELECT sample_id,relative_path,name,role,bpm,musical_key,duration,tags
                   FROM files WHERE learning_status=''
                   ORDER BY relative_path LIMIT 1"""
            ).fetchone()
            if row and claim:
                claimed_at = datetime.now(UTC).isoformat()
                changed = db.execute(
                    """UPDATE files SET learning_status='processing', learning_claimed_at=?
                       WHERE sample_id=? AND learning_status=''""",
                    (claimed_at, row["sample_id"]),
                ).rowcount
                if changed != 1:
                    db.rollback()
                    return None
                db.commit()
        if not row:
            return None
        try:
            path = self.audio_path(row["sample_id"])
        except ReferenceError:
            self.record_learning(row["sample_id"], {}, error="索引音源ファイルが見つからないか、取得条件外です")
            return self.next_learning_sample(claim=claim)
        duration = row["duration"]
        if duration is None:
            duration = _duration(path)
            if duration is None:
                self.record_learning(row["sample_id"], {}, error="音源の長さを読み取れません")
                return self.next_learning_sample(claim=claim)
            with closing(self._connect()) as db, db:
                db.execute(
                    "UPDATE files SET duration=? WHERE sample_id=?",
                    (duration, row["sample_id"]),
                )
        if not 0.5 <= float(duration) <= 1800:
            self.record_learning(row["sample_id"], {}, error="自動解析対象は0.5〜1800秒の音源です")
            return self.next_learning_sample(claim=claim)
        return {
            "sample_id": row["sample_id"],
            "name": row["name"],
            "role": row["role"],
            "bpm": row["bpm"],
            "key": row["musical_key"],
            "duration": duration,
            "tags": row["tags"].split(),
            "path": str(path),
        }

    def record_learning(self, sample_id: str, features: dict, *, error: str = "") -> None:
        """Persist compact audio-analysis findings without copying the sample."""
        if not re.fullmatch(r"[a-f0-9]{24}", sample_id):
            raise ReferenceError("サンプルが見つかりません")
        status = "failed" if error else "learned"
        payload = {key: value for key, value in features.items() if value is None or isinstance(value, (str, int, float, bool))}
        with closing(self._connect()) as db, db:
            changed = db.execute(
                """UPDATE files SET learning_status=?, learning_claimed_at=NULL, learned_at=?, learning_features=?
                   WHERE sample_id=?""",
                (status, datetime.now(UTC).isoformat(), json.dumps(payload, ensure_ascii=False), sample_id),
            ).rowcount
        if not changed:
            raise ReferenceError("サンプルが見つかりません")

    def release_abandoned_learning_claims(self, *, older_than_seconds: int = 1800) -> int:
        """Return claims left by a crashed worker to the unfinished queue."""
        cutoff = datetime.now(UTC) - timedelta(seconds=max(1, older_than_seconds))
        with closing(self._connect()) as db, db:
            changed = db.execute(
                """UPDATE files SET learning_status='', learning_claimed_at=NULL
                   WHERE learning_status='processing'
                   AND (learning_claimed_at IS NULL OR learning_claimed_at < ?)""",
                (cutoff.isoformat(),),
            ).rowcount
        return int(changed)

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
        if role in {"kick", "hat", "snare", "clap", "percussion"}:
            conditions.append("(sample_kind IS NULL OR sample_kind != 'loop')")
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

        def lexical_score(row) -> float:
            haystack = f"{row['relative_path']} {row['tags']}".lower()
            return sum(2.0 for term in terms if term in haystack)

        def score(row) -> tuple[float, str]:
            value = lexical_score(row)
            if tempo is not None and row["bpm"] is not None:
                value += max(0.0, 4.0 - abs(float(row["bpm"]) - float(tempo)) / 2)
            if requested_key and row["musical_key"]:
                value += 5.0 if str(row["musical_key"]).lower() == requested_key else 0.0
            return value, row["relative_path"]

        ranked = sorted(rows, key=lambda row: (-score(row)[0], score(row)[1]))
        if query_lower and not terms:
            ranked = []
        elif terms:
            ranked = [row for row in ranked if lexical_score(row) > 0]
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
