from typing import Any

from kihachi_mcp.api import Brain

_brain = Brain()


def remember_song(
    songspec: dict[str, Any],
    review: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Remember a SongSpec and optional ReviewResult."""
    return _brain.remember_song(songspec, review).to_dict()


def search_memory(
    genre: str | None = None,
    approved_only: bool = False,
    min_score: float = 0.0,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Search memory by genre and optional review quality filters."""
    return [
        entry.to_dict()
        for entry in _brain.search_memory(genre, approved_only, min_score, limit)
    ]
