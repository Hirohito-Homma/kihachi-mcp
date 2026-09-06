from typing import Any

from kihachi_mcp.api import Brain

_brain = Brain()


def generate_songspec(
    genre: str,
    tempo: int,
    key: str,
    length_minutes: float,
    mood: str,
) -> dict[str, Any]:
    """Generate a SongSpec via Brain and return it as JSON."""
    return _brain.generate_song(
        genre=genre,
        tempo=tempo,
        key=key,
        length_minutes=length_minutes,
        mood=mood,
    ).to_dict()
