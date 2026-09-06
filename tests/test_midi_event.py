from kihachi_mcp.models import MidiEvent


def test_midi_event_round_trips_as_json_data() -> None:
    event = MidiEvent("Kick", 1, 1, 36, 100)

    assert MidiEvent.from_dict(event.to_dict()) == event


def test_midi_event_accepts_valid_project_range() -> None:
    event = MidiEvent("Bass", 8, 4, 40, 96)

    assert event.validate(project_bars=16) == []


def test_midi_event_reports_invalid_values_and_range() -> None:
    event = MidiEvent("", 0, 0, 128, 0)

    assert event.validate(project_bars=8) == [
        "track_name must not be empty",
        "start_bar must be at least 1",
        "duration_bars must be at least 1",
        "pitch must be between 0 and 127",
        "velocity must be between 1 and 127",
    ]


def test_midi_event_reports_project_overflow() -> None:
    event = MidiEvent("Pad", 8, 2, 60, 80)

    assert event.validate(project_bars=8) == ["event is outside the project bars"]
