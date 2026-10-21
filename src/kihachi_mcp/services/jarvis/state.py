"""Explicit Jarvis states. A write cannot finish before readback."""

from __future__ import annotations

STATES = (
    "IDLE",
    "UNDERSTANDING",
    "READING_STATE",
    "PLANNING",
    "PROPOSED",
    "APPROVAL_REQUIRED",
    "APPROVED",
    "EXECUTING",
    "VERIFYING",
    "VERIFIED",
    "HUMAN_AUDITION_REQUIRED",
    "COMPLETED",
    "FAILED",
    "UNKNOWN",
)

#: Phase 1 may report a plan. It may not approve, execute, or verify a write.
_TRANSITIONS: dict[str, frozenset[str]] = {
    "IDLE": frozenset({"UNDERSTANDING"}),
    "UNDERSTANDING": frozenset({"READING_STATE", "FAILED", "UNKNOWN"}),
    "READING_STATE": frozenset({"PLANNING", "FAILED", "UNKNOWN"}),
    "PLANNING": frozenset({"PROPOSED", "APPROVAL_REQUIRED", "FAILED", "UNKNOWN"}),
    "PROPOSED": frozenset({"COMPLETED", "FAILED", "UNKNOWN"}),
    "APPROVAL_REQUIRED": frozenset({"APPROVED"}),
    "APPROVED": frozenset({"EXECUTING"}),
    "EXECUTING": frozenset({"VERIFYING"}),
    "VERIFYING": frozenset({"VERIFIED", "FAILED", "UNKNOWN", "HUMAN_AUDITION_REQUIRED"}),
    "VERIFIED": frozenset({"HUMAN_AUDITION_REQUIRED"}),
    "HUMAN_AUDITION_REQUIRED": frozenset(),
    "COMPLETED": frozenset(),
    "FAILED": frozenset(),
    "UNKNOWN": frozenset(),
}


class JarvisTransitionError(Exception):
    """Raised when a turn tries to skip or invent a state."""


class JarvisStateMachine:
    """One turn's state. UNKNOWN and FAILED cannot become COMPLETED."""

    def __init__(self) -> None:
        self.state = "IDLE"
        self.history = ["IDLE"]

    def reset(self) -> None:
        self.state = "IDLE"
        self.history = ["IDLE"]

    def transition(self, nxt: str) -> None:
        if nxt not in STATES:
            raise JarvisTransitionError(f"unknown state {nxt}")
        if nxt == "COMPLETED" and self.state != "PROPOSED":
            raise JarvisTransitionError(f"{self.state} cannot become COMPLETED")
        if nxt not in _TRANSITIONS[self.state]:
            raise JarvisTransitionError(f"{self.state} cannot become {nxt}")
        self.state = nxt
        self.history.append(nxt)
