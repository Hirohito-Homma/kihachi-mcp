import json
from pathlib import Path

from kihachi_mcp.models import AudioRenderRequest
from kihachi_mcp.services import GoogleLyriaAdapter


def request() -> AudioRenderRequest:
    return AudioRenderRequest(
        "Demo", "dub techno", "Bass", 110, "D#m", 1, 32, "deep bass"
    )


def test_adapter_blocks_without_credentials(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    result = GoogleLyriaAdapter(api_key="").render(request(), tmp_path / "bass.mp3")

    assert result.status == "blocked"
    assert "GEMINI_API_KEY" in result.error
    assert result.artifact_path == ""


def test_adapter_returns_verified_receipt_without_provider_secrets(
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, str, dict[str, str], bytes | None]] = []

    def fake_http(method: str, url: str, headers: dict[str, str], body: bytes | None):
        calls.append((method, url, headers, body))
        return (
            200,
            b'{"id":"interaction-1","steps":[{"content":[{"type":"audio","data":"d2F2LWJ5dGVz"}]}]}',
            {},
        )

    output = tmp_path / "bass.mp3"
    result = GoogleLyriaAdapter(api_key="secret", http_call=fake_http).render(
        request(), output
    )

    assert result.status == "succeeded"
    assert result.task_id == "interaction-1"
    assert result.artifact_path == str(output)
    assert result.sha256
    assert output.read_bytes() == b"wav-bytes"
    assert "secret" not in str(result.to_dict())
    assert calls[0][2]["x-goog-api-key"] == "secret"
    payload = json.loads(calls[0][3])
    assert "Genre: dub techno" in payload["input"]
    assert "tempo: 110 BPM" in payload["input"]
    assert "key: D#m" in payload["input"]


def test_adapter_rejects_missing_audio(tmp_path: Path) -> None:
    def fake_http(method: str, url: str, headers: dict[str, str], body: bytes | None):
        return 200, b'{"id":"interaction-1","steps":[]}', {}

    result = GoogleLyriaAdapter(api_key="secret", http_call=fake_http).render(
        request(), tmp_path / "bass.mp3"
    )

    assert result.status == "failed"
    assert result.error == "Lyria returned no audio data"


def test_adapter_blocks_quota_response(tmp_path: Path) -> None:
    def fake_http(method: str, url: str, headers: dict[str, str], body: bytes | None):
        return 429, b'{"error":"quota"}', {}

    result = GoogleLyriaAdapter(api_key="secret", http_call=fake_http).render(
        request(), tmp_path / "bass.mp3"
    )

    assert result.status == "blocked"
    assert result.error == "Google Lyria quota or rate limit reached"


def test_adapter_blocks_authentication_response(tmp_path: Path) -> None:
    def fake_http(method: str, url: str, headers: dict[str, str], body: bytes | None):
        return 403, b'{"error":"forbidden"}', {}

    result = GoogleLyriaAdapter(api_key="secret", http_call=fake_http).render(
        request(), tmp_path / "bass.mp3"
    )

    assert result.status == "blocked"
    assert result.error == "Google Lyria authentication was rejected"


def test_adapter_rejects_invalid_audio_encoding(tmp_path: Path) -> None:
    def fake_http(method: str, url: str, headers: dict[str, str], body: bytes | None):
        return (
            200,
            b'{"id":"interaction-1","steps":[{"content":[{"type":"audio","data":"not-base64"}]}]}',
            {},
        )

    output = tmp_path / "bass.mp3"
    result = GoogleLyriaAdapter(api_key="secret", http_call=fake_http).render(
        request(), output
    )

    assert result.status == "failed"
    assert not output.exists()
