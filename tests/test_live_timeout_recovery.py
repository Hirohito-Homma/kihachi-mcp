"""Timeout recovery: a lost reply leaves an operation UNKNOWN, not failed.

These tests simulate the two halves of DOC-005 section 13 with a
transport that applies (or does not apply) an operation and then
drops the reply. The receipt must record the operation as attempted
but not completed with a timeout error, the remaining operations
must be abandoned, and a fresh snapshot must classify the operation
as landed or not landed so recovery can re-plan only what is
missing and complete under a new approval.
"""

from typing import Any

from live_fixtures import (
    empty_live_set,
    executor,
    gate,
    planner,
    project_plan,
    snapshot_of,
)

from kihachi_mcp.models.live_contract import STATUS_PARTIALLY_APPLIED, STATUS_VERIFIED
from kihachi_mcp.services.live_bridge import ERROR_TIMEOUT, LiveTransportError
from kihachi_mcp.services.live_execution_service import _verify_against_snapshot
from kihachi_mcp.services.live_state_inspector import LiveStateInspector
from kihachi_mcp.services.live_transport import METHOD_APPLY_OPERATION
from kihachi_mcp.services.live_transport_fake import FakeLiveTransport


class ReplyDroppingTransport:
    """Forward to the fake Set, dropping one apply reply as a timeout.

    With ``apply_first`` the device performs the operation and only
    the reply is lost (the dangerous case: the Set changed but the
    receipt cannot know). Without it the operation never happens.
    """

    def __init__(
        self,
        inner: FakeLiveTransport,
        *,
        drop_on_apply: int,
        apply_first: bool,
    ) -> None:
        self._inner = inner
        self._drop_on_apply = drop_on_apply
        self._apply_first = apply_first
        self._applies = 0

    def request(self, message: dict[str, Any]) -> dict[str, Any]:
        if message.get("method") == METHOD_APPLY_OPERATION:
            self._applies += 1
            if self._applies == self._drop_on_apply:
                if self._apply_first:
                    self._inner.request(message)
                raise LiveTransportError(
                    ERROR_TIMEOUT, "simulated reply loss after 40s"
                )
        return self._inner.request(message)


def _run_with_dropped_reply(apply_first: bool):
    inner = FakeLiveTransport(empty_live_set(tempo=120.0))
    transport = ReplyDroppingTransport(inner, drop_on_apply=2, apply_first=apply_first)
    approval_gate = gate()
    plan = planner().create_session_plan(project_plan(), snapshot_of(inner))
    token = approval_gate.approve(plan)
    receipt = executor(transport, approval_gate).execute(plan, True, token)
    return inner, approval_gate, plan, receipt


def test_a_lost_reply_after_applying_leaves_the_operation_unknown() -> None:
    inner, _, plan, receipt = _run_with_dropped_reply(apply_first=True)

    assert receipt.status == STATUS_PARTIALLY_APPLIED
    assert receipt.failed_operation == receipt.attempted_operations[-1]
    assert receipt.failed_operation not in receipt.completed_operations
    assert receipt.error.startswith(ERROR_TIMEOUT)

    failed = next(
        op for op in plan.operations if op.operation_id == receipt.failed_operation
    )
    current = LiveStateInspector(inner).snapshot()
    assert _verify_against_snapshot(failed, current) == []


def test_a_lost_reply_without_applying_recovers_by_replanning() -> None:
    inner, approval_gate, plan, receipt = _run_with_dropped_reply(apply_first=False)

    assert receipt.status == STATUS_PARTIALLY_APPLIED
    assert receipt.failed_operation not in receipt.completed_operations
    assert receipt.error.startswith(ERROR_TIMEOUT)

    failed = next(
        op for op in plan.operations if op.operation_id == receipt.failed_operation
    )
    current = LiveStateInspector(inner).snapshot()
    assert _verify_against_snapshot(failed, current) != []

    recovery_plan = planner().create_session_plan(project_plan(), snapshot_of(inner))
    recovery = executor(inner, approval_gate).execute(
        recovery_plan, True, approval_gate.approve(recovery_plan)
    )

    assert recovery.status == STATUS_VERIFIED
    final = LiveStateInspector(inner).snapshot()
    assert {track.name for track in final.tracks} >= {
        "Kick [KIHACHI]",
        "Bass [KIHACHI]",
    }
