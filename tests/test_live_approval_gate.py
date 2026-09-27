"""Approval and idempotency guarantees."""

from datetime import UTC, datetime, timedelta

import pytest
from live_fixtures import FIXED_NOW, fixed_clock, gate

from kihachi_mcp.models.live_mutation import LiveMutationOperation, LiveMutationPlan
from kihachi_mcp.services.live_approval_gate import ApprovalError, ApprovalGate


def _plan(
    request_id: str = "req-1",
    idempotency_key: str = "key-1",
    tempo: float = 110.0,
    expires_at: str = "",
) -> LiveMutationPlan:
    return LiveMutationPlan(
        request_id=request_id,
        idempotency_key=idempotency_key,
        source_plan_hash="source",
        set_fingerprint="fingerprint",
        expires_at=expires_at
        or (FIXED_NOW + timedelta(minutes=15)).isoformat(),
        operations=[
            LiveMutationOperation(
                operation_id="001-set_tempo",
                op="set_tempo",
                arguments={"tempo": tempo},
                expected_readback={"tempo": tempo},
            )
        ],
    )


def test_approved_plan_is_authorized_once() -> None:
    subject = gate()
    plan = _plan()

    token = subject.approve(plan)
    subject.authorize(plan, token)

    assert subject.is_consumed(plan.idempotency_key) is False


def test_unapproved_plan_is_refused() -> None:
    with pytest.raises(ApprovalError, match="unknown or retired") as error:
        gate().authorize(_plan(), "not-a-real-token")

    assert error.value.code == "unapproved"


def test_editing_an_approved_plan_invalidates_the_approval() -> None:
    subject = gate()
    plan = _plan()
    token = subject.approve(plan)
    edited = _plan(tempo=128.0)

    assert edited.plan_hash != plan.plan_hash
    with pytest.raises(ApprovalError, match="changed after it was approved") as error:
        subject.authorize(edited, token)
    assert error.value.code == "plan_changed"


def test_the_same_idempotency_key_cannot_run_twice() -> None:
    subject = gate()
    plan = _plan()
    token = subject.approve(plan)
    subject.authorize(plan, token)
    subject.consume(plan, token)

    with pytest.raises(ApprovalError, match="already used") as error:
        subject.authorize(plan, token)
    assert error.value.code == "duplicate"
    assert subject.is_consumed(plan.idempotency_key) is True


def test_a_consumed_key_cannot_be_approved_again() -> None:
    subject = gate()
    plan = _plan()
    token = subject.approve(plan)
    subject.consume(plan, token)

    with pytest.raises(ApprovalError, match="already used"):
        subject.approve(plan)


def test_an_expired_plan_is_refused() -> None:
    subject = gate()
    plan = _plan(expires_at=(FIXED_NOW - timedelta(minutes=1)).isoformat())
    token = subject.approve(plan)

    with pytest.raises(ApprovalError, match="expired") as error:
        subject.authorize(plan, token)
    assert error.value.code == "expired"


def test_an_unparsable_expiry_is_treated_as_expired() -> None:
    subject = gate()
    plan = _plan(expires_at="whenever")
    token = subject.approve(plan)

    with pytest.raises(ApprovalError, match="expired"):
        subject.authorize(plan, token)


def test_a_plan_with_no_operations_cannot_be_approved() -> None:
    empty = LiveMutationPlan(
        request_id="req-2",
        idempotency_key="key-2",
        source_plan_hash="source",
        set_fingerprint="fingerprint",
        expires_at=(FIXED_NOW + timedelta(minutes=15)).isoformat(),
    )

    with pytest.raises(ApprovalError, match="nothing to approve"):
        gate().approve(empty)


def test_consumed_keys_survive_a_restart_when_persistence_is_configured(
    tmp_path,
) -> None:
    storage = tmp_path / "approvals.json"
    plan = _plan()
    first = ApprovalGate(storage_path=storage, clock=fixed_clock())
    token = first.approve(plan)
    first.consume(plan, token)

    restarted = ApprovalGate(storage_path=storage, clock=fixed_clock())

    assert restarted.is_consumed(plan.idempotency_key) is True
    with pytest.raises(ApprovalError, match="already used"):
        restarted.authorize(plan, token)


def test_approval_tokens_are_not_predictable() -> None:
    subject = gate()
    first = subject.approve(_plan(request_id="a", idempotency_key="ka"))
    second = subject.approve(_plan(request_id="b", idempotency_key="kb"))

    assert first != second
    assert len(first) >= 24


def test_a_token_from_another_request_is_refused() -> None:
    subject = gate()
    token = subject.approve(_plan(request_id="a", idempotency_key="ka"))
    other = LiveMutationPlan(
        request_id="b",
        idempotency_key="kb",
        source_plan_hash="source",
        set_fingerprint="fingerprint",
        expires_at=(datetime.now(UTC) + timedelta(minutes=15)).isoformat(),
        operations=_plan().operations,
    )

    with pytest.raises(ApprovalError) as error:
        subject.authorize(other, token)

    assert error.value.code == "plan_changed"
