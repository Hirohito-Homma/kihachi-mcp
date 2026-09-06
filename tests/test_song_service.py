import pytest

from kihachi_mcp.knowledge import UnknownGenreError
from kihachi_mcp.models import SongSpec
from kihachi_mcp.services import SongService

_DUB_TRACKS = ["Kick", "Bass", "Dub Chords", "Pad", "FX"]


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
        tracks=_DUB_TRACKS,
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
        "tracks": _DUB_TRACKS,
    }


def test_default_tracks_come_from_genre_yaml() -> None:
    assert SongService().default_tracks("dub techno") == _DUB_TRACKS


def test_default_tracks_unknown_genre_raises() -> None:
    with pytest.raises(UnknownGenreError):
        SongService().default_tracks("unknown-genre")


def test_validate_songspec_accepts_generated_spec() -> None:
    spec = SongService().generate_songspec(
        genre="dub techno",
        tempo=110,
        key="D#m",
        length_minutes=5,
        mood="hypnotic",
    )

    assert SongService().validate_songspec(spec) == []


def test_generate_matches_generate_songspec() -> None:
    service = SongService()
    kwargs = {
        "genre": "dub techno",
        "tempo": 110,
        "key": "D#m",
        "length_minutes": 5,
        "mood": "hypnotic",
    }

    assert service.generate(**kwargs) == service.generate_songspec(**kwargs)


def test_validate_aliases_validate_songspec() -> None:
    spec = SongService().generate(
        genre="dub techno",
        tempo=110,
        key="D#m",
        length_minutes=5,
        mood="hypnotic",
    )

    assert SongService().validate(spec) == SongService().validate_songspec(spec)


def test_bars_from_minutes_and_minutes_from_bars() -> None:
    service = SongService()

    assert service.bars_from_minutes(5) == 160
    assert service.minutes_from_bars(160) == 5
    assert service.bars_from_minutes(0) == 1


def test_estimate_duration_uses_bar_grid() -> None:
    spec = SongService().generate(
        genre="dub techno",
        tempo=110,
        key="D#m",
        length_minutes=5,
        mood="hypnotic",
    )

    assert SongService().estimate_duration(spec) == 5


def test_default_arrangement_uses_genre_template() -> None:
    sections = SongService().default_arrangement("dub techno")

    assert [section.name for section in sections] == [
        "Intro",
        "Build",
        "Drop",
        "Breakdown",
        "Outro",
    ]
    assert sum(section.length_bars for section in sections) == 160
    assert sections[0].start_bar == 1


def test_default_arrangement_scales_to_requested_bars() -> None:
    sections = SongService().default_arrangement("dub techno", bars=80)

    assert sum(section.length_bars for section in sections) == 80


def test_to_dict_and_from_dict_roundtrip() -> None:
    service = SongService()
    spec = service.generate(
        genre="dub techno",
        tempo=110,
        key="D#m",
        length_minutes=5,
        mood="hypnotic",
    )

    assert service.from_dict(service.to_dict(spec)) == spec


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


def test_generate_uses_tech_house_tracks() -> None:
    spec = SongService().generate(
        genre="tech house",
        tempo=124,
        key="Am",
        length_minutes=5,
        mood="driving",
    )

    assert spec.tracks == ["Kick", "Bass", "Hats", "Stab", "FX"]


def test_generate_falls_back_to_genre_defaults() -> None:
    spec = SongService().generate(
        genre="dub techno",
        tempo=0,
        key="  ",
        length_minutes=5,
        mood="hypnotic",
    )

    assert spec.tempo == 110
    assert spec.key == "D#m"


def test_generate_can_omit_knowledge_backed_inputs() -> None:
    spec = SongService().generate(genre="dub techno", length_minutes=5)

    assert spec.tempo == 110
    assert spec.key == "D#m"
    assert spec.tracks == _DUB_TRACKS
