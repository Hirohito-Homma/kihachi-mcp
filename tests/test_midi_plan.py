from kihachi_mcp.models import Arrangement, MidiPlan, ProjectPlan, TrackSpec
from kihachi_mcp.services import AbletonService


def test_midi_plan_maps_midi_tracks_to_arrangement_sections() -> None:
    plan = ProjectPlan(
        project_name="Demo",
        genre="dub techno",
        tempo=110,
        key="D#m",
        length_minutes=1,
        bars=32,
        tracks=[
            TrackSpec("Kick", "MIDI", "Red"),
            TrackSpec("FX", "Audio", "Gray"),
        ],
        arrangement=[Arrangement("Intro", 1, 16), Arrangement("Drop", 17, 16)],
    )

    assert AbletonService().create_midi_plan(plan).to_dict() == {
        "tempo": 110,
        "bars": 32,
        "clips": [
            {"track_name": "Kick", "section_name": "Intro", "start_bar": 1, "length_bars": 16},
            {"track_name": "Kick", "section_name": "Drop", "start_bar": 17, "length_bars": 16},
        ],
        "events": [],
    }


def test_midi_plan_round_trips_events() -> None:
    source = {
        "tempo": 110,
        "bars": 16,
        "clips": [],
        "events": [
            {
                "track_name": "Kick",
                "start_bar": 1,
                "duration_bars": 1,
                "pitch": 36,
                "velocity": 100,
            }
        ],
    }

    assert MidiPlan.from_dict(source).to_dict() == source
