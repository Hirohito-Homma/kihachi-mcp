"""Jarvis gateway writes. ``confirm=true`` alone never calls the tool."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from kihachi_mcp.models.live_mutation import LiveMutationPlan
from kihachi_mcp.services.live_approval_gate import ApprovalGate

Tool = Callable[..., dict[str, Any]]


def request_gateway_write(
    tool: Tool,
    plan: LiveMutationPlan,
    gate: ApprovalGate,
    *,
    confirm: bool = False,
    execute: bool = False,
) -> dict[str, Any]:
    """Call ``tool`` only when this plan is already approved and execution is allowed.

    Studio does not pass ``execute=True``. Tests may, and only with a fake tool.
    """
    if not gate.holds(plan.plan_hash):
        return _refused(_refusal_message(confirm, plan.destructive_operation_count > 0))
    if not execute:
        return {
            "ok": True,
            "authorized": True,
            "executed": False,
            "live_changed": False,
            "paid_api": False,
            "called": False,
            "message": "承認は記録済みです。Liveには送っていません。",
        }
    result = tool(confirm=True)
    if not isinstance(result, dict):
        result = {"ok": False, "live_changed": False}
    return {
        "ok": bool(result.get("ok")),
        "authorized": True,
        "executed": True,
        "live_changed": bool(result.get("live_changed")),
        "paid_api": False,
        "called": True,
        "result": result,
    }


def _refusal_message(confirm: bool, destructive: bool) -> str:
    if destructive:
        return "破壊的な操作は人の承認が無いと失敗します。Liveには送っていません。"
    if confirm:
        return "confirm=true だけでは送りません。人の承認が必要です。"
    return "承認されていない書き込みは送りません。"


def _refused(message: str) -> dict[str, Any]:
    return {
        "ok": False,
        "authorized": False,
        "executed": False,
        "live_changed": False,
        "paid_api": False,
        "called": False,
        "error": message,
    }
