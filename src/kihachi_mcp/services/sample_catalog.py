"""Filename-only index of a user-selected sample folder; audio is never copied."""

from __future__ import annotations

import os
import re
import sqlite3
import threading
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from kihachi_mcp.services.reference_analysis import EXTENSIONS, ReferenceError
from kihachi_mcp.services.reference_library import MAX_BYTES, default_library_dir

MAX_FILES = 150_000
_ROLES = {
    "kick": re.compile(r"(?<![a-z])(kick|kicks|bd)(?![a-z])", re.IGNORECASE),
    "hats": re.compile(r"(?<![a-z])(hat|hats|hihat|hi-hat|hh)(?![a-z])", re.IGNORECASE),
    "snare": re.compile(r"(?<![a-z])(snare|snares|sd)(?![a-z])", re.IGNORECASE),
    "clap": re.compile(r"(?<![a-z])(clap|claps)(?![a-z])", re.IGNORECASE),
}


def _role(path: str) -> str:
    for text in (Path(path).name, path):
        for role, pattern in _ROLES.items():
            if pattern.search(text):
                return role
    return "other"


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
                    rows.append((relative, name, _role(relative), size))
                    if len(rows) > MAX_FILES:
                        raise ReferenceError(
                            "音源が15万件を超えました。小さいフォルダを指定してください"
                        )
            with closing(self._connect()) as db, db:
                db.execute("DELETE FROM files")
                db.executemany("INSERT INTO files VALUES (?,?,?,?)", rows)
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

    def search(self, role: str, query: str = "", limit: int = 50) -> dict:
        if role not in {"kick", "hats", "snare", "clap", "other", "all"}:
            raise ReferenceError("音源の役割を選んでください")
        if not isinstance(query, str) or len(query) > 80 or not 1 <= limit <= 100:
            raise ReferenceError("検索語は80文字以内にしてください")
        status = self.status()
        if not status["root"]:
            return {"ok": True, "results": [], "count": 0, "root": ""}
        root = Path(status["root"])
        conditions, params = [], []
        if role != "all":
            conditions.append("role=?")
            params.append(role)
        if query.strip():
            conditions.append("lower(relative_path) LIKE ? ESCAPE '\\'")
            safe = (
                query.strip()
                .lower()
                .replace("\\", "\\\\")
                .replace("%", "\\%")
                .replace("_", "\\_")
            )
            params.append("%" + safe + "%")
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        with closing(self._connect()) as db:
            count = db.execute("SELECT COUNT(*) FROM files" + where, params).fetchone()[
                0
            ]
            rows = db.execute(
                "SELECT relative_path,name,role,bytes FROM files"
                + where
                + " ORDER BY relative_path LIMIT ?",
                [*params, limit],
            ).fetchall()
        return {
            "ok": True,
            "root": str(root),
            "count": count,
            "results": [
                {
                    "relative_path": r["relative_path"],
                    "name": r["name"],
                    "role": r["role"],
                    "bytes": r["bytes"],
                    "path": str(root / r["relative_path"]),
                }
                for r in rows
            ],
            "audio_copied": False,
        }
