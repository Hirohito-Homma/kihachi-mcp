"""Arrangement expansion guarantees: verified session first, free ranges only."""

import pytest
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
    STATUS_UNAVAILABLE,
    STATUS_VERIFICATION_FAILED,
    STATUS_VERIFIED,
)
from kihachi_mcp.models.live_mutation import LiveMutationOperation, LiveMutationPlan
from kihachi_mcp.models.live_receipt import LiveExecutionReceipt
from kihachi_mcp.models.live_state import LiveLocator, LiveStateSnapshot
from kihachi_mcp.services.arrangement_expander import (
    ArrangementExpander,
    bar_to_beats,
    bars_to_beats,
)
from kihachi_mcp.services.live_execution_service import (
    AMBIGUOUS_MATCH_FIELD,
    LOCATORS_UNREPORTED_FIELD,
    readback_mismatches,
)
from kihachi_mcp.services.live_transport import ERROR_DISCONNECTED, LiveTransportError
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


def test_an_occupied_arrangement_range_never_sends_mutations() -> None:
    transport, _, receipt, approval_gate = _verified_session()
    existing = FakeArrangementClip(0, "User Intro Take", 0.0, 32.0, 24)
    transport.live_set.arrangement_clips.append(existing)
    before = list(transport.live_set.arrangement_clips)
    applied_before = list(transport.applied_operation_ids)
    plan = _expander().create_arrangement_plan(
        project_plan(), snapshot_of(transport), receipt
    )

    result = executor(transport, approval_gate).execute(
        plan, approved=True, approval_token="unused"
    )

    assert result.status == STATUS_BLOCKED
    assert transport.applied_operation_ids == applied_before
    assert transport.live_set.arrangement_clips == before
    assert transport.live_set.locators == []


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

    second_readback = executor(transport, arrangement_gate).verify(plan, result)

    assert second_readback.status == STATUS_VERIFIED


def test_arrangement_second_readback_detects_a_changed_clip() -> None:
    transport, _, receipt, _ = _verified_session()
    arrangement_gate = gate()
    plan = _expander().create_arrangement_plan(
        project_plan(), snapshot_of(transport), receipt
    )
    token = arrangement_gate.approve(plan)
    service = executor(transport, arrangement_gate)
    result = service.execute(plan, approved=True, approval_token=token)
    transport.live_set.arrangement_clips[0].name = "Unexpected clip name"

    second_readback = service.verify(plan, result)

    assert second_readback.status == STATUS_VERIFICATION_FAILED
    assert any(
        mismatch.field_name == "name"
        for mismatch in second_readback.mismatches
    )


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


def _executed_arrangement():
    transport, _, receipt, _ = _verified_session()
    arrangement_gate = gate()
    plan = _expander().create_arrangement_plan(
        project_plan(), snapshot_of(transport), receipt
    )
    token = arrangement_gate.approve(plan)
    service = executor(transport, arrangement_gate)
    result = service.execute(plan, approved=True, approval_token=token)
    assert result.status == STATUS_VERIFIED
    return transport, plan, result, service


def _locator_mismatches(receipt: LiveExecutionReceipt, plan) -> list[tuple]:
    locator_ids = {
        operation.operation_id
        for operation in plan.operations
        if operation.op == OP_CREATE_LOCATOR
    }
    return [
        (item.field_name, item.expected, item.observed)
        for item in receipt.mismatches
        if item.operation_id in locator_ids
    ]


def test_arrangement_second_readback_confirms_each_locator() -> None:
    transport, plan, result, service = _executed_arrangement()

    second_readback = service.verify(plan, result)

    assert second_readback.status == STATUS_VERIFIED
    assert snapshot_of(transport).locators == [
        LiveLocator(name=locator["name"], beats=locator["beats"])
        for locator in transport.live_set.locators
    ]
    assert len(transport.live_set.locators) == 2


def test_arrangement_second_readback_reports_unavailable_without_resending() -> None:
    transport, plan, receipt, service = _executed_arrangement()
    completed_before = list(transport.applied_operation_ids)

    def disconnected(_message):
        raise LiveTransportError(ERROR_DISCONNECTED, "Live bridge closed")

    transport.request = disconnected
    checked = service.verify(plan, receipt)

    assert checked.status == STATUS_UNAVAILABLE
    assert checked.completed_operations == receipt.completed_operations
    assert checked.error == "disconnected: Live bridge closed"
    assert transport.applied_operation_ids == completed_before


def test_arrangement_second_readback_detects_a_renamed_locator() -> None:
    transport, plan, result, service = _executed_arrangement()
    original = transport.live_set.locators[1]["name"]
    transport.live_set.locators[1]["name"] = "Renamed by hand"

    second_readback = service.verify(plan, result)

    assert second_readback.status == STATUS_VERIFICATION_FAILED
    assert _locator_mismatches(second_readback, plan) == [
        ("name", original, "Renamed by hand")
    ]


def test_arrangement_second_readback_detects_a_moved_locator() -> None:
    transport, plan, result, service = _executed_arrangement()
    original = transport.live_set.locators[1]["beats"]
    transport.live_set.locators[1]["beats"] = original + 4.0

    second_readback = service.verify(plan, result)

    assert second_readback.status == STATUS_VERIFICATION_FAILED
    assert _locator_mismatches(second_readback, plan) == [
        ("beats", original, original + 4.0)
    ]


def test_arrangement_second_readback_detects_a_deleted_locator() -> None:
    transport, plan, result, service = _executed_arrangement()
    removed = transport.live_set.locators.pop(1)

    second_readback = service.verify(plan, result)

    assert second_readback.status == STATUS_VERIFICATION_FAILED
    assert _locator_mismatches(second_readback, plan) == [
        ("name", removed["name"], None),
        ("beats", removed["beats"], None),
    ]


def test_a_device_that_reports_no_locators_never_confirms_them() -> None:
    transport, plan, result, service = _executed_arrangement()
    transport.reports_locators = False

    second_readback = service.verify(plan, result)

    assert second_readback.status == STATUS_VERIFICATION_FAILED
    assert _locator_mismatches(second_readback, plan) == [
        (LOCATORS_UNREPORTED_FIELD, True, False),
        (LOCATORS_UNREPORTED_FIELD, True, False),
    ]
    assert "did not report locators" in second_readback.error


def test_unreported_locators_differ_from_an_empty_list() -> None:
    transport = FakeLiveTransport(empty_live_set())
    payload = transport.snapshot_payload()
    without_key = {key: value for key, value in payload.items() if key != "locators"}

    assert LiveStateSnapshot.from_dict(payload).locators == []
    assert LiveStateSnapshot.from_dict(without_key).locators is None
    assert transport.snapshot_payload({"include_arrangement": False}).get(
        "locators"
    ) is None


def test_locators_do_not_change_the_set_fingerprint() -> None:
    transport = FakeLiveTransport(empty_live_set())
    before = snapshot_of(transport).set_fingerprint
    transport.live_set.locators.append({"name": "Intro [KIHACHI]", "beats": 0.0})

    assert snapshot_of(transport).set_fingerprint == before


_ABSENT = object()


def _snapshot_with_locators(locators) -> LiveStateSnapshot:
    payload = FakeLiveTransport(empty_live_set()).snapshot_payload()
    payload.pop("locators", None)
    if locators is not _ABSENT:
        payload["locators"] = locators
    return LiveStateSnapshot.from_dict(payload)


@pytest.mark.parametrize(
    "locators",
    [
        pytest.param(_ABSENT, id="key-missing"),
        pytest.param(None, id="null"),
        pytest.param({"name": "Intro", "beats": 0.0}, id="top-level-dict"),
        pytest.param("Intro", id="top-level-string"),
        pytest.param([None], id="null-entry"),
        pytest.param([{}], id="empty-entry"),
        pytest.param(["Intro"], id="string-entry"),
        pytest.param([{"name": "Intro"}], id="beats-missing"),
        pytest.param([{"beats": 0.0}], id="name-missing"),
        pytest.param([{"name": None, "beats": 0.0}], id="name-null"),
        pytest.param([{"name": 1, "beats": 0.0}], id="name-number"),
        pytest.param([{"name": "Intro", "beats": "64"}], id="beats-numeric-string"),
        pytest.param([{"name": "Intro", "beats": "bad"}], id="beats-text"),
        pytest.param([{"name": "Intro", "beats": None}], id="beats-null"),
        pytest.param([{"name": "Intro", "beats": True}], id="beats-bool"),
        pytest.param([{"name": "Intro", "beats": float("nan")}], id="beats-nan"),
        pytest.param([{"name": "Intro", "beats": float("inf")}], id="beats-inf"),
        pytest.param([{"name": "Intro", "beats": float("-inf")}], id="beats-neg-inf"),
        pytest.param(
            [{"name": "Intro", "beats": 0.0}, None], id="valid-then-null"
        ),
        pytest.param(
            [{"name": "Intro", "beats": 0.0}, {"name": "Drop", "beats": "64"}],
            id="valid-then-bad-beats",
        ),
    ],
)
def test_a_malformed_locator_list_is_treated_as_unreported(locators) -> None:
    assert _snapshot_with_locators(locators).locators is None


def test_a_valid_empty_locator_list_stays_empty() -> None:
    assert _snapshot_with_locators([]).locators == []


def test_valid_locators_are_read_in_order() -> None:
    snapshot = _snapshot_with_locators(
        [
            {"name": "Intro [KIHACHI]", "beats": 0},
            {"name": "Drop [KIHACHI]", "beats": 64.0},
            {"name": "", "beats": 128.5},
        ]
    )

    assert snapshot.locators == [
        LiveLocator(name="Intro [KIHACHI]", beats=0.0),
        LiveLocator(name="Drop [KIHACHI]", beats=64.0),
        LiveLocator(name="", beats=128.5),
    ]


@pytest.mark.parametrize(
    "corrupt",
    [
        pytest.param(lambda locators: [None], id="null-entry"),
        pytest.param(
            lambda locators: [*locators, {"name": "X", "beats": "bad"}],
            id="valid-plus-bad-beats",
        ),
        pytest.param(
            lambda locators: [
                {**locators[0], "beats": float("nan")},
                *locators[1:],
            ],
            id="nan-beats",
        ),
    ],
)
def test_a_malformed_locator_reply_never_confirms_a_locator(corrupt) -> None:
    transport, plan, result, service = _executed_arrangement()
    honest = transport.snapshot_payload

    def corrupted(options=None):
        payload = honest(options)
        payload["locators"] = corrupt(payload["locators"])
        return payload

    transport.snapshot_payload = corrupted

    second_readback = service.verify(plan, result)

    assert second_readback.status == STATUS_VERIFICATION_FAILED
    assert _locator_mismatches(second_readback, plan) == [
        (LOCATORS_UNREPORTED_FIELD, True, False),
        (LOCATORS_UNREPORTED_FIELD, True, False),
    ]
    assert "did not report locators" in second_readback.error


def _two_drop_locators():
    operations = [
        LiveMutationOperation(
            operation_id=f"00{index}-create_locator",
            op=OP_CREATE_LOCATOR,
            target={"scope": "arrangement"},
            arguments={"name": "Drop abcd1234 [KIHACHI]", "beats": beats},
            expected_readback={"name": "Drop abcd1234 [KIHACHI]", "beats": beats},
        )
        for index, beats in ((1, 64.0), (2, 192.0))
    ]
    plan = LiveMutationPlan(
        request_id="arr-dup",
        idempotency_key="key",
        source_plan_hash="hash",
        set_fingerprint="fp",
        expires_at="2026-01-01T00:15:00+00:00",
        operations=operations,
    )
    ids = [operation.operation_id for operation in operations]
    receipt = LiveExecutionReceipt(
        request_id="arr-dup",
        status=STATUS_VERIFIED,
        attempted_operations=ids,
        completed_operations=ids,
    )
    return plan, receipt


def _with_locators(locators) -> LiveStateSnapshot:
    payload = FakeLiveTransport(empty_live_set()).snapshot_payload()
    payload["locators"] = locators
    return LiveStateSnapshot.from_dict(payload)


def test_same_named_locators_pass_only_at_their_own_positions() -> None:
    plan, receipt = _two_drop_locators()
    snapshot = _with_locators(
        [
            {"name": "Drop abcd1234 [KIHACHI]", "beats": 192.0},
            {"name": "Drop abcd1234 [KIHACHI]", "beats": 64.0},
        ]
    )

    assert readback_mismatches(plan, receipt, snapshot) == []


def test_a_deleted_locator_is_not_matched_to_its_same_named_twin() -> None:
    plan, receipt = _two_drop_locators()
    snapshot = _with_locators([{"name": "Drop abcd1234 [KIHACHI]", "beats": 64.0}])

    found = [
        (item.operation_id, item.field_name, item.observed)
        for item in readback_mismatches(plan, receipt, snapshot)
    ]

    assert found == [
        ("002-create_locator", "name", None),
        ("002-create_locator", "beats", None),
    ]


def test_one_live_locator_cannot_confirm_two_planned_locators() -> None:
    plan, receipt = _two_drop_locators()
    snapshot = _with_locators([{"name": "Drop abcd1234 [KIHACHI]", "beats": 128.0}])

    found = readback_mismatches(plan, receipt, snapshot)

    assert {item.operation_id for item in found} == {
        "001-create_locator",
        "002-create_locator",
    }
    assert all(item.observed is None for item in found)


def test_a_duplicated_locator_is_ambiguous() -> None:
    plan, receipt = _two_drop_locators()
    snapshot = _with_locators(
        [
            {"name": "Drop abcd1234 [KIHACHI]", "beats": 64.0},
            {"name": "Drop abcd1234 [KIHACHI]", "beats": 64.0},
            {"name": "Drop abcd1234 [KIHACHI]", "beats": 192.0},
        ]
    )

    found = [
        (item.operation_id, item.field_name, item.expected, item.observed)
        for item in readback_mismatches(plan, receipt, snapshot)
    ]

    assert found == [("001-create_locator", AMBIGUOUS_MATCH_FIELD, 1, 2)]


def test_arrangement_note_counts_are_not_reverified_from_the_summary() -> None:
    """Live's Arrangement summary reports note_count 0; only the first readback counts."""
    transport, plan, result, service = _executed_arrangement()
    for clip in transport.live_set.arrangement_clips:
        clip.note_count = 0

    assert service.verify(plan, result).status == STATUS_VERIFIED
