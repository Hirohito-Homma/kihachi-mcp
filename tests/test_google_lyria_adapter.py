import base64
import json
from email.message import EmailMessage
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError, URLError

from kihachi_mcp.models import Arrangement, AudioRenderRequest
from kihachi_mcp.services import GoogleLyriaAdapter
from kihachi_mcp.services.google_lyria_adapter import (
    _DEFAULT_BASE_URL,
    _DEFAULT_MODEL,
    looks_like_mp3,
)

SECRET = "secret-key-xyz"
MIN_MP3 = b"ID3\x04\x00\x00\x00\x00\x00\x00" + b"\xff\xfb\x90\x00" + bytes(64)
MIN_MP3_B64 = base64.b64encode(MIN_MP3).decode()


def request() -> AudioRenderRequest:
    return AudioRenderRequest(
        "Demo",
        "dub techno",
        "Bass",
        110,
        "D#m",
        1,
        32,
        "deep bass",
        arrangement=(Arrangement("Intro", 1, 16), Arrangement("Groove", 17, 16)),
        mood="Deep",
        tracks=("Kick", "Bass"),
    )


def _audio_body(audio_b64: str = MIN_MP3_B64, task_id: str = "interaction-1") -> bytes:
    return json.dumps(
        {
            "id": task_id,
            "steps": [
                {
                    "type": "model_output",
                    "content": [{"type": "audio", "data": audio_b64}],
                }
            ],
        }
    ).encode()


def test_default_model_and_endpoint(monkeypatch) -> None:
    monkeypatch.delenv("LYRIA_MODEL", raising=False)
    monkeypatch.delenv("LYRIA_BASE_URL", raising=False)
    adapter = GoogleLyriaAdapter(api_key=SECRET)

    assert adapter.model == _DEFAULT_MODEL
    assert adapter.model == "lyria-3.5"
    assert adapter.base_url == _DEFAULT_BASE_URL


def test_adapter_blocks_without_credentials(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    result = GoogleLyriaAdapter(api_key="").render(request(), tmp_path / "bass.mp3")

    assert result.status == "blocked"
    assert "GEMINI_API_KEY" in result.error
    assert result.artifact_path == ""


def test_adapter_sends_audio_response_format_and_prompt(tmp_path: Path) -> None:
    calls: list[tuple[str, str, dict[str, str], bytes | None]] = []

    def fake_http(method: str, url: str, headers: dict[str, str], body: bytes | None):
        calls.append((method, url, headers, body))
        return 200, _audio_body(), {}

    output = tmp_path / "bass.mp3"
    result = GoogleLyriaAdapter(api_key=SECRET, http_call=fake_http).render(
        request(), output
    )
    payload = json.loads(calls[0][3] or b"{}")

    assert result.status == "succeeded"
    assert calls[0][0] == "POST"
    assert calls[0][1] == _DEFAULT_BASE_URL
    assert calls[0][2]["x-goog-api-key"] == SECRET
    assert payload["model"] == "lyria-3.5"
    assert payload["response_format"] == {"type": "audio"}
    assert "Tempo: 110 BPM" in payload["input"]
    assert "Key: D# minor" in payload["input"]
    assert "Dub Techno" in payload["input"]
    assert "1 minute" in payload["input"]
    assert "[0:00-" in payload["input"]
    assert "Intro" in payload["input"]


def test_adapter_returns_verified_receipt_without_provider_secrets(
    tmp_path: Path,
) -> None:
    def fake_http(method: str, url: str, headers: dict[str, str], body: bytes | None):
        return 200, _audio_body(), {}

    output = tmp_path / "bass.mp3"
    result = GoogleLyriaAdapter(api_key=SECRET, http_call=fake_http).render(
        request(), output
    )

    assert result.status == "succeeded"
    assert result.task_id == "interaction-1"
    assert result.artifact_path == str(output)
    assert result.duration_seconds == 0
    assert result.sample_rate == 44100
    assert result.channels == 2
    assert result.sha256
    assert output.read_bytes() == MIN_MP3
    assert SECRET not in str(result.to_dict())
    assert "x-goog-api-key" not in str(result.to_dict())
    assert "GEMINI_API_KEY" not in str(result.to_dict())


def test_adapter_accepts_output_audio_fallback(tmp_path: Path) -> None:
    def fake_http(method: str, url: str, headers: dict[str, str], body: bytes | None):
        return (
            200,
            json.dumps({"id": "legacy-1", "output_audio": {"data": MIN_MP3_B64}}).encode(),
            {},
        )

    result = GoogleLyriaAdapter(api_key=SECRET, http_call=fake_http).render(
        request(), tmp_path / "legacy.mp3"
    )

    assert result.status == "succeeded"
    assert result.task_id == "legacy-1"


def test_adapter_rejects_missing_audio(tmp_path: Path) -> None:
    def fake_http(method: str, url: str, headers: dict[str, str], body: bytes | None):
        return 200, b'{"id":"interaction-1","steps":[]}', {}

    result = GoogleLyriaAdapter(api_key=SECRET, http_call=fake_http).render(
        request(), tmp_path / "bass.mp3"
    )

    assert result.status == "failed"
    assert result.error == "Lyria returned no audio data"
    assert not (tmp_path / "bass.mp3").exists()


def test_adapter_rejects_text_only_response(tmp_path: Path) -> None:
    def fake_http(method: str, url: str, headers: dict[str, str], body: bytes | None):
        return (
            200,
            b'{"id":"interaction-1","output_text":"verse","steps":[{"type":"model_output","content":[{"type":"text","text":"verse"}]}]}',
            {},
        )

    result = GoogleLyriaAdapter(api_key=SECRET, http_call=fake_http).render(
        request(), tmp_path / "bass.mp3"
    )

    assert result.status == "failed"
    assert result.error == "Lyria returned no audio data"


def test_adapter_rejects_invalid_json(tmp_path: Path) -> None:
    def fake_http(method: str, url: str, headers: dict[str, str], body: bytes | None):
        return 200, b"not-json", {}

    result = GoogleLyriaAdapter(api_key=SECRET, http_call=fake_http).render(
        request(), tmp_path / "bass.mp3"
    )

    assert result.status == "failed"
    assert result.error == "Google Lyria returned invalid JSON"


def test_adapter_blocks_quota_response(tmp_path: Path) -> None:
    def fake_http(method: str, url: str, headers: dict[str, str], body: bytes | None):
        return 429, b'{"error":"quota"}', {}

    result = GoogleLyriaAdapter(api_key=SECRET, http_call=fake_http).render(
        request(), tmp_path / "bass.mp3"
    )

    assert result.status == "blocked"
    assert result.error == "Google Lyria quota or rate limit reached"


def test_adapter_blocks_authentication_response(tmp_path: Path) -> None:
    def fake_http(method: str, url: str, headers: dict[str, str], body: bytes | None):
        return 403, json.dumps({"error": SECRET}).encode(), {}

    result = GoogleLyriaAdapter(api_key=SECRET, http_call=fake_http).render(
        request(), tmp_path / "bass.mp3"
    )

    assert result.status == "blocked"
    assert result.error == "Google Lyria authentication was rejected"
    assert SECRET not in result.error


def test_adapter_classifies_client_and_provider_failures(tmp_path: Path) -> None:
    cases = {
        400: "Google Lyria request was invalid",
        404: "Google Lyria endpoint or model was not found",
        500: "Google Lyria provider error",
    }
    for status, message in cases.items():
        def fake_http(
            method: str,
            url: str,
            headers: dict[str, str],
            body: bytes | None,
            captured=status,
        ):
            return captured, json.dumps({"error": SECRET}).encode(), {}

        result = GoogleLyriaAdapter(api_key=SECRET, http_call=fake_http).render(
            request(), tmp_path / f"{status}.mp3"
        )
        assert result.status == "failed"
        assert result.error == message
        assert SECRET not in result.error


def test_real_http_path_classifies_status_codes(monkeypatch, tmp_path: Path) -> None:
    def fake_urlopen(*args, **kwargs):
        raise HTTPError(
            _DEFAULT_BASE_URL,
            401,
            "Unauthorized",
            EmailMessage(),
            BytesIO(json.dumps({"error": SECRET}).encode()),
        )

    monkeypatch.setattr(
        "kihachi_mcp.services.google_lyria_adapter.urlopen", fake_urlopen
    )
    result = GoogleLyriaAdapter(api_key=SECRET).render(request(), tmp_path / "bass.mp3")

    assert result.status == "blocked"
    assert result.error == "Google Lyria authentication was rejected"
    assert SECRET not in result.error
    assert "x-goog-api-key" not in result.error


def test_real_http_path_classifies_timeout(monkeypatch, tmp_path: Path) -> None:
    def fake_urlopen(*args, **kwargs):
        raise URLError(TimeoutError("timed out"))

    monkeypatch.setattr(
        "kihachi_mcp.services.google_lyria_adapter.urlopen", fake_urlopen
    )
    result = GoogleLyriaAdapter(api_key=SECRET).render(request(), tmp_path / "bass.mp3")

    assert result.status == "failed"
    assert result.error == "Google Lyria request timed out"
    assert SECRET not in result.error


def test_real_http_path_classifies_network_failure(monkeypatch, tmp_path: Path) -> None:
    def fake_urlopen(*args, **kwargs):
        raise URLError(OSError("nodename nor servname provided"))

    monkeypatch.setattr(
        "kihachi_mcp.services.google_lyria_adapter.urlopen", fake_urlopen
    )
    result = GoogleLyriaAdapter(api_key=SECRET).render(request(), tmp_path / "bass.mp3")

    assert result.status == "failed"
    assert result.error == "Google Lyria network request failed"


def test_adapter_rejects_invalid_audio_encoding(tmp_path: Path) -> None:
    def fake_http(method: str, url: str, headers: dict[str, str], body: bytes | None):
        return (
            200,
            b'{"id":"interaction-1","steps":[{"type":"model_output","content":[{"type":"audio","data":"not-base64"}]}]}',
            {},
        )

    output = tmp_path / "bass.mp3"
    result = GoogleLyriaAdapter(api_key=SECRET, http_call=fake_http).render(
        request(), output
    )

    assert result.status == "failed"
    assert result.error == "Lyria returned invalid audio encoding"
    assert not output.exists()


def test_adapter_rejects_empty_audio(tmp_path: Path) -> None:
    def fake_http(method: str, url: str, headers: dict[str, str], body: bytes | None):
        return (
            200,
            b'{"id":"interaction-1","steps":[{"type":"model_output","content":[{"type":"audio","data":""}]}]}',
            {},
        )

    output = tmp_path / "bass.mp3"
    result = GoogleLyriaAdapter(api_key=SECRET, http_call=fake_http).render(
        request(), output
    )

    assert result.status == "failed"
    assert result.error == "Lyria returned no audio data"
    assert not output.exists()


def test_adapter_rejects_invalid_artifact(tmp_path: Path) -> None:
    def fake_http(method: str, url: str, headers: dict[str, str], body: bytes | None):
        junk = base64.b64encode(b"wav-bytes").decode()
        return (
            200,
            json.dumps(
                {
                    "id": "interaction-1",
                    "steps": [
                        {"type": "model_output", "content": [{"type": "audio", "data": junk}]}
                    ],
                }
            ).encode(),
            {},
        )

    output = tmp_path / "bass.mp3"
    result = GoogleLyriaAdapter(api_key=SECRET, http_call=fake_http).render(
        request(), output
    )

    assert result.status == "failed"
    assert result.error == "Lyria returned an invalid audio artifact"
    assert not output.exists()


def test_looks_like_mp3_accepts_id3_and_frame_sync() -> None:
    assert looks_like_mp3(MIN_MP3)
    assert looks_like_mp3(b"\xff\xfb\x90\x00" + bytes(16))
    assert not looks_like_mp3(b"wav-bytes")
    assert not looks_like_mp3(b"")
