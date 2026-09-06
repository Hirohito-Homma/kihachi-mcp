from kihachi_mcp.models import SongSpec
from kihachi_mcp.services import SongService


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


def test_validate_songspec_accepts_generated_spec() -> None:
    spec = SongService().generate_songspec(
        genre="dub techno",
        tempo=110,
        key="D#m",
        length_minutes=5,
        mood="hypnotic",
    )

    assert SongService().validate_songspec(spec) == []


def test_validate_songspec_reports_invalid_fields() -> None:
    spec = SongSpec(
        genre="",
        tempo=0,
        key="",
        length_minutes=0,
        bars=0,
        tracks=[],
    )

    comments = SongService().validate_songspec(spec)

    assert "genre is required" in comments
    assert "tempo must be positive" in comments
    assert "key is required" in comments
    assert "length_minutes must be positive" in comments
    assert "bars must be at least 1" in comments
    assert "tracks must not be empty" in comments
