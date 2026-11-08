"""Ollama Cloud provider: settings wiring and contract, with a fake transport."""

import pytest

from kihachi_mcp.services.ai_provider import OllamaCloudProvider, provider_from_settings
from kihachi_mcp.services.studio_interpreter import InterpretationError


def test_provider_from_settings_selects_cloud_with_default_model() -> None:
    provider = provider_from_settings({"ai_provider": "ollama_cloud"})
    assert isinstance(provider, OllamaCloudProvider)
    assert provider.model == "gemma4:31b"
    assert provider.capabilities() == {
        "provider": "ollama_cloud",
        "structured_output": True,
        "downloads_models": False,
        "paid_api": False,
    }


def test_provider_from_settings_honours_cloud_model_override() -> None:
    provider = provider_from_settings(
        {"ai_provider": "ollama_cloud", "ollama_cloud_model": "gpt-oss:120b"}
    )
    assert isinstance(provider, OllamaCloudProvider)
    assert provider.model == "gpt-oss:120b"


def test_cloud_health_reports_missing_key_without_network(monkeypatch) -> None:
    monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
    provider = OllamaCloudProvider(api_key=None)
    # An empty-string key injection must behave like "not configured" too.
    empty = OllamaCloudProvider(api_key="")
    assert provider.health()["ok"] is False
    assert provider.health()["state"] == "api_key_missing"
    assert empty._key() is None


class _FakeCloud(OllamaCloudProvider):
    """Replace the HTTPS transport with canned Ollama-shaped responses."""

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


def test_cloud_generate_structured_parses_fenced_json() -> None:
    provider = _FakeCloud({
        "/api/chat": {
            "message": {
                "content": '```json\n{"notes": [{"pitch": 39, "start": 0.0, "duration": 0.5, "velocity": 100, "channel": 0}]}\n```'
            }
        }
    })
    payload = provider.generate_structured("compose", {"type": "object"})
    assert payload["notes"][0]["pitch"] == 39
    method, path, body = provider.requests[0]
    assert (method, path) == ("POST", "/api/chat")
    assert body is not None and body["format"] == {"type": "object"}
    assert body["model"] == "gemma4:31b"


def test_cloud_list_models_reads_tags() -> None:
    provider = _FakeCloud({
        "/api/tags": {"models": [{"name": "gemma4:31b"}, {"name": "gpt-oss:120b"}]}
    })
    assert provider.list_models() == ["gemma4:31b", "gpt-oss:120b"]
    health = provider.health()
    assert health["ok"] is True and health["provider"] == "ollama_cloud"


def test_cloud_interpret_falls_back_offline_without_key(monkeypatch) -> None:
    monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
    provider = OllamaCloudProvider(api_key=None)
    brief = provider.interpret("Mutation Funk、110 BPM、D#マイナー、16小節")
    # Offline interpretation still honours the explicit values in the brief.
    assert brief.tempo.value == 110


def test_cloud_request_errors_are_distinct() -> None:
    provider = OllamaCloudProvider(api_key="test-key-not-sent")
    with pytest.raises(InterpretationError, match="OLLAMA_API_KEYが設定されていません"):
        OllamaCloudProvider(api_key="")._request("GET", "/api/tags")
    assert provider._key() == "test-key-not-sent"
