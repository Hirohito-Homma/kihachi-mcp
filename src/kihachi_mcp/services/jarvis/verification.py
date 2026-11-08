"""Readback order. This module does not call Live."""

from __future__ import annotations

from kihachi_mcp.models.live_contract import (
    STATUS_UNAVAILABLE,
    STATUS_VERIFICATION_FAILED,
    STATUS_VERIFIED,
)
from kihachi_mcp.services.jarvis.state import JarvisStateMachine, JarvisTransitionError

_RECEIPT_COMPARISON = {
    STATUS_VERIFIED: "matched",
    STATUS_VERIFICATION_FAILED: "mismatched",
    STATUS_UNAVAILABLE: "incomparable",
}

_OUTCOMES = {
    "matched": "HUMAN_AUDITION_REQUIRED",
    "mismatched": "FAILED",
    "incomparable": "UNKNOWN",
    "pending": "UNKNOWN",
}

_MESSAGES = {
    "matched": "Liveは計画どおり応答しました。音の完成ではありません。人が聴く必要があります。",
    "mismatched": "読み戻しが計画と一致しません。完了にはしていません。",
    "incomparable": "比較できないので UNKNOWN です。成功にはしていません。",
    "pending": "LIVE VERIFICATION PENDING のままです。完了にはしていません。",
}


def settle_readback(machine: JarvisStateMachine, comparison: str) -> str:
    """Move EXECUTING to VERIFYING, then to a terminal state. Never COMPLETED."""
    if machine.state != "EXECUTING":
        raise JarvisTransitionError("readback starts only after EXECUTING")
    machine.transition("VERIFYING")
    if comparison not in _OUTCOMES:
        machine.transition("UNKNOWN")
        return _MESSAGES["incomparable"]
    if comparison == "matched":
        machine.transition("VERIFIED")
    machine.transition(_OUTCOMES[comparison])
    return _MESSAGES[comparison]


def settle_receipt(status: str) -> tuple[JarvisStateMachine, str]:
    """Walk one execution receipt status through EXECUTING and VERIFYING."""
    machine = JarvisStateMachine()
    for state in ("UNDERSTANDING", "READING_STATE", "PLANNING", "APPROVAL_REQUIRED", "APPROVED", "EXECUTING"):
        machine.transition(state)
    return machine, settle_readback(machine, _RECEIPT_COMPARISON.get(status, "pending"))
