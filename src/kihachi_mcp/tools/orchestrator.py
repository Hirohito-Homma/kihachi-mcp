from typing import Any

from kihachi_mcp.api import Brain

_brain = Brain()


def orchestrate_song(
    genre: str,
    tempo: int | None = None,
    key: str | None = None,
    length_minutes: float = 5.0,
    mood: str | None = None,
) -> dict[str, Any]:
    """Run generation, review, memory, and project preparation."""
    return _brain.orchestrate_song(
        genre=genre,
        tempo=tempo,
        key=key,
        length_minutes=length_minutes,
        mood=mood,
    ).to_dict()
