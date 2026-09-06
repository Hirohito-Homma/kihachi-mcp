import pytest

from kihachi_mcp.models import Arrangement


def test_arrangement_fields() -> None:
    section = Arrangement(name="Intro", start_bar=1, length_bars=16)

    assert section.name == "Intro"
    assert section.start_bar == 1
    assert section.length_bars == 16


def test_arrangement_to_dict() -> None:
    section = Arrangement(name="Drop", start_bar=33, length_bars=32)

    assert section.to_dict() == {
        "name": "Drop",
        "start_bar": 33,
        "length_bars": 32,
    }


def test_arrangement_from_dict_roundtrip() -> None:
    data = {"name": "Outro", "start_bar": 129, "length_bars": 16}

    assert Arrangement.from_dict(data).to_dict() == data


def test_arrangement_from_dict_defaults_length_bars() -> None:
    section = Arrangement.from_dict({"name": "Break"})

    assert section.start_bar == 0
    assert section.length_bars == 1


def test_arrangement_is_frozen() -> None:
    section = Arrangement(name="Verse", start_bar=17, length_bars=16)

    with pytest.raises(AttributeError):
        section.name = "Chorus"  # type: ignore[misc]
