"""Comprehensive tests for GeminiAIProvider (mocking API responses)."""

import json
from unittest.mock import MagicMock, patch

import pytest

from kihachi_mcp.services.ai_provider import GeminiAIProvider, InterpretationError


class MockHTTPResponse:
    def __init__(self, status: int, data: bytes):
        self.status = status
        self._data = data

    def read(self) -> bytes:
        return self._data


def test_gemini_provider_missing_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)

    provider = GeminiAIProvider()
    h = provider.health()
    assert h["ok"] is False
    assert h["state"] == "api_key_missing"
    assert "GEMINI_API_KEY" in h["message"]

    conn_res = provider.test_connection()
    assert conn_res["ok"] is False
    assert "設定されていません" in conn_res["message"]

    with pytest.raises(InterpretationError) as exc_info:
        provider.generate("hello")
    assert "設定されていません" in str(exc_info.value)


def test_gemini_provider_success_structured(monkeypatch):
    secret_key = "secret_mock_key_xyz_123"
    monkeypatch.setenv("GEMINI_API_KEY", secret_key)

    mock_gemini_body = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {
                            "text": json.dumps({
                                "theory_interpretation": {"summary": "Great groove"},
                                "generation_rules": {"bpm_target": 128.0},
                            })
                        }
                    ]
                },
                "finishReason": "STOP",
            }
        ],
        "usageMetadata": {
            "promptTokenCount": 80,
            "candidatesTokenCount": 40,
        },
    }
    raw_bytes = json.dumps(mock_gemini_body).encode("utf-8")

    mock_conn = MagicMock()
    mock_conn.getresponse.return_value = MockHTTPResponse(200, raw_bytes)

    with patch("kihachi_mcp.services.ai_provider.HTTPSConnection", return_value=mock_conn):
        provider = GeminiAIProvider(model="gemini-2.5-flash")
        res = provider.generate_structured("prompt", schema={"type": "object"})

        assert res["theory_interpretation"]["summary"] == "Great groove"
        assert res["generation_rules"]["bpm_target"] == 128.0
        assert provider.last_usage["input_tokens"] == 80
        assert provider.last_usage["output_tokens"] == 40

        # Verify key was passed in header, NOT in URL path
        mock_conn.request.assert_called_once()
        args, kwargs = mock_conn.request.call_args
        assert args[0] == "POST"
        assert "key=" not in args[1]
        headers = kwargs.get("headers") or (args[3] if len(args) > 3 else {})
        assert headers.get("x-goog-api-key") == secret_key


def test_gemini_provider_rate_limit_429(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test_key")

    mock_conn = MagicMock()
    mock_conn.getresponse.return_value = MockHTTPResponse(429, b'{"error": "RESOURCE_EXHAUSTED"}')

    with patch("kihachi_mcp.services.ai_provider.HTTPSConnection", return_value=mock_conn):
        provider = GeminiAIProvider()
        # test_connection
        conn = provider.test_connection()
        assert conn["ok"] is False
        assert "429" in conn["message"] or "レート制限" in conn["message"]

        # generate
        with pytest.raises(InterpretationError) as exc_info:
            provider.generate("test")
        assert "429" in str(exc_info.value) or "レート制限" in str(exc_info.value)


def test_gemini_provider_auth_error_401_403(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "invalid_key")

    mock_conn = MagicMock()
    mock_conn.getresponse.return_value = MockHTTPResponse(403, b'{"error": "PERMISSION_DENIED"}')

    with patch("kihachi_mcp.services.ai_provider.HTTPSConnection", return_value=mock_conn):
        provider = GeminiAIProvider()
        conn = provider.test_connection()
        assert conn["ok"] is False
        assert "権限" in conn["message"] or "無効" in conn["message"]

        with pytest.raises(InterpretationError) as exc_info:
            provider.generate("test")
        assert "無効" in str(exc_info.value) or "権限" in str(exc_info.value)


def test_gemini_provider_malformed_json_response(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test_key")

    mock_gemini_body = {
        "candidates": [
            {
                "content": {
                    "parts": [{"text": "NOT_A_JSON_TEXT"}]
                },
                "finishReason": "STOP",
            }
        ]
    }
    raw_bytes = json.dumps(mock_gemini_body).encode("utf-8")

    mock_conn = MagicMock()
    mock_conn.getresponse.return_value = MockHTTPResponse(200, raw_bytes)

    with patch("kihachi_mcp.services.ai_provider.HTTPSConnection", return_value=mock_conn):
        provider = GeminiAIProvider()
        with pytest.raises(InterpretationError) as exc_info:
            provider.generate_structured("prompt", schema={"type": "object"})
        assert "JSON" in str(exc_info.value)


def test_gemini_provider_network_timeout(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test_key")

    mock_conn = MagicMock()
    mock_conn.request.side_effect = OSError("Connection timed out")

    with patch("kihachi_mcp.services.ai_provider.HTTPSConnection", return_value=mock_conn):
        provider = GeminiAIProvider()
        conn = provider.test_connection()
        assert conn["ok"] is False
        assert "接続できません" in conn["message"]

        with pytest.raises(InterpretationError) as exc_info:
            provider.generate("test")
        assert "接続できません" in str(exc_info.value)


def test_api_key_not_leaked_in_exceptions(monkeypatch):
    secret_key = "mock_gemini_key_for_redaction_test"
    monkeypatch.setenv("GEMINI_API_KEY", secret_key)

    mock_conn = MagicMock()
    mock_conn.getresponse.return_value = MockHTTPResponse(500, b'{"error": "Internal"}')

    with patch("kihachi_mcp.services.ai_provider.HTTPSConnection", return_value=mock_conn):
        provider = GeminiAIProvider()
        with pytest.raises(InterpretationError) as exc_info:
            provider.generate("test")
        err_msg = str(exc_info.value)
        assert secret_key not in err_msg


def test_gemini_provider_with_audio_success(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test_key")

    mock_gemini_body = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {
                            "text": json.dumps({
                                "theory_interpretation": {"summary": "Direct audio analysis"},
                                "generation_rules": {"kick_style": "four_on_the_floor"},
                            })
                        }
                    ]
                },
                "finishReason": "STOP",
            }
        ]
    }
    raw_bytes = json.dumps(mock_gemini_body).encode("utf-8")
    mock_conn = MagicMock()
    mock_conn.getresponse.return_value = MockHTTPResponse(200, raw_bytes)

    with patch("kihachi_mcp.services.ai_provider.HTTPSConnection", return_value=mock_conn):
        provider = GeminiAIProvider()
        fake_wav = b"RIFF....WAVEfmt ...."
        res = provider.generate_with_audio(
            prompt="analyze groove",
            audio_bytes=fake_wav,
            mime_type="audio/wav",
            schema={"type": "object"},
        )
        assert res["theory_interpretation"]["summary"] == "Direct audio analysis"

        mock_conn.request.assert_called_once()
        args, kwargs = mock_conn.request.call_args
        body_sent = json.loads(kwargs.get("body") or args[2])
        parts = body_sent["contents"][0]["parts"]
        assert len(parts) == 2
        assert "inlineData" in parts[0]
        assert parts[0]["inlineData"]["mimeType"] == "audio/wav"


def test_gemini_provider_with_audio_size_limit(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test_key")
    provider = GeminiAIProvider()
    oversized_audio = b"x" * (20 * 1024 * 1024 + 100)

    with pytest.raises(InterpretationError) as exc_info:
        provider.generate_with_audio("prompt", oversized_audio)
    assert "20MB" in str(exc_info.value)
