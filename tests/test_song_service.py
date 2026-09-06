from models.songspec import SongSpec
from services.song_service import SongService


def test_generate_songspec_returns_expected_songspec() -> None:
    """SongService keeps the VS2 SongSpec contract."""
    spec = SongService().generate_songspec(
        genre="dub techno",
        tempo=110,
        key="D#m",
        length_minutes=5,
        mood="hypnotic",
    )

    assert spec == SongSpec(
        genre="dub techno",
        tempo=110,
        key="D#m",
        length_minutes=5,
        bars=160,
        tracks=["Kick", "Bass", "Dub Chords", "Lead", "FX"],
    )


def test_generate_songspec_json_matches_public_api() -> None:
    """Serialized SongSpec matches the public MCP JSON shape."""
    spec = SongService().generate_songspec(
        genre="dub techno",
        tempo=110,
        key="D#m",
        length_minutes=5,
        mood="hypnotic",
    )

    assert spec.to_dict() == {
        "genre": "dub techno",
        "tempo": 110,
        "key": "D#m",
        "length_minutes": 5,
        "bars": 160,
        "tracks": ["Kick", "Bass", "Dub Chords", "Lead", "FX"],
    }
