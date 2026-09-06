import pytest

from kihachi_mcp.models import TrackSpec


def test_track_spec_fields() -> None:
    track = TrackSpec(name="Kick", type="MIDI", color="Red")

    assert track.name == "Kick"
    assert track.type == "MIDI"
    assert track.color == "Red"


def test_track_spec_to_dict() -> None:
    track = TrackSpec(name="FX", type="Audio", color="Gray")

    assert track.to_dict() == {"name": "FX", "type": "Audio", "color": "Gray"}


def test_track_spec_from_dict_roundtrip() -> None:
    data = {"name": "Bass", "type": "MIDI", "color": "Blue"}

    assert TrackSpec.from_dict(data).to_dict() == data


def test_track_spec_is_frozen() -> None:
    track = TrackSpec(name="Lead", type="MIDI", color="Green")

    with pytest.raises(AttributeError):
        track.name = "Pad"  # type: ignore[misc]
