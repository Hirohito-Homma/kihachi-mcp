"""Arrangement expansion guarantees: verified session first, free ranges only."""

from live_fixtures import (
    approved_run,
    empty_live_set,
    executor,
    fixed_clock,
    gate,
    project_plan,
    snapshot_of,
)

from kihachi_mcp.models import Arrangement
from kihachi_mcp.models.live_contract import (
    OP_CREATE_LOCATOR,
    OP_PLACE_ARRANGEMENT_CLIP,
    STATUS_APPROVAL_REQUIRED,
    STATUS_BLOCKED,
    STATUS_VERIFIED,
)
from kihachi_mcp.models.live_receipt import LiveExecutionReceipt
from kihachi_mcp.services.arrangement_expander import (
    ArrangementExpander,
    bar_to_beats,
    bars_to_beats,
)
from kihachi_mcp.services.live_transport_fake import (
    FakeArrangementClip,
    FakeLiveTransport,
)


def _expander() -> ArrangementExpander:
    counter = {"value": 0}

    def next_request_id() -> str:
        counter["value"] += 1
        return f"arr-{counter['value']:04d}"

    return ArrangementExpander(
        request_id_factory=next_request_id, clock=fixed_clock()
    )


def _verified_session(tempo: float = 120.0):
    transport = FakeLiveTransport(empty_live_set(tempo=tempo))
    plan, receipt, subject_gate, _ = approved_run(transport)
    return transport, plan, receipt, subject_gate


def test_bar_positions_convert_deterministically() -> None:
    assert bar_to_beats(1, 4.0) == 0.0
    assert bar_to_beats(17, 4.0) == 64.0
    assert bar_to_beats(113, 4.0) == 448.0
    assert bars_to_beats(16, 4.0) == 64.0
    assert bar_to_beats(17, 3.0) == 48.0
    assert bars_to_beats(16, 3.0) == 48.0


def test_a_verified_session_expands_into_locators_and_clips() -> None:
    transport, _, receipt, _ = _verified_session()

    plan = _expander().create_arrangement_plan(
        project_plan(), snapshot_of(transport), receipt
    )
    ops = [operation.op for operation in plan.operations]

    assert receipt.status == STATUS_VERIFIED
    assert plan.status == STATUS_APPROVAL_REQUIRED
    assert plan.conflicts == []
    assert ops.count(OP_CREATE_LOCATOR) == 2
    assert ops.count(OP_PLACE_ARRANGEMENT_CLIP) == 4
    assert plan.destructive_operation_count == 0


def test_arrangement_clips_are_planned_before_locators() -> None:
    transport, _, receipt, _ = _verified_session()

    plan = _expander().create_arrangement_plan(
        project_plan(), snapshot_of(transport), receipt
    )
    ops = [operation.op for operation in plan.operations]
    first_locator = ops.index(OP_CREATE_LOCATOR)

    assert all(op == OP_PLACE_ARRANGEMENT_CLIP for op in ops[:first_locator])
    assert all(op == OP_CREATE_LOCATOR for op in ops[first_locator:])


def test_placements_use_the_planned_bar_grid() -> None:
    transport, _, receipt, _ = _verified_session()

    plan = _expander().create_arrangement_plan(
        project_plan(), snapshot_of(transport), receipt
    )
    placements = [
        (
            operation.target["track_index"],
            operation.arguments["start_beats"],
            operation.arguments["length_beats"],
        )
        for operation in plan.operations
        if operation.op == OP_PLACE_ARRANGEMENT_CLIP
    ]

    assert (0, 0.0, 64.0) in placements
    assert (0, 64.0, 128.0) in placements
    assert (1, 0.0, 64.0) in placements
    assert (1, 64.0, 128.0) in placements


def test_an_unverified_session_blocks_expansion() -> None:
    transport, _, receipt, _ = _verified_session()
    unverified = LiveExecutionReceipt(
        request_id=receipt.request_id,
        status="partially_applied",
        attempted_operations=list(receipt.attempted_operations),
        completed_operations=list(receipt.completed_operations)[:1],
    )

    plan = _expander().create_arrangement_plan(
        project_plan(), snapshot_of(transport), unverified
    )

    assert plan.status == STATUS_BLOCKED
    assert plan.operations == []
    assert plan.conflicts[0].kind == "session_not_verified"
    assert "not 'verified'" in plan.conflicts[0].detail


def test_a_verification_failed_session_blocks_expansion() -> None:
    transport, _, receipt, _ = _verified_session()
    failed = LiveExecutionReceipt(
        request_id=receipt.request_id,
        status="verification_failed",
        attempted_operations=list(receipt.attempted_operations),
        completed_operations=list(receipt.completed_operations),
    )

    plan = _expander().create_arrangement_plan(
        project_plan(), snapshot_of(transport), failed
    )

    assert plan.status == STATUS_BLOCKED
    assert plan.conflicts[0].kind == "session_not_verified"


def test_an_occupied_arrangement_range_blocks_expansion() -> None:
    transport, _, receipt, _ = _verified_session()
    transport.live_set.arrangement_clips.append(
        FakeArrangementClip(0, "User Intro Take", 0.0, 32.0, 24)
    )

    plan = _expander().create_arrangement_plan(
        project_plan(), snapshot_of(transport), receipt
    )

    assert plan.status == STATUS_BLOCKED
    assert plan.operations == []
    occupied = [
        conflict
        for conflict in plan.conflicts
        if conflict.kind == "arrangement_range_occupied"
    ]
    assert occupied
    assert "User Intro Take" in occupied[0].detail
    assert "will not move or trim" in occupied[0].detail


def test_a_range_adjacent_to_an_existing_clip_is_allowed() -> None:
    transport, _, receipt, _ = _verified_session()
    transport.live_set.arrangement_clips.append(
        FakeArrangementClip(0, "User Outro", 192.0, 64.0, 24)
    )

    plan = _expander().create_arrangement_plan(
        project_plan(), snapshot_of(transport), receipt
    )

    assert plan.status == STATUS_APPROVAL_REQUIRED
    assert plan.conflicts == []


def test_overlapping_sections_in_one_request_are_refused() -> None:
    transport, _, receipt, _ = _verified_session()

    plan = _expander().create_arrangement_plan(
        project_plan(
            sections=[
                Arrangement(name="Intro", start_bar=1, length_bars=16),
                Arrangement(name="Drop", start_bar=8, length_bars=32),
            ]
        ),
        snapshot_of(transport),
        receipt,
    )

    assert plan.status == STATUS_BLOCKED
    assert any(
        conflict.kind == "arrangement_range_occupied"
        for conflict in plan.conflicts
    )


def test_expansion_is_refused_when_the_session_layout_is_missing() -> None:
    transport = FakeLiveTransport(empty_live_set(tempo=110.0))
    receipt = LiveExecutionReceipt(
        request_id="req-1",
        status=STATUS_VERIFIED,
        attempted_operations=["a"],
        completed_operations=["a"],
    )

    plan = _expander().create_arrangement_plan(
        project_plan(), snapshot_of(transport), receipt
    )

    assert plan.status == STATUS_BLOCKED
    assert plan.conflicts[0].kind == "missing_track"


def test_recording_blocks_expansion() -> None:
    transport, _, receipt, _ = _verified_session()
    transport.live_set.is_recording = True

    plan = _expander().create_arrangement_plan(
        project_plan(), snapshot_of(transport), receipt
    )

    assert plan.status == STATUS_BLOCKED
    assert plan.conflicts[0].kind == "live_is_recording"


def test_an_approved_arrangement_plan_executes_and_verifies() -> None:
    transport, _, receipt, _ = _verified_session()
    arrangement_gate = gate()
    plan = _expander().create_arrangement_plan(
        project_plan(), snapshot_of(transport), receipt
    )
    token = arrangement_gate.approve(plan)

    result = executor(transport, arrangement_gate).execute(
        plan, approved=True, approval_token=token
    )

    assert result.status == STATUS_VERIFIED
    assert len(transport.live_set.arrangement_clips) == 4
    assert len(transport.live_set.locators) == 2
    placed = sorted(
        (clip.track_index, clip.start_beats, clip.length_beats, clip.note_count)
        for clip in transport.live_set.arrangement_clips
    )
    assert placed[0][:3] == (0, 0.0, 64.0)
    assert all(entry[3] > 0 for entry in placed)


def test_a_three_four_set_places_clips_on_a_three_beat_grid() -> None:
    transport, _, receipt, _ = _verified_session()
    transport.live_set.numerator = 3

    plan = _expander().create_arrangement_plan(
        project_plan(), snapshot_of(transport), receipt
    )
    starts = sorted(
        operation.arguments["start_beats"]
        for operation in plan.operations
        if operation.op == OP_PLACE_ARRANGEMENT_CLIP
    )

    assert starts == [0.0, 0.0, 48.0, 48.0]
