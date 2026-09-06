from typing import Any

from kihachi_mcp.models import MemoryEntry


class MemoryService:
    """Store and retrieve song decisions for the lifetime of the MCP process."""

    def __init__(self) -> None:
        self._entries: list[MemoryEntry] = []

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
        return entry

    def search(self, genre: str | None = None) -> list[MemoryEntry]:
        """Return remembered entries, optionally filtered by genre."""
        if not genre:
            return list(self._entries)
        query = genre.strip().casefold()
        return [entry for entry in self._entries if entry.genre.casefold() == query]
