from kihachi_mcp.models import Arrangement, ProjectPlan, TrackSpec
from kihachi_mcp.services import AbletonService


def _project_plan() -> ProjectPlan:
    return ProjectPlan(
        project_name="Untitled",
        genre="dub techno",
        tempo=110,
        key="D#m",
        length_minutes=5,
        bars=160,
        tracks=[
            TrackSpec(name="Kick", type="MIDI", color="Red"),
            TrackSpec(name="FX", type="Audio", color="Gray"),
        ],
        arrangement=[
            Arrangement(name="Intro", start_bar=1, length_bars=32),
            Arrangement(name="Drop", start_bar=33, length_bars=128),
        ],
    )


def test_ableton_service_maps_project_plan() -> None:
    result = AbletonService().create_plan(_project_plan())

    assert result.to_dict() == {
        "set_name": "Untitled",
        "genre": "dub techno",
        "tempo": 110,
        "key": "D#m",
        "length_minutes": 5,
        "bars": 160,
        "tracks": [
            {"name": "Kick", "track_type": "MIDI", "color": "Red"},
            {"name": "FX", "track_type": "Audio", "color": "Gray"},
        ],
        "locators": [
            {"name": "Intro", "start_bar": 1, "length_bars": 32},
            {"name": "Drop", "start_bar": 33, "length_bars": 128},
        ],
    }


def test_ableton_service_accepts_project_plan_json() -> None:
    source = _project_plan().to_dict(include_arrangement=True)

    result = AbletonService().create_plan(source)

    assert result.set_name == "Untitled"
    assert result.tracks[0].track_type == "MIDI"
    assert result.locators[-1].name == "Drop"


def test_ableton_service_handles_legacy_project_json_without_arrangement() -> None:
    source = _project_plan().to_dict()

    result = AbletonService().create_plan(source)

    assert result.locators == []
