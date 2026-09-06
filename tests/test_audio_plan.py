from kihachi_mcp.models import (
    AudioRenderRequest,
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
    assert result == AudioRenderRequest(
        "Demo", "dub techno", "Bass", 110, "D#m", 5, 160, "deep bass", "noise"
    )
    assert "api_key" not in result.to_dict()


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
