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

    def search(
        self,
        genre: str | None = None,
        approved_only: bool = False,
        min_score: float = 0.0,
        limit: int | None = None,
        query: str | None = None,
        sort_by: str = "recent",
    ) -> list[MemoryEntry]:
        """Filter memory and optionally rank entries by review score."""
        genre_query = genre.strip().casefold() if genre else None
        text_query = query.strip().casefold() if query else None
        if sort_by not in {"recent", "score"}:
            raise ValueError("sort_by must be 'recent' or 'score'")

        def matches(entry: MemoryEntry) -> bool:
            songspec = entry.songspec
            searchable = " ".join(
                [
                    entry.genre,
                    str(songspec.get("key", "")),
                    " ".join(str(track) for track in songspec.get("tracks", [])),
                    " ".join(
                        str(comment)
                        for comment in (entry.review or {}).get("comments", [])
                    ),
                ]
            ).casefold()
            return (
                (genre_query is None or genre_query in entry.genre.casefold())
                and (text_query is None or text_query in searchable)
                and (
                    not approved_only
                    or (entry.review is not None and bool(entry.review.get("approved")))
                )
                and (
                    entry.review is None
                    or float(entry.review.get("score", 0)) >= min_score
                )
            )

        results = [entry for entry in self._entries if matches(entry)]
        if sort_by == "score":
            results.sort(
                key=lambda entry: float((entry.review or {}).get("score", 0)),
                reverse=True,
            )
        return results if limit is None else results[: max(0, limit)]

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
