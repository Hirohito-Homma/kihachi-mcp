"""Run an approved plan on the in-memory Live stand-in. Real Live is refused."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from kihachi_mcp.models.live_contract import STATUS_VERIFIED
from kihachi_mcp.models.live_mutation import LiveMutationPlan
from kihachi_mcp.services.jarvis.verification import settle_receipt
from kihachi_mcp.services.live_approval_gate import ApprovalGate
from kihachi_mcp.services.live_execution_service import LiveExecutionService
from kihachi_mcp.services.live_state_inspector import LiveStateInspector
from kihachi_mcp.services.live_transport_fake import FakeLiveTransport


def rebind_uninspected_plan(plan: LiveMutationPlan, transport: FakeLiveTransport) -> LiveMutationPlan:
    """Copy the fake Set fingerprint onto an unread plan. The old approval no longer matches."""
    fingerprint = LiveStateInspector(transport).snapshot().set_fingerprint
    if plan.set_fingerprint == fingerprint:
        return plan
    return replace(plan, set_fingerprint=fingerprint)


def run_approved_plan_on_fake(
    plan: LiveMutationPlan,
    gate: ApprovalGate,
    transport: Any,
) -> dict[str, Any]:
    """Execute only when the gate holds the plan and the transport is fake."""
    if not isinstance(transport, FakeLiveTransport):
        return _stopped("この経路は偽の Live だけです。実機には送っていません。", executed=False)
    token = gate.token_for(plan.plan_hash)
    service = LiveExecutionService(transport=transport, approval_gate=gate)
    if token is None:
        service.execute(plan, approved=False)
        return _stopped("承認されていない計画は実行しません。Liveは変えていません。", executed=False)
    receipt = service.execute(plan, approved=True, approval_token=token)
    machine, message = settle_receipt(receipt.status)
    if receipt.status == STATUS_VERIFIED:
        message = "偽の Live は計画どおり応答しました。実機は見ていません。音の完成ではありません。"
    return {
        "ok": receipt.status == STATUS_VERIFIED,
        "status": receipt.status,
        "state": machine.state,
        "states": list(machine.history),
        "reply": message,
        "executed": bool(receipt.completed_operations),
        "live_changed": bool(receipt.completed_operations),
        "musical_quality_claimed": False,
        "paid_api": False,
    }


def _stopped(message: str, *, executed: bool) -> dict[str, Any]:
    return {
        "ok": False,
        "status": "approval_required",
        "state": "APPROVAL_REQUIRED",
        "states": ["APPROVAL_REQUIRED"],
        "reply": message,
        "executed": executed,
        "live_changed": False,
        "musical_quality_claimed": False,
        "paid_api": False,
    }
