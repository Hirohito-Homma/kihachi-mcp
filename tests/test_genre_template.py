import pytest

from kihachi_mcp.models import Arrangement, GenreTemplate


def _dub_techno() -> GenreTemplate:
    return GenreTemplate(
        name="Dub Techno",
        default_bpm=110,
        default_key="D#m",
        tracks=["Kick", "Bass", "Dub Chords", "Pad", "FX"],
        arrangement=[
            Arrangement(name="Intro", start_bar=1, length_bars=32),
            Arrangement(name="Build", start_bar=33, length_bars=32),
            Arrangement(name="Drop", start_bar=65, length_bars=64),
            Arrangement(name="Breakdown", start_bar=129, length_bars=16),
            Arrangement(name="Outro", start_bar=145, length_bars=16),
        ],
        mood="Deep",
    )


def test_genre_template_fields() -> None:
    template = _dub_techno()

    assert template.name == "Dub Techno"
    assert template.default_bpm == 110
    assert template.default_key == "D#m"
    assert template.tracks == ["Kick", "Bass", "Dub Chords", "Pad", "FX"]
    assert template.mood == "Deep"
    assert template.arrangement == [
        Arrangement(name="Intro", start_bar=1, length_bars=32),
        Arrangement(name="Build", start_bar=33, length_bars=32),
        Arrangement(name="Drop", start_bar=65, length_bars=64),
        Arrangement(name="Breakdown", start_bar=129, length_bars=16),
        Arrangement(name="Outro", start_bar=145, length_bars=16),
    ]
    assert all(isinstance(section, Arrangement) for section in template.arrangement)


def test_genre_template_to_dict() -> None:
    assert _dub_techno().to_dict() == {
        "name": "Dub Techno",
        "default_bpm": 110,
        "default_key": "D#m",
        "tracks": ["Kick", "Bass", "Dub Chords", "Pad", "FX"],
        "arrangement": [
            {"name": "Intro", "start_bar": 1, "length_bars": 32},
            {"name": "Build", "start_bar": 33, "length_bars": 32},
            {"name": "Drop", "start_bar": 65, "length_bars": 64},
            {"name": "Breakdown", "start_bar": 129, "length_bars": 16},
            {"name": "Outro", "start_bar": 145, "length_bars": 16},
        ],
        "mood": "Deep",
    }


def test_genre_template_from_dict_roundtrip() -> None:
    data = _dub_techno().to_dict()

    assert GenreTemplate.from_dict(data).to_dict() == data


def test_genre_template_from_dict_uses_arrangement_model() -> None:
    template = GenreTemplate.from_dict(
        {
            "name": "Tech House",
            "default_bpm": 124,
            "default_key": "Am",
            "tracks": ["Kick"],
            "arrangement": [{"name": "Groove", "start_bar": 1, "length_bars": 64}],
            "mood": "Driving",
        }
    )

    assert template.arrangement == [
        Arrangement(name="Groove", start_bar=1, length_bars=64)
    ]


def test_genre_template_is_frozen() -> None:
    template = _dub_techno()

    with pytest.raises(AttributeError):
        template.default_bpm = 120  # type: ignore[misc]
