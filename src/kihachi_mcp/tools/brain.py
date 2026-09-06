from typing import Any

from kihachi_mcp.services import SongService

_song_service = SongService()


def generate_songspec(
    genre: str,
    tempo: int,
    key: str,
    length_minutes: float,
    mood: str,
) -> dict[str, Any]:
    """Generate a SongSpec via SongService and return it as JSON."""
    return _song_service.generate_songspec(
        genre=genre,
        tempo=tempo,
        key=key,
        length_minutes=length_minutes,
        mood=mood,
    ).to_dict()
