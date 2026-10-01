"""Local estimates and a hard monthly guard for paid AI providers."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

DEFAULT_MONTHLY_LIMIT_JPY = 500
DEFAULT_USD_JPY = 150.0


@dataclass(frozen=True)
class ModelPrice:
    input_usd_per_million: float
    output_usd_per_million: float


# Standard, short-context prices read from the official OpenAI pricing page.
OPENAI_PRICES = {
    "gpt-6-luna": ModelPrice(0.05, 0.25),
    "gpt-6.1-sol": ModelPrice(1.0, 5.0),
    "gpt-6-astra": ModelPrice(5.0, 25.0),
}


def estimate_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
    usd_jpy: float = DEFAULT_USD_JPY,
) -> dict[str, Any]:
    """Return a conservative local estimate without contacting OpenAI."""
    price = OPENAI_PRICES.get(model)
    if price is None:
        raise ValueError("料金を確認できるOpenAIモデルを選んでください")
    usd = (
        max(0, input_tokens) * price.input_usd_per_million
        + max(0, output_tokens) * price.output_usd_per_million
    ) / 1_000_000
    return {
        "model": model,
        "input_tokens": max(0, input_tokens),
        "output_tokens": max(0, output_tokens),
        "estimated_usd": round(usd, 8),
        "estimated_jpy": round(usd * usd_jpy, 4),
        "usd_jpy": usd_jpy,
        "estimate_only": True,
    }


class MonthlyCostGuard:
    """Persist only token counts and cost; prompts and API keys never enter the ledger."""

    def __init__(self, path: Path | None = None) -> None:
        configured = os.environ.get("KIHACHI_AI_COST_FILE")
        self.path = path or (Path(configured) if configured else Path.home() / ".kihachi" / "ai-costs.json")

    def status(self, limit_jpy: int = DEFAULT_MONTHLY_LIMIT_JPY) -> dict[str, Any]:
        month = datetime.now().astimezone().strftime("%Y-%m")
        rows = self._read()
        spent = round(
            sum(float(row.get("actual_jpy") or row.get("estimated_jpy") or 0) for row in rows if row.get("month") == month),
            4,
        )
        return {
            "month": month,
            "spent_jpy": spent,
            "limit_jpy": limit_jpy,
            "remaining_jpy": max(0.0, round(limit_jpy - spent, 4)),
        }

    def authorize(self, estimate_jpy: float, limit_jpy: int = DEFAULT_MONTHLY_LIMIT_JPY) -> dict[str, Any]:
        current = self.status(limit_jpy)
        allowed = current["spent_jpy"] + max(0.0, estimate_jpy) <= limit_jpy
        return {**current, "allowed": allowed, "estimated_request_jpy": round(estimate_jpy, 4)}

    def record(self, usage: dict[str, Any], estimate: dict[str, Any]) -> None:
        rows = self._read()
        input_tokens = int(usage.get("input_tokens") or estimate["input_tokens"])
        output_tokens = int(usage.get("output_tokens") or estimate["output_tokens"])
        actual = estimate_cost(estimate["model"], input_tokens, output_tokens, float(estimate["usd_jpy"]))
        rows.append({
            "month": datetime.now().astimezone().strftime("%Y-%m"),
            "model": estimate["model"],
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "estimated_jpy": estimate["estimated_jpy"],
            "actual_jpy": actual["estimated_jpy"],
        })
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, self.path)

    def _read(self) -> list[dict[str, Any]]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        return [row for row in data if isinstance(row, dict)] if isinstance(data, list) else []
