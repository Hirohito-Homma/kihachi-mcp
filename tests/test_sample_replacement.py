from pathlib import Path

from live_fixtures import empty_live_set, snapshot_of

from kihachi_mcp.services.live_approval_gate import ApprovalGate
from kihachi_mcp.services.live_execution_service import LiveExecutionService
from kihachi_mcp.services.live_state_inspector import LiveStateInspector
from kihachi_mcp.services.live_transport_fake import FakeLiveTransport
from kihachi_mcp.services.sample_replacement import create_sample_replacement_plan


def _replacement(tmp_path: Path):
    old = tmp_path / "old-kick.wav"
    new = tmp_path / "heavy-kick.wav"
    old.write_bytes(b"old")
    new.write_bytes(b"new")
    live = empty_live_set(tempo=124)
    track = live.add_track("KIHACHI Kick abcdef12 [KIHACHI]")
    track["device_names"].append("Drum Rack")
    track["occupied_pads"] = [
        {"note": 36, "name": "old-kick", "chain_count": 1, "sample_path": str(old)}
    ]
    transport = FakeLiveTransport(live)
    plan = create_sample_replacement_plan(
        snapshot_of(transport),
        track_index=track["index"],
        note=36,
        current_sample_path=str(old),
        replacement_sample_path=str(new),
    )
    return plan, transport, old, new


def test_replacement_is_one_destructive_approval_gated_operation(tmp_path: Path) -> None:
    plan, _transport, old, new = _replacement(tmp_path)

    assert plan.status == "approval_required"
    assert plan.destructive_operation_count == 1
    operation = plan.operations[0]
    assert operation.arguments == {"note": 36, "sample_path": str(new)}
    assert operation.expected_readback["sample_path"] == str(new)
    assert any(
        condition.kind == "drum_pad_sample_path"
        and condition.arguments["sample_path"] == str(old)
        for condition in operation.preconditions
    )


def test_approved_replacement_changes_only_the_selected_pad(tmp_path: Path) -> None:
    plan, transport, _old, new = _replacement(tmp_path)
    gate = ApprovalGate(tmp_path / "approvals.json")
    executor = LiveExecutionService(
        transport=transport,
        inspector=LiveStateInspector(transport),
        approval_gate=gate,
    )
    token = gate.approve(plan)

    receipt = executor.execute(plan, approved=True, approval_token=token)

    assert receipt.status == "verified"
    pads = transport.live_set.tracks[0]["occupied_pads"]
    assert pads == [
        {"note": 36, "name": "heavy-kick", "chain_count": 1, "sample_path": str(new)}
    ]


def test_changed_current_sample_blocks_stale_approved_plan(tmp_path: Path) -> None:
    plan, transport, _old, _new = _replacement(tmp_path)
    transport.live_set.tracks[0]["occupied_pads"][0]["sample_path"] = "/changed.wav"
    gate = ApprovalGate(tmp_path / "approvals.json")
    executor = LiveExecutionService(
        transport=transport,
        inspector=LiveStateInspector(transport),
        approval_gate=gate,
    )
    token = gate.approve(plan)

    receipt = executor.execute(plan, approved=True, approval_token=token)

    assert receipt.status == "blocked"
    assert transport.live_set.tracks[0]["occupied_pads"][0]["sample_path"] == "/changed.wav"


def test_unmanaged_track_is_never_planned(tmp_path: Path) -> None:
    sample = tmp_path / "kick.wav"
    sample.write_bytes(b"kick")
    live = empty_live_set(tempo=124)
    track = live.add_track("User Kick")
    plan = create_sample_replacement_plan(
        snapshot_of(FakeLiveTransport(live)),
        track_index=track["index"],
        note=36,
        current_sample_path=str(sample),
        replacement_sample_path=str(sample),
    )
    assert plan.status == "blocked"
    assert plan.operations == []
