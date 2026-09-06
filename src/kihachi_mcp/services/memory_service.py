import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from kihachi_mcp.models import MemoryEntry


class MemoryService:
    """Store song decisions in memory, optionally backed by a JSON file."""

    def __init__(self, storage_path: str | Path | None = None) -> None:
        configured_path = storage_path or os.getenv("KIHACHI_MEMORY_PATH")
        self._storage_path = (
            Path(configured_path).expanduser() if configured_path else None
        )
        self._entries = self._load()

    def remember(
        self,
        songspec: dict[str, Any],
        review: dict[str, Any] | None = None,
    ) -> MemoryEntry:
        entry = MemoryEntry(
            genre=str(songspec.get("genre", "")),
            songspec=dict(songspec),
            review=dict(review) if review is not None else None,
        )
        self._entries.append(entry)
        self._save()
        return entry

    def search(self, genre: str | None = None) -> list[MemoryEntry]:
        """Return remembered entries, optionally filtered by genre."""
        if not genre:
            return list(self._entries)
        query = genre.strip().casefold()
        return [entry for entry in self._entries if entry.genre.casefold() == query]

    def _load(self) -> list[MemoryEntry]:
        if self._storage_path is None or not self._storage_path.exists():
            return []
        data = json.loads(self._storage_path.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            raise TypeError("Memory storage must contain a JSON list")
        return [
            MemoryEntry(
                genre=str(item.get("genre", "")),
                songspec=dict(item.get("songspec") or {}),
                review=dict(item["review"]) if item.get("review") is not None else None,
            )
            for item in data
            if isinstance(item, dict)
        ]

    def _save(self) -> None:
        if self._storage_path is None:
            return
        self._storage_path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=self._storage_path.parent,
            prefix=f".{self._storage_path.name}.",
            delete=False,
        ) as temporary:
            json.dump(
                [entry.to_dict() for entry in self._entries],
                temporary,
                ensure_ascii=False,
                indent=2,
            )
            temporary.write("\n")
            temporary_path = Path(temporary.name)
        temporary_path.replace(self._storage_path)
