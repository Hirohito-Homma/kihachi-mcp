"""Execution guarantees: approval, fingerprints, idempotency, and readback."""

from dataclasses import replace

from live_fixtures import (
    approved_run,
    empty_live_set,
    executor,
    gate,
    planner,
    project_plan,
    snapshot_of,
)

from kihachi_mcp.models.live_contract import (
    OP_SET_TEMPO,
    STATUS_APPROVAL_REQUIRED,
    STATUS_BLOCKED,
    STATUS_PARTIALLY_APPLIED,
    STATUS_UNAVAILABLE,
    STATUS_VERIFICATION_FAILED,
    STATUS_VERIFIED,
)
from kihachi_mcp.models.live_mutation import LiveMutationOperation
from kihachi_mcp.services.live_bridge import MAX_PLAN_OPERATIONS
from kihachi_mcp.services.live_execution_service import LiveExecutionService
from kihachi_mcp.services.live_transport import (
    ERROR_DISCONNECTED,
    ERROR_OPERATION_FAILED,
    LiveTransportError,
)
from kihachi_mcp.services.live_transport_fake import FakeLiveTransport


def test_fake_transport_happy_path_is_verified() -> None:
    transport = FakeLiveTransport(empty_live_set(tempo=120.0))

    plan, receipt, _, _ = approved_run(transport)

    assert receipt.status == STATUS_VERIFIED
    assert receipt.mismatches == []
    assert receipt.failed_operation == ""
    assert receipt.completed_operations == receipt.attempted_operations
    assert len(receipt.completed_operations) == len(plan.operations)
    assert receipt.set_fingerprint_before == plan.set_fingerprint
    assert receipt.set_fingerprint_after != receipt.set_fingerprint_before
    assert receipt.is_complete is True


def test_verified_run_actually_built_the_session_layout() -> None:
    transport = FakeLiveTransport(empty_live_set(tempo=120.0))

    approved_run(transport)
    live = transport.live_set

    assert [track["name"] for track in live.tracks] == [
        "Kick [KIHACHI]",
        "Bass [KIHACHI]",
    ]
    assert [scene["name"] for scene in live.scenes] == [
        "Intro [KIHACHI]",
        "Drop [KIHACHI]",
    ]
    assert len(live.session_clips) == 4
    assert all(clip.notes for clip in live.session_clips.values())
    assert live.tracks[0]["device_names"] == ["Drum Rack"]
    assert live.tempo == 110.0


def test_execution_without_approval_changes_nothing() -> None:
    transport = FakeLiveTransport(empty_live_set(tempo=120.0))
    subject_gate = gate()
    plan = planner().create_session_plan(project_plan(), snapshot_of(transport))

    receipt = executor(transport, subject_gate).execute(plan, approved=False)

    assert receipt.status == STATUS_APPROVAL_REQUIRED
    assert receipt.attempted_operations == []
    assert transport.applied_operation_ids == []
    assert transport.live_set.tracks == []


def test_execution_with_a_forged_token_changes_nothing() -> None:
    transport = FakeLiveTransport(empty_live_set(tempo=120.0))
    subject_gate = gate()
    plan = planner().create_session_plan(project_plan(), snapshot_of(transport))

    receipt = executor(transport, subject_gate).execute(
        plan, approved=True, approval_token="forged"
    )

    assert receipt.status == STATUS_APPROVAL_REQUIRED
    assert transport.live_set.tracks == []


def test_a_set_that_changed_after_planning_is_refused() -> None:
    transport = FakeLiveTransport(empty_live_set(tempo=120.0))
    subject_gate = gate()
    plan = planner().create_session_plan(project_plan(), snapshot_of(transport))
    token = subject_gate.approve(plan)
    transport.live_set.add_track("User Pad", "midi")

    receipt = executor(transport, subject_gate).execute(
        plan, approved=True, approval_token=token
    )

    assert receipt.status == STATUS_BLOCKED
    assert "changed after this plan was created" in receipt.error
    assert transport.applied_operation_ids == []
    assert len(transport.live_set.tracks) == 1


def test_the_same_plan_cannot_be_executed_twice() -> None:
    transport = FakeLiveTransport(empty_live_set(tempo=120.0))

    plan, first, subject_gate, token = approved_run(transport)
    applied_after_first = list(transport.applied_operation_ids)
    second = executor(transport, subject_gate).execute(
        plan, approved=True, approval_token=token
    )

    assert first.status == STATUS_VERIFIED
    assert second.status == STATUS_BLOCKED
    assert "already used" in second.error
    assert transport.applied_operation_ids == applied_after_first


def test_recording_started_after_planning_blocks_execution() -> None:
    transport = FakeLiveTransport(empty_live_set(tempo=110.0))
    subject_gate = gate()
    plan = planner().create_session_plan(project_plan(), snapshot_of(transport))
    token = subject_gate.approve(plan)
    transport.live_set.is_recording = True

    receipt = executor(transport, subject_gate).execute(
        plan, approved=True, approval_token=token
    )

    assert receipt.status == STATUS_BLOCKED
    assert "recording" in receipt.error
    assert transport.applied_operation_ids == []


def test_playback_started_after_planning_blocks_structural_changes() -> None:
    transport = FakeLiveTransport(empty_live_set(tempo=110.0))
    subject_gate = gate()
    plan = planner().create_session_plan(project_plan(), snapshot_of(transport))
    token = subject_gate.approve(plan)
    transport.live_set.is_playing = True

    receipt = executor(transport, subject_gate).execute(
        plan, approved=True, approval_token=token
    )

    assert receipt.status == STATUS_BLOCKED
    assert "transport is running" in receipt.error
    assert transport.applied_operation_ids == []


def test_a_readback_mismatch_is_never_verified() -> None:
    transport = FakeLiveTransport(empty_live_set(tempo=120.0))
    snapshot = snapshot_of(transport)
    subject_gate = gate()
    plan = planner().create_session_plan(project_plan(), snapshot)
    lying_operation = plan.operations[0].operation_id
    transport.readback_overrides[lying_operation] = {"tempo": 128.0}
    token = subject_gate.approve(plan)

    receipt = executor(transport, subject_gate).execute(
        plan, approved=True, approval_token=token
    )

    assert receipt.status == STATUS_VERIFICATION_FAILED
    assert receipt.mismatches[0].field_name == "tempo"
    assert receipt.mismatches[0].expected == 110.0
    assert receipt.mismatches[0].observed == 128.0
    assert receipt.is_complete is False
    assert receipt.needs_manual_review is True


def test_a_missing_readback_field_is_never_verified() -> None:
    transport = FakeLiveTransport(empty_live_set(tempo=120.0))
    subject_gate = gate()
    plan = planner().create_session_plan(project_plan(), snapshot_of(transport))
    transport.readback_overrides[plan.operations[0].operation_id] = {}
    token = subject_gate.approve(plan)

    receipt = executor(transport, subject_gate).execute(
        plan, approved=True, approval_token=token
    )

    assert receipt.status == STATUS_VERIFICATION_FAILED
    assert receipt.mismatches[0].observed is None


def test_a_partial_failure_abandons_the_remaining_operations() -> None:
    transport = FakeLiveTransport(empty_live_set(tempo=120.0))
    subject_gate = gate()
    plan = planner().create_session_plan(project_plan(), snapshot_of(transport))
    failing = plan.operations[2].operation_id
    transport.failures[failing] = (ERROR_OPERATION_FAILED, "Live refused the track")
    token = subject_gate.approve(plan)

    receipt = executor(transport, subject_gate).execute(
        plan, approved=True, approval_token=token
    )

    assert receipt.status == STATUS_PARTIALLY_APPLIED
    assert receipt.failed_operation == failing
    assert len(receipt.completed_operations) == 2
    assert len(receipt.attempted_operations) == 3
    assert "will not be retried automatically" in receipt.error
    assert receipt.needs_manual_review is True


def test_a_failure_on_the_first_operation_reports_blocked() -> None:
    transport = FakeLiveTransport(empty_live_set(tempo=120.0))
    subject_gate = gate()
    plan = planner().create_session_plan(project_plan(), snapshot_of(transport))
    transport.failures[plan.operations[0].operation_id] = (
        ERROR_OPERATION_FAILED,
        "Live refused the tempo change",
    )
    token = subject_gate.approve(plan)

    receipt = executor(transport, subject_gate).execute(
        plan, approved=True, approval_token=token
    )

    assert receipt.status == STATUS_BLOCKED
    assert receipt.completed_operations == []


def test_a_mid_run_disconnect_is_reported_as_partially_applied() -> None:
    transport = FakeLiveTransport(empty_live_set(tempo=120.0), disconnect_after=3)
    subject_gate = gate()
    plan = planner().create_session_plan(project_plan(), snapshot_of(transport))
    token = subject_gate.approve(plan)

    receipt = executor(transport, subject_gate).execute(
        plan, approved=True, approval_token=token
    )

    assert receipt.status == STATUS_PARTIALLY_APPLIED
    assert ERROR_DISCONNECTED in receipt.error
    assert len(receipt.completed_operations) == 3


def test_an_unreachable_live_reports_unavailable_and_applies_nothing() -> None:
    class DeadTransport:
        def request(self, message):
            raise LiveTransportError("unavailable", "Live is not running")

    transport = FakeLiveTransport(empty_live_set(tempo=120.0))
    subject_gate = gate()
    plan = planner().create_session_plan(project_plan(), snapshot_of(transport))
    token = subject_gate.approve(plan)
    service = LiveExecutionService(
        transport=DeadTransport(), approval_gate=subject_gate
    )

    receipt = service.execute(plan, approved=True, approval_token=token)

    assert receipt.status == STATUS_UNAVAILABLE
    assert receipt.attempted_operations == []
    assert transport.live_set.tracks == []


def test_a_blocked_plan_is_never_executed() -> None:
    live = empty_live_set(tempo=110.0, available_devices=["EQ Eight"])
    transport = FakeLiveTransport(live)
    subject_gate = gate()
    plan = planner().create_session_plan(project_plan(), snapshot_of(transport))

    receipt = executor(transport, subject_gate).execute(plan, approved=True)

    assert receipt.status == STATUS_BLOCKED
    assert "unresolved conflicts" in receipt.error
    assert transport.applied_operation_ids == []


def test_verify_confirms_a_verified_receipt_against_live() -> None:
    transport = FakeLiveTransport(empty_live_set(tempo=120.0))

    plan, receipt, subject_gate, _ = approved_run(transport)
    confirmed = executor(transport, subject_gate).verify(plan, receipt)

    assert confirmed.status == STATUS_VERIFIED
    assert confirmed.mismatches == []


def test_verify_fails_when_live_no_longer_matches_the_receipt() -> None:
    transport = FakeLiveTransport(empty_live_set(tempo=120.0))

    plan, receipt, subject_gate, _ = approved_run(transport)
    transport.live_set.tracks[0]["name"] = "Someone Renamed This"
    confirmed = executor(transport, subject_gate).verify(plan, receipt)

    assert confirmed.status == STATUS_VERIFICATION_FAILED
    assert any(
        mismatch.observed == "Someone Renamed This"
        for mismatch in confirmed.mismatches
    )


def test_verify_detects_a_user_deleting_a_managed_clip() -> None:
    transport = FakeLiveTransport(empty_live_set(tempo=120.0))

    plan, receipt, subject_gate, _ = approved_run(transport)
    transport.live_set.session_clips.pop((0, 0))
    confirmed = executor(transport, subject_gate).verify(plan, receipt)

    assert confirmed.status == STATUS_VERIFICATION_FAILED


def test_verify_reports_unavailable_when_live_disappears() -> None:
    class DeadTransport:
        def request(self, message):
            raise LiveTransportError("disconnected", "bridge closed")

    transport = FakeLiveTransport(empty_live_set(tempo=120.0))
    plan, receipt, subject_gate, _ = approved_run(transport)
    service = LiveExecutionService(
        transport=DeadTransport(), approval_gate=subject_gate
    )

    confirmed = service.verify(plan, receipt)

    assert confirmed.status == STATUS_UNAVAILABLE


def test_an_existing_set_keeps_user_tracks_clips_and_arrangement_untouched() -> None:
    live = empty_live_set(tempo=110.0)
    live.add_track("User Kick", "midi")
    live.add_scene("My Scene")
    from kihachi_mcp.services.live_transport_fake import (
        FakeArrangementClip,
        FakeSessionClip,
    )

    live.session_clips[(0, 0)] = FakeSessionClip(name="My Loop", length_beats=16.0)
    live.arrangement_clips.append(
        FakeArrangementClip(0, "My Arrangement Clip", 0.0, 64.0, 12)
    )
    transport = FakeLiveTransport(live)

    _, receipt, _, _ = approved_run(transport)

    assert receipt.status == STATUS_VERIFIED
    assert live.tracks[0]["name"] == "User Kick"
    assert live.session_clips[(0, 0)].name == "My Loop"
    assert live.scenes[0]["name"] == "My Scene"
    assert len(live.arrangement_clips) == 1
    assert live.arrangement_clips[0].name == "My Arrangement Clip"


def _tempo_operations(count: int) -> list[LiveMutationOperation]:
    """指定件数のテンポ設定操作を生成する。"""
    return [
        LiveMutationOperation(
            operation_id=f"op-tempo-{idx:04d}",
            op=OP_SET_TEMPO,
            arguments={"tempo": 110.0},
            expected_readback={"tempo": 110.0},
        )
        for idx in range(count)
    ]


def test_plan_with_734_operations_succeeds_without_hitting_safety_limit() -> None:
    """734操作の適用計画が操作数上限で停止せず検証完了すること。"""
    transport = FakeLiveTransport(empty_live_set(tempo=120.0))
    subject_gate = gate()
    base_plan = planner().create_session_plan(project_plan(), snapshot_of(transport))
    plan = replace(base_plan, operations=_tempo_operations(734))
    token = subject_gate.approve(plan)

    receipt = executor(transport, subject_gate).execute(
        plan, approved=True, approval_token=token
    )

    assert receipt.status == STATUS_VERIFIED
    assert len(receipt.completed_operations) == 734
    assert receipt.mismatches == []


def test_plan_over_2048_operations_is_blocked() -> None:
    """2049操作以上の適用計画が従来どおりブロックされエラーメッセージを返すこと。"""
    assert MAX_PLAN_OPERATIONS == 2048
    transport = FakeLiveTransport(empty_live_set(tempo=120.0))
    subject_gate = gate()
    base_plan = planner().create_session_plan(project_plan(), snapshot_of(transport))
    plan = replace(base_plan, operations=_tempo_operations(MAX_PLAN_OPERATIONS + 1))
    token = subject_gate.approve(plan)

    receipt = executor(transport, subject_gate).execute(
        plan, approved=True, approval_token=token
    )

    assert receipt.status == STATUS_BLOCKED
    assert (
        receipt.error
        == f"the plan has {MAX_PLAN_OPERATIONS + 1} operations, over the {MAX_PLAN_OPERATIONS} operation safety limit"
    )
    assert transport.applied_operation_ids == []


def test_resolve_structural_pacing_seconds_reads_env_and_clamps() -> None:
    """構造変更操作ペーシング秒数の環境変数解決・フォールバック・クランプを検証する。"""
    from kihachi_mcp.services.live_execution_service import (
        MAX_STRUCTURAL_PACING_SECONDS,
        STRUCTURAL_PACING_ENV,
        STUDIO_STRUCTURAL_PACING_SECONDS,
        resolve_structural_pacing_seconds,
    )

    assert (
        resolve_structural_pacing_seconds(environ={})
        == STUDIO_STRUCTURAL_PACING_SECONDS
    )
    assert (
        resolve_structural_pacing_seconds(environ={STRUCTURAL_PACING_ENV: "0.25"})
        == 0.25
    )
    assert (
        resolve_structural_pacing_seconds(environ={STRUCTURAL_PACING_ENV: "0"}) == 0.0
    )
    assert (
        resolve_structural_pacing_seconds(environ={STRUCTURAL_PACING_ENV: "bad"})
        == STUDIO_STRUCTURAL_PACING_SECONDS
    )
    assert (
        resolve_structural_pacing_seconds(environ={STRUCTURAL_PACING_ENV: "-1.0"}) == 0.0
    )
    assert (
        resolve_structural_pacing_seconds(environ={STRUCTURAL_PACING_ENV: "99.0"})
        == MAX_STRUCTURAL_PACING_SECONDS
    )


def test_structural_pacing_sleeps_only_after_non_final_structural_ops() -> None:
    """構造変更操作の直後（末尾除く）にのみペーシング待機が入り、非構造操作では待機しないこと。"""
    from kihachi_mcp.models.live_contract import STRUCTURAL_OPS

    transport = FakeLiveTransport(empty_live_set(tempo=120.0))
    subject_gate = gate()
    plan = planner().create_session_plan(project_plan(), snapshot_of(transport))
    token = subject_gate.approve(plan)

    sleeps: list[float] = []
    service = LiveExecutionService(
        transport=transport,
        approval_gate=subject_gate,
        structural_pacing_seconds=0.1,
        sleeper=sleeps.append,
    )
    receipt = service.execute(plan, approved=True, approval_token=token)

    assert receipt.status == STATUS_VERIFIED
    expected_sleeps = sum(
        1
        for op in plan.operations[:-1]
        if op.op in STRUCTURAL_OPS
    )
    assert expected_sleeps > 0
    assert sleeps == [0.1] * expected_sleeps


def test_structural_pacing_stops_immediately_on_timeout_without_retry() -> None:
    """途中でタイムアウトしたら残操作を中断し自動リトライせず余分な待機もしないこと。"""
    from kihachi_mcp.services.live_transport import ERROR_TIMEOUT

    inner = FakeLiveTransport(empty_live_set(tempo=120.0))
    subject_gate = gate()
    plan = planner().create_session_plan(project_plan(), snapshot_of(inner))
    token = subject_gate.approve(plan)

    class TimeoutOnSecondApply:
        def __init__(self) -> None:
            self.apply_calls = 0

        def request(self, message: dict) -> dict:
            if message.get("method") == "apply_operation":
                self.apply_calls += 1
                if self.apply_calls == 2:
                    raise LiveTransportError(
                        ERROR_TIMEOUT,
                        "the Max for Live device did not reply within 60s",
                    )
            return inner.request(message)

    wrapped = TimeoutOnSecondApply()
    sleeps: list[float] = []
    service = LiveExecutionService(
        transport=wrapped,
        approval_gate=subject_gate,
        structural_pacing_seconds=0.1,
        sleeper=sleeps.append,
    )
    receipt = service.execute(plan, approved=True, approval_token=token)

    assert receipt.status == STATUS_PARTIALLY_APPLIED
    assert len(receipt.completed_operations) == 1
    assert receipt.failed_operation == plan.operations[1].operation_id
    assert "timeout:" in receipt.error
    assert wrapped.apply_calls == 2
    # 1件目が非構造（set_tempo）なら 0回、構造なら 1回のみ（失敗後の余分な待機なし）
    assert len(sleeps) <= 1
