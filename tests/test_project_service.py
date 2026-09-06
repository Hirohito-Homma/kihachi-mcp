from kihachi_mcp.models import Arrangement, ProjectPlan, SongSpec, TrackSpec
from kihachi_mcp.services import ProjectService


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


def test_create_project_from_songspec_accepts_json_dict() -> None:
    plan = ProjectService().create_project_from_songspec(_sample_songspec().to_dict())

    assert plan.project_name == "Untitled"
    assert plan.tracks[0].name == "Kick"


def test_project_name_and_output_directory() -> None:
    spec = _sample_songspec()
    service = ProjectService()

    assert service.project_name(spec) == "Untitled"
    assert service.output_directory(spec) == "projects/Untitled"


def test_created_at_is_iso8601() -> None:
    stamp = ProjectService().created_at()

    assert "T" in stamp
    assert stamp.endswith(("+00:00", "Z")) or "+00:00" in stamp


def test_metadata_includes_output_and_timestamp() -> None:
    spec = _sample_songspec()
    metadata = ProjectService().metadata(spec)

    assert metadata["project_name"] == "Untitled"
    assert metadata["output_directory"] == "projects/Untitled"
    assert metadata["genre"] == "dub techno"
    assert "created_at" in metadata


def test_prepare_project_metadata() -> None:
    metadata = ProjectService().prepare_project_metadata(_sample_songspec())

    assert metadata == {
        "project_name": "Untitled",
        "genre": "dub techno",
        "tempo": 110,
        "key": "D#m",
        "length_minutes": 5,
        "bars": 160,
    }


def test_create_project_accepts_arrangement() -> None:
    arrangement = [Arrangement(name="Intro", start_bar=1, length_bars=160)]

    plan = ProjectService().create_project_from_songspec(
        _sample_songspec(),
        arrangement=arrangement,
    )

    assert plan.arrangement == arrangement
