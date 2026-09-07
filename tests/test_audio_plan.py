from kihachi_mcp.models import (
    Arrangement,
    AudioRenderResult,
    ProjectPlan,
    TrackSpec,
)
from kihachi_mcp.services import AudioService


def plan() -> ProjectPlan:
    return ProjectPlan(
        "Demo", "dub techno", 110, "D#m", 5, 160, [TrackSpec("Bass", "Audio", "Blue")]
    )


def test_audio_service_builds_provider_neutral_request() -> None:
    result = AudioService().create_request(plan(), "Bass", " deep bass ", " noise ")
    assert result.project_name == "Demo"
    assert result.genre == "dub techno"
    assert result.target_track == "Bass"
    assert result.tempo == 110
    assert result.key == "D#m"
    assert result.prompt == "deep bass"
    assert result.negative_prompt == "noise"
    assert result.mood == "Deep"
    assert result.tracks == ("Kick", "Bass", "Dub Chords", "Pad", "FX")
    assert result.arrangement[0].name == "Intro"
    assert result.arrangement[0].start_bar == 1
    assert "api_key" not in result.to_dict()
    assert "GEMINI_API_KEY" not in result.to_dict()


def test_audio_service_keeps_explicit_arrangement() -> None:
    sections = [Arrangement("Drop", 1, 8)]
    project = ProjectPlan(
        "Demo",
        "dub techno",
        110,
        "D#m",
        1,
        8,
        [TrackSpec("Bass", "Audio", "Blue")],
        sections,
    )

    result = AudioService().create_request(project, "Bass")

    assert result.arrangement == tuple(sections)


def test_audio_service_accepts_project_json() -> None:
    result = AudioService().create_request(plan().to_dict(), "Bass")
    assert result.target_track == "Bass"


def test_audio_service_rejects_unknown_target() -> None:
    try:
        AudioService().create_request(plan(), "Kick")
    except ValueError as exc:
        assert str(exc) == "target_track not found: Kick"
    else:
        raise AssertionError("expected ValueError")


def test_audio_result_is_an_artifact_receipt() -> None:
    result = AudioRenderResult(
        "succeeded", "task-1", "/tmp/bass.wav", 12.0, 44100, 2, "abc"
    )
    assert result.to_dict()["sha256"] == "abc"
