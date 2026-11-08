"""Gemini provider: settings wiring, schema reduction, and contract."""

import pytest

from kihachi_mcp.services.ai_provider import (
    GeminiProvider,
    _gemini_schema,
    provider_from_settings,
)
from kihachi_mcp.services.studio_interpreter import InterpretationError
from kihachi_mcp.services.studio_workflow import validate_settings


def test_provider_from_settings_selects_gemini_with_default_model() -> None:
    provider = provider_from_settings({"ai_provider": "gemini"})
    assert isinstance(provider, GeminiProvider)
    assert provider.model == "gemini-3.8-flash"
    assert provider.thinking_budget == 0
    assert provider.capabilities() == {
        "provider": "gemini",
        "structured_output": True,
        "downloads_models": False,
        "paid_api": False,
    }


def test_provider_from_settings_honours_gemini_model_override() -> None:
    provider = provider_from_settings(
        {"ai_provider": "gemini", "gemini_model": "gemini-2.5-flash-lite"}
    )
    assert isinstance(provider, GeminiProvider)
    assert provider.model == "gemini-2.5-flash-lite"


def test_gemini_health_reports_missing_key_without_network(monkeypatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    provider = GeminiProvider(api_key=None)
    empty = GeminiProvider(api_key="")
    assert provider.health()["ok"] is False
    assert provider.health()["state"] == "api_key_missing"
    assert empty._key() is None


def test_gemini_key_falls_back_to_google_api_key(monkeypatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("GOOGLE_API_KEY", "google-key-not-sent")
    assert GeminiProvider(api_key=None)._key() == "google-key-not-sent"


def test_gemini_schema_drops_unsupported_keys_and_uppercases_types() -> None:
    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["notes"],
        "properties": {
            "notes": {
                "type": "array",
                "maxItems": 4096,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "pitch": {"type": "integer", "minimum": 0, "maximum": 127},
                        "duration": {"type": "number", "exclusiveMinimum": 0},
                    },
                },
            }
        },
    }
    reduced = _gemini_schema(schema)
    assert reduced["type"] == "OBJECT"
    assert "additionalProperties" not in reduced
    note_items = reduced["properties"]["notes"]
    assert note_items["type"] == "ARRAY"
    assert note_items["maxItems"] == 4096
    duration = note_items["items"]["properties"]["duration"]
    assert duration == {"type": "NUMBER"}


class _FakeGemini(GeminiProvider):
    """Replace the HTTPS transport with canned Gemini-shaped responses."""

    def __init__(self, responses: dict) -> None:
        super().__init__(api_key="test-key-not-sent")
        self._responses = responses
        self.requests: list[tuple[str, str, dict | None]] = []

    def _request(self, method, path, payload=None):  # type: ignore[override]
        self.requests.append((method, path, payload))
        response = self._responses[path]
        if isinstance(response, Exception):
            raise response
        return response


_GENERATE_PATH = "/v1beta/models/gemini-3.8-flash:generateContent"


def test_gemini_generate_structured_parses_candidate_text() -> None:
    provider = _FakeGemini({
        _GENERATE_PATH: {
            "candidates": [{
                "content": {
                    "parts": [{
                        "text": '{"notes": [{"pitch": 39, "start": 0.0, "duration": 0.5, "velocity": 100, "channel": 0}]}'
                    }]
                }
            }],
            "usageMetadata": {"promptTokenCount": 120, "candidatesTokenCount": 45},
        }
    })
    payload = provider.generate_structured("compose", {"type": "object"})
    assert payload["notes"][0]["pitch"] == 39
    method, path, body = provider.requests[0]
    assert (method, path) == ("POST", _GENERATE_PATH)
    assert body is not None
    config = body["generationConfig"]
    assert config["responseMimeType"] == "application/json"
    assert config["responseSchema"] == {"type": "OBJECT"}
    assert config["thinkingConfig"] == {"thinkingBudget": 0}
    assert provider.last_usage == {"input_tokens": 120, "output_tokens": 45}


def test_gemini_generate_structured_retries_without_schema_on_400() -> None:
    calls: list[dict | None] = []

    class _FlakyGemini(GeminiProvider):
        def __init__(self) -> None:
            super().__init__(api_key="test-key-not-sent")

        def _request(self, method, path, payload=None):  # type: ignore[override]
            calls.append(payload)
            if payload and "responseSchema" in (payload.get("generationConfig") or {}):
                raise InterpretationError("Geminiが応答を拒否しました (HTTP 400)")
            return {
                "candidates": [{
                    "content": {"parts": [{"text": '{"notes": []}'}]}
                }]
            }

    provider = _FlakyGemini()
    assert provider.generate_structured("compose", {"type": "object"}) == {"notes": []}
    assert len(calls) == 2
    assert "responseSchema" not in (calls[1] or {})["generationConfig"]
    assert "thinkingConfig" not in (calls[1] or {})["generationConfig"]


def test_gemini_list_models_strips_prefix_and_filters() -> None:
    provider = _FakeGemini({
        "/v1beta/models": {
            "models": [
                {"name": "models/gemini-2.5-flash", "supportedGenerationMethods": ["generateContent"]},
                {"name": "models/text-embedding-004", "supportedGenerationMethods": ["embedContent"]},
                {"name": "models/gemini-2.5-pro", "supportedGenerationMethods": ["generateContent"]},
            ]
        }
    })
    assert provider.list_models() == ["gemini-2.5-flash", "gemini-2.5-pro"]
    health = provider.health()
    assert health["ok"] is True and health["provider"] == "gemini"


def test_gemini_interpret_falls_back_offline_without_key(monkeypatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    provider = GeminiProvider(api_key=None)
    brief = provider.interpret("Mutation Funk、110 BPM、D#マイナー、16小節")
    assert brief.tempo.value == 110


def test_provider_from_settings_honours_gemini_thinking_budget() -> None:
    provider = provider_from_settings(
        {"ai_provider": "gemini", "gemini_thinking_budget": 512}
    )
    assert isinstance(provider, GeminiProvider)
    assert provider.thinking_budget == 512
    fallback = provider_from_settings(
        {"ai_provider": "gemini", "gemini_thinking_budget": "fast"}
    )
    assert isinstance(fallback, GeminiProvider)
    assert fallback.thinking_budget == 0


def test_validate_settings_gemini_thinking_budget() -> None:
    assert validate_settings({"gemini_thinking_budget": 0}, []) == ""
    assert validate_settings({"gemini_thinking_budget": 1024}, []) == ""
    assert validate_settings({"gemini_thinking_budget": -1}, []) != ""
    assert validate_settings({"gemini_thinking_budget": 24_577}, []) != ""
    assert validate_settings({"gemini_thinking_budget": True}, []) != ""
    assert validate_settings({"gemini_thinking_budget": "0"}, []) != ""


def test_gemini_generate_sends_thinking_budget_by_default() -> None:
    provider = _FakeGemini({
        _GENERATE_PATH: {
            "candidates": [{"content": {"parts": [{"text": "ok"}]}}]
        }
    })
    assert provider.generate("ping") == "ok"
    _, _, body = provider.requests[0]
    assert body is not None
    assert body["generationConfig"]["thinkingConfig"] == {"thinkingBudget": 0}


def test_gemini_thinking_budget_none_omits_thinking_config() -> None:
    class _NoThinking(GeminiProvider):
        def __init__(self) -> None:
            super().__init__(api_key="t", thinking_budget=None)
            self.bodies: list = []

        def _request(self, method, path, payload=None):  # type: ignore[override]
            self.bodies.append(payload)
            return {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}

    provider = _NoThinking()
    assert provider.generate("ping") == "ok"
    assert "thinkingConfig" not in provider.bodies[0]["generationConfig"]


def test_gemini_generate_retries_without_thinking_on_400() -> None:
    calls: list = []

    class _ThinkingRejected(GeminiProvider):
        def __init__(self) -> None:
            super().__init__(api_key="t")

        def _request(self, method, path, payload=None):  # type: ignore[override]
            calls.append(payload)
            config = (payload or {}).get("generationConfig") or {}
            if "thinkingConfig" in config:
                raise InterpretationError("Geminiが応答を拒否しました (HTTP 400)")
            return {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}

    provider = _ThinkingRejected()
    assert provider.generate("ping") == "ok"
    assert len(calls) == 2
    assert "thinkingConfig" not in calls[1]["generationConfig"]


def test_gemini_request_requires_key() -> None:
    provider = GeminiProvider(api_key="test-key-not-sent")
    assert provider._key() == "test-key-not-sent"
    with pytest.raises(InterpretationError, match="GEMINI_API_KEYが設定されていません"):
        GeminiProvider(api_key="")._request("GET", "/v1beta/models")
