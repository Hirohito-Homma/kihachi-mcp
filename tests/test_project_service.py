from models.project_plan import ProjectPlan
from models.songspec import SongSpec
from models.track import TrackSpec
from services.project_service import ProjectService


def _sample_songspec() -> SongSpec:
    return SongSpec(
        genre="dub techno",
        tempo=110,
        key="D#m",
        length_minutes=5,
        bars=160,
        tracks=["Kick", "Bass", "Dub Chords", "Lead", "FX"],
    )


def test_create_project_from_songspec_returns_expected_plan() -> None:
    """ProjectService keeps the VS2 ProjectPlan contract."""
    plan = ProjectService().create_project_from_songspec(_sample_songspec())

    assert plan == ProjectPlan(
        project_name="Untitled",
        genre="dub techno",
        tempo=110,
        key="D#m",
        length_minutes=5,
        bars=160,
        tracks=[
            TrackSpec(name="Kick", type="MIDI", color="Red"),
            TrackSpec(name="Bass", type="MIDI", color="Blue"),
            TrackSpec(name="Dub Chords", type="MIDI", color="Purple"),
            TrackSpec(name="Lead", type="MIDI", color="Green"),
            TrackSpec(name="FX", type="Audio", color="Gray"),
        ],
    )


def test_create_project_from_songspec_json_matches_public_api() -> None:
    """Serialized ProjectPlan matches the public MCP JSON shape."""
    plan = ProjectService().create_project_from_songspec(_sample_songspec())

    assert plan.to_dict() == {
        "project_name": "Untitled",
        "genre": "dub techno",
        "tempo": 110,
        "key": "D#m",
        "length_minutes": 5,
        "bars": 160,
        "tracks": [
            {"name": "Kick", "type": "MIDI", "color": "Red"},
            {"name": "Bass", "type": "MIDI", "color": "Blue"},
            {"name": "Dub Chords", "type": "MIDI", "color": "Purple"},
            {"name": "Lead", "type": "MIDI", "color": "Green"},
            {"name": "FX", "type": "Audio", "color": "Gray"},
        ],
    }
