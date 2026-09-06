from pathlib import Path

from kihachi_mcp.models import AudioRenderRequest
from kihachi_mcp.services import AceStepAdapter


def request() -> AudioRenderRequest:
    return AudioRenderRequest(
        "Demo", "dub techno", "Bass", 110, "D#m", 1, 32, "deep bass"
    )


def test_adapter_blocks_without_credentials(tmp_path: Path) -> None:
    result = AceStepAdapter(api_key="").render(request(), tmp_path / "bass.wav")

    assert result.status == "blocked"
    assert "API_KEY" in result.error
    assert result.artifact_path == ""


def test_adapter_returns_verified_receipt_without_provider_secrets(
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, str, dict[str, str], bytes | None]] = []

    def fake_http(method: str, url: str, headers: dict[str, str], body: bytes | None):
        calls.append((method, url, headers, body))
        if url.endswith("release_task"):
            return 200, b'{"task_id":"task-1"}', {}
        if url.endswith("query_result"):
            return (
                200,
                b'{"status":"succeeded","audio_url":"http://127.0.0.1:8001/bass.wav","duration_seconds":60}',
                {},
            )
        return 200, b"wav-bytes", {"content-type": "audio/wav"}

    output = tmp_path / "bass.wav"
    result = AceStepAdapter(api_key="secret", http_call=fake_http).render(
        request(), output
    )

    assert result.status == "succeeded"
    assert result.task_id == "task-1"
    assert result.artifact_path == str(output)
    assert result.sha256
    assert output.read_bytes() == b"wav-bytes"
    assert "secret" not in str(result.to_dict())
    assert calls[0][2]["Authorization"] == "Bearer secret"


def test_adapter_rejects_unexpected_duration(tmp_path: Path) -> None:
    def fake_http(method: str, url: str, headers: dict[str, str], body: bytes | None):
        if url.endswith("release_task"):
            return 200, b'{"task_id":"task-1"}', {}
        if url.endswith("query_result"):
            return (
                200,
                b'{"status":"succeeded","audio_url":"http://127.0.0.1:8001/bass.wav","duration_seconds":12}',
                {},
            )
        return 200, b"wav-bytes", {}

    result = AceStepAdapter(api_key="secret", http_call=fake_http).render(
        request(), tmp_path / "bass.wav"
    )

    assert result.status == "failed"
    assert result.error == "audio duration does not match request"
