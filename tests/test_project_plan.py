import pytest

from kihachi_mcp.models import Arrangement, ProjectPlan, TrackSpec


def test_project_plan_fields() -> None:
    plan = ProjectPlan(
        project_name="Untitled",
        genre="dub techno",
        tempo=110,
        key="D#m",
        length_minutes=5,
        bars=160,
        tracks=[TrackSpec(name="Kick", type="MIDI", color="Red")],
    )

    assert plan.project_name == "Untitled"
    assert plan.tracks[0] == TrackSpec(name="Kick", type="MIDI", color="Red")


def test_project_plan_to_dict_matches_public_json() -> None:
    plan = ProjectPlan(
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
    )

    assert plan.to_dict() == {
        "project_name": "Untitled",
        "genre": "dub techno",
        "tempo": 110,
        "key": "D#m",
        "length_minutes": 5,
        "bars": 160,
        "tracks": [
            {"name": "Kick", "type": "MIDI", "color": "Red"},
            {"name": "FX", "type": "Audio", "color": "Gray"},
        ],
    }


def test_project_plan_from_dict_roundtrip() -> None:
    data = {
        "project_name": "Untitled",
        "genre": "dub techno",
        "tempo": 110,
        "key": "D#m",
        "length_minutes": 5.0,
        "bars": 160,
        "tracks": [{"name": "Bass", "type": "MIDI", "color": "Blue"}],
    }

    assert ProjectPlan.from_dict(data).to_dict() == data


def test_project_plan_is_frozen() -> None:
    plan = ProjectPlan.from_dict({"project_name": "Untitled", "bars": 16})

    with pytest.raises(AttributeError):
        plan.project_name = "New Song"  # type: ignore[misc]


def test_project_plan_arrangement_serialization_is_opt_in() -> None:
    data = {
        "project_name": "Untitled",
        "bars": 16,
        "arrangement": [{"name": "Intro", "start_bar": 1, "length_bars": 16}],
    }

    plan = ProjectPlan.from_dict(data)

    assert plan.arrangement == [Arrangement(name="Intro", start_bar=1, length_bars=16)]
    assert "arrangement" not in plan.to_dict()
    assert plan.to_dict(include_arrangement=True)["arrangement"] == [
        {
            "name": "Intro",
            "start_bar": 1,
            "length_bars": 16,
        }
    ]
