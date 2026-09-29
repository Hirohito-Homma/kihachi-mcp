import json
from pathlib import Path

import pytest

from kihachi_mcp.services.ai_cost_control import MonthlyCostGuard, estimate_cost
from kihachi_mcp.services.ai_provider import OpenAIProvider
from kihachi_mcp.services.studio_interpreter import InterpretationError
from kihachi_mcp.services.studio_runtime import StudioRuntime


def test_published_model_cost_estimates() -> None:
    assert estimate_cost("gpt-6.1-sol", 5_000, 1_000)["estimated_jpy"] == 1.5
    assert estimate_cost("gpt-6-astra", 5_000, 1_000)["estimated_jpy"] == 7.5


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
    result = runtime.generate("124 BPM、D# minor")
    assert result["ok"] is False
    assert "月間AI利用上限" in result["error"]
