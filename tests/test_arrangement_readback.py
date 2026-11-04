"""Re-verification of Arrangement placements against a fresh snapshot.

The generic snapshot verifier used to have no Arrangement branch, so
a place_arrangement_clip operation fell into the track-name check and
every Arrangement receipt re-verified as failed. These tests pin the
dedicated branch, including the device renaming a placed clip with a
fresh managed id (only the ``[K:...]`` suffix changes).
"""

from live_fixtures import (
    approved_run,
    empty_live_set,
    executor,
    project_plan,
    snapshot_of,
)

from kihachi_mcp.models.live_contract import OP_PLACE_ARRANGEMENT_CLIP, STATUS_VERIFIED
from kihachi_mcp.models.live_mutation import LiveMutationOperation
from kihachi_mcp.models.live_state import (
    LiveArrangementClip,
    LiveStateSnapshot,
    LiveTimeSignature,
)
from kihachi_mcp.services.arrangement_expander import ArrangementExpander
from kihachi_mcp.services.live_execution_service import _verify_against_snapshot
from kihachi_mcp.services.live_transport_fake import FakeLiveTransport


def _snapshot(clips: list[LiveArrangementClip]) -> LiveStateSnapshot:
    return LiveStateSnapshot(
        live_version="12.0",
        set_name="Verify Set",
        set_path="",
        tempo=110.0,
        time_signature=LiveTimeSignature(),
        is_playing=False,
        is_recording=False,
        observed_at="2026-01-01T00:00:00+00:00",
        arrangement_clips=clips,
    )


def _place_operation(name: str, length: float) -> LiveMutationOperation:
    return LiveMutationOperation(
        operation_id="001-place_arrangement_clip",
        op=OP_PLACE_ARRANGEMENT_CLIP,
        target={"track_index": 0, "scene_index": 0},
        arguments={"name": name, "start_beats": 0.0, "length_beats": length},
        expected_readback={
            "track_index": 0,
            "name": name,
            "start_beats": 0.0,
            "length_beats": length,
        },
    )


def test_verify_matches_a_renamed_placed_clip() -> None:
    operation = _place_operation("Intro [K:12345678]", 16.0)
    snapshot = _snapshot(
        [
            LiveArrangementClip(
                track_index=0,
                name="Intro [K:abcdef00]",
                start_beats=0.0,
                length_beats=16.0,
            )
        ]
    )

    assert _verify_against_snapshot(operation, snapshot) == []


def test_verify_reports_a_length_difference() -> None:
    operation = _place_operation("Intro [K:12345678]", 32.0)
    snapshot = _snapshot(
        [
            LiveArrangementClip(
                track_index=0,
                name="Intro [K:abcdef00]",
                start_beats=0.0,
                length_beats=16.0,
            )
        ]
    )

    mismatches = _verify_against_snapshot(operation, snapshot)

    assert [(item.field_name, item.expected, item.observed) for item in mismatches] == [
        ("length_beats", 32.0, 16.0)
    ]


def test_verify_reports_a_missing_clip_and_a_wrong_section() -> None:
    operation = _place_operation("Intro [K:12345678]", 16.0)

    missing = _verify_against_snapshot(operation, _snapshot([]))
    assert {item.field_name for item in missing} == {"name", "length_beats"}

    wrong_section = _verify_against_snapshot(
        operation,
        _snapshot(
            [
                LiveArrangementClip(
                    track_index=0,
                    name="Drop [K:abcdef00]",
                    start_beats=0.0,
                    length_beats=16.0,
                )
            ]
        ),
    )
    assert [item.field_name for item in wrong_section] == ["name"]


def test_verify_confirms_an_executed_arrangement_plan() -> None:
    transport = FakeLiveTransport(empty_live_set(tempo=120.0))
    _, session_receipt, approval_gate, _ = approved_run(transport)
    assert session_receipt.status == STATUS_VERIFIED
    plan = ArrangementExpander().create_arrangement_plan(
        project_plan(), snapshot_of(transport), session_receipt
    )
    service = executor(transport, approval_gate)
    receipt = service.execute(plan, True, approval_gate.approve(plan))
    assert receipt.status == STATUS_VERIFIED

    verified = service.verify(plan, receipt)

    assert verified.status == STATUS_VERIFIED
    assert verified.mismatches == []
