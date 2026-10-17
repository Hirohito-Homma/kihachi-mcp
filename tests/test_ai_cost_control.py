import json
from pathlib import Path

import pytest

from kihachi_mcp.services.ai_cost_control import MonthlyCostGuard, estimate_cost
from kihachi_mcp.services.ai_provider import OpenAIProvider
from kihachi_mcp.services.studio_interpreter import InterpretationError
from kihachi_mcp.services.studio_runtime import StudioRuntime


def test_published_model_cost_estimates() -> None:
    assert estimate_cost("gpt-6.1-sol", 5_000, 1_000)["estimated_jpy"] == 3.0
    assert estimate_cost("gpt-6-astra", 5_000, 1_000)["estimated_jpy"] == 15.0


def test_monthly_guard_blocks_the_request_that_crosses_limit(tmp_path: Path) -> None:
    ledger = tmp_path / "costs.json"
    ledger.write_text(json.dumps([{"month": __import__("datetime").datetime.now().astimezone().strftime("%Y-%m"), "actual_jpy": 499.0}]))
    guard = MonthlyCostGuard(ledger)
    assert guard.authorize(1.0, 500)["allowed"] is True
    assert guard.authorize(1.01, 500)["allowed"] is False


def test_openai_provider_needs_environment_key(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    provider = OpenAIProvider()
    assert provider.health()["state"] == "api_key_missing"
    with pytest.raises(InterpretationError, match="OPENAI_API_KEY"):
        provider.generate("do not send")
    assert provider.test_connection()["ok"] is False


def test_openai_connection_checks_model_without_generating(monkeypatch) -> None:
    requests = []

    class FakeResponse:
        status = 200

        def read(self):
            return b'{"id":"gpt-6.1-sol","object":"model"}'

    class FakeConnection:
        def __init__(self, host, timeout):
            assert host == "api.openai.com" and timeout == 10

        def request(self, method, path, headers):
            requests.append((method, path, headers))

        def getresponse(self):
            return FakeResponse()

        def close(self):
            pass

    monkeypatch.setenv("OPENAI_API_KEY", "test-key-never-sent")
    monkeypatch.setattr("kihachi_mcp.services.ai_provider.HTTPSConnection", FakeConnection)
    assert OpenAIProvider().test_connection()["ok"] is True
    assert requests[0][:2] == ("GET", "/v1/models/gpt-6.1-sol")


def test_openai_connection_reports_ip_allowlist_without_exposing_response(monkeypatch) -> None:
    class FakeResponse:
        status = 401

        def read(self):
            return b'{"error":{"code":"ip_not_authorized","message":"private details"}}'

    class FakeConnection:
        def __init__(self, host, timeout):
            pass

        def request(self, method, path, headers):
            pass

        def getresponse(self):
            return FakeResponse()

        def close(self):
            pass

    monkeypatch.setenv("OPENAI_API_KEY", "test-key-never-sent")
    monkeypatch.setattr("kihachi_mcp.services.ai_provider.HTTPSConnection", FakeConnection)
    result = OpenAIProvider().test_connection()
    assert result["ok"] is False and "許可リスト" in result["message"]
    assert "private details" not in str(result)


def test_cost_ledger_never_contains_prompt_or_api_key(tmp_path: Path) -> None:
    path = tmp_path / "ledger.json"
    guard = MonthlyCostGuard(path)
    estimate = estimate_cost("gpt-6.1-sol", 5_000, 1_000)
    guard.record({"input_tokens": 100, "output_tokens": 20}, estimate)
    text = path.read_text()
    assert "prompt" not in text and "api_key" not in text


def test_runtime_blocks_paid_call_before_provider_when_limit_is_spent(
    tmp_path: Path, monkeypatch
) -> None:
    month = __import__("datetime").datetime.now().astimezone().strftime("%Y-%m")
    ledger = tmp_path / "ledger.json"
    ledger.write_text(json.dumps([{"month": month, "actual_jpy": 500.0}]))
    runtime = StudioRuntime(cost_guard=MonthlyCostGuard(ledger))
    assert runtime.update_settings({"ai_provider": "openai"}, persist=False)["ok"]
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-never-sent")
    monkeypatch.setattr(
        OpenAIProvider,
        "interpret",
        lambda self, brief: pytest.fail("provider must not run after the limit"),
    )
    result = runtime.generate("124 BPM、D# minor", confirmed_paid=True)
    assert result["ok"] is False
    assert "月間AI利用上限" in result["error"]


def test_paid_generation_needs_explicit_confirmation(tmp_path: Path, monkeypatch) -> None:
    runtime = StudioRuntime(cost_guard=MonthlyCostGuard(tmp_path / "ledger.json"))
    assert runtime.update_settings({"ai_provider": "openai"}, persist=False)["ok"]
    monkeypatch.setattr(OpenAIProvider, "interpret", lambda self, brief: pytest.fail("must not make a paid call"))
    preview = runtime.queue_generate("124 BPM、D# minor")
    assert preview["ok"] is False and preview["cost"]["estimate"]["estimated_jpy"] > 0
    assert runtime.generate("124 BPM、D# minor")["ok"] is False
    assert runtime.job_status()["state"] == "error"
