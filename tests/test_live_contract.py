"""Contract-level guarantees: schema versions, hashing, and path portability."""

import pytest
from live_fixtures import empty_live_set, snapshot_of

from kihachi_mcp.models.live_contract import (
    SCHEMA_VERSION,
    LiveContractError,
    SchemaVersionError,
    canonical_hash,
    is_managed_name,
    managed_clip_name,
    managed_track_name,
)
from kihachi_mcp.models.live_mutation import LiveMutationOperation, LiveMutationPlan
from kihachi_mcp.models.live_paths import normalize_set_path, set_name_from_path
from kihachi_mcp.models.live_receipt import LiveExecutionReceipt
from kihachi_mcp.models.live_state import LiveStateSnapshot
from kihachi_mcp.services.live_transport_fake import FakeLiveTransport


def test_snapshot_json_round_trip_is_stable() -> None:
    original = snapshot_of(FakeLiveTransport(empty_live_set()))

    restored = LiveStateSnapshot.from_dict(original.to_dict())

    assert restored == original
    assert restored.set_fingerprint == original.set_fingerprint
    assert restored.to_dict() == original.to_dict()


def test_plan_json_round_trip_preserves_plan_hash() -> None:
    plan = LiveMutationPlan(
        request_id="req-1",
        idempotency_key="key-1",
        source_plan_hash="abc",
        set_fingerprint="def",
        expires_at="2026-01-01T00:15:00+00:00",
        operations=[
            LiveMutationOperation(
                operation_id="001-set_tempo",
                op="set_tempo",
                arguments={"tempo": 110.0},
                expected_readback={"tempo": 110.0},
            )
        ],
    )

    restored = LiveMutationPlan.from_dict(plan.to_dict())

    assert restored == plan
    assert restored.plan_hash == plan.plan_hash


def test_receipt_json_round_trip() -> None:
    receipt = LiveExecutionReceipt(
        request_id="req-1",
        status="verified",
        attempted_operations=["001-set_tempo"],
        completed_operations=["001-set_tempo"],
    )

    assert LiveExecutionReceipt.from_dict(receipt.to_dict()) == receipt


@pytest.mark.parametrize("bad_version", [0, 2, 99, "one", None])
def test_snapshot_rejects_other_schema_versions(bad_version) -> None:
    payload = snapshot_of(FakeLiveTransport(empty_live_set())).to_dict()
    payload["schema_version"] = bad_version

    with pytest.raises(SchemaVersionError):
        LiveStateSnapshot.from_dict(payload)


def test_plan_rejects_other_schema_versions() -> None:
    with pytest.raises(SchemaVersionError):
        LiveMutationPlan.from_dict({"schema_version": SCHEMA_VERSION + 1})


def test_receipt_rejects_other_schema_versions() -> None:
    with pytest.raises(SchemaVersionError):
        LiveExecutionReceipt.from_dict({"schema_version": 7, "status": "verified"})


def test_unsupported_operation_is_refused_at_construction() -> None:
    with pytest.raises(LiveContractError, match="unsupported Live operation"):
        LiveMutationOperation(operation_id="x", op="delete_everything")


def test_receipt_cannot_be_verified_with_mismatches() -> None:
    with pytest.raises(LiveContractError, match="cannot be verified"):
        LiveExecutionReceipt(
            request_id="req-1",
            status="verified",
            attempted_operations=["a"],
            completed_operations=["a"],
            mismatches=[
                type(
                    "M",
                    (),
                    {
                        "operation_id": "a",
                        "field_name": "tempo",
                        "expected": 1,
                        "observed": 2,
                    },
                )()
            ],
        )


def test_receipt_cannot_be_verified_with_incomplete_operations() -> None:
    with pytest.raises(LiveContractError, match="incomplete operations"):
        LiveExecutionReceipt(
            request_id="req-1",
            status="verified",
            attempted_operations=["a", "b"],
            completed_operations=["a"],
        )


def test_receipt_rejects_unknown_status() -> None:
    with pytest.raises(LiveContractError, match="unsupported execution status"):
        LiveExecutionReceipt(request_id="req-1", status="done")


def test_canonical_hash_ignores_key_order() -> None:
    assert canonical_hash({"a": 1, "b": 2}) == canonical_hash({"b": 2, "a": 1})


def test_ownership_marker_round_trip() -> None:
    assert managed_track_name("Kick") == "Kick [KIHACHI]"
    assert managed_track_name("Kick [KIHACHI]") == "Kick [KIHACHI]"
    assert is_managed_name("Kick [KIHACHI]")
    assert is_managed_name(managed_clip_name("Intro", "abc123"))
    assert not is_managed_name("Kick")


def test_windows_and_macos_set_paths_are_comparable() -> None:
    windows = normalize_set_path(r"c:\Users\me\Music\Ableton\Demo\Demo.als")
    posix = normalize_set_path("/Users/me/Music/Ableton/Demo/Demo.als")

    assert windows == "C:/Users/me/Music/Ableton/Demo/Demo.als"
    assert posix == "/Users/me/Music/Ableton/Demo/Demo.als"
    assert set_name_from_path(windows) == "Demo"
    assert set_name_from_path(posix) == "Demo"
    assert normalize_set_path("") == ""


def test_same_windows_set_spelled_two_ways_has_one_fingerprint() -> None:
    base = snapshot_of(FakeLiveTransport(empty_live_set())).to_dict()
    backslash = dict(base, set_path=r"C:\Users\me\Demo\Demo.als")
    forward = dict(base, set_path="C:/Users/me/Demo/Demo.als")

    assert (
        LiveStateSnapshot.from_dict(backslash).set_fingerprint
        == LiveStateSnapshot.from_dict(forward).set_fingerprint
    )


def test_fingerprint_ignores_transport_state_but_tracks_structure() -> None:
    transport = FakeLiveTransport(empty_live_set())
    baseline = snapshot_of(transport).set_fingerprint

    transport.live_set.is_playing = True
    assert snapshot_of(transport).set_fingerprint == baseline

    transport.live_set.add_track("User Kick")
    assert snapshot_of(transport).set_fingerprint != baseline
