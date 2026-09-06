import pytest

from kihachi_mcp.models import SongSpec


def test_songspec_fields() -> None:
    spec = SongSpec(
        genre="dub techno",
        tempo=110,
        key="D#m",
        length_minutes=5,
        bars=160,
        tracks=["Kick", "Bass"],
    )

    assert spec.genre == "dub techno"
    assert spec.tempo == 110
    assert spec.key == "D#m"
    assert spec.length_minutes == 5
    assert spec.bars == 160
    assert spec.tracks == ["Kick", "Bass"]


def test_songspec_to_dict_matches_public_json() -> None:
    spec = SongSpec(
        genre="dub techno",
        tempo=110,
        key="D#m",
        length_minutes=5,
        bars=160,
        tracks=["Kick", "Bass", "Dub Chords", "Lead", "FX"],
    )

    assert spec.to_dict() == {
        "genre": "dub techno",
        "tempo": 110,
        "key": "D#m",
        "length_minutes": 5,
        "bars": 160,
        "tracks": ["Kick", "Bass", "Dub Chords", "Lead", "FX"],
    }


def test_songspec_from_dict_computes_bars_when_missing() -> None:
    spec = SongSpec.from_dict(
        {
            "genre": "dub techno",
            "tempo": 110,
            "key": "D#m",
            "length_minutes": 5,
            "tracks": ["Kick"],
        }
    )

    assert spec.bars == 160


def test_songspec_from_dict_roundtrip() -> None:
    data = {
        "genre": "house",
        "tempo": 124,
        "key": "Am",
        "length_minutes": 4.0,
        "bars": 128,
        "tracks": ["Kick", "Bass"],
    }

    assert SongSpec.from_dict(data).to_dict() == data


def test_songspec_is_frozen() -> None:
    spec = SongSpec.from_dict({"tempo": 110, "length_minutes": 5})

    with pytest.raises(AttributeError):
        spec.tempo = 120  # type: ignore[misc]
