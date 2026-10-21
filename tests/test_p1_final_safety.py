"""P1 Final Safety Test Suite: Dry Run TTL, Target Confirmation, and Partial Apply Recovery."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

# Ensure kihachi_mcp is importable
TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from live_fixtures import gate

from kihachi_mcp.models.midi_candidate import MidiCandidate
from kihachi_mcp.services.brief_parser import extract_explicit
from kihachi_mcp.services.live_execution_service import LiveExecutionService
from kihachi_mcp.services.live_state_inspector import LiveStateInspector
from kihachi_mcp.services.live_transport import (
    METHOD_APPLY_OPERATION,
    LiveTransportError,
)
from kihachi_mcp.services.live_transport_fake import (
    FakeArrangementClip,
    FakeLiveSet,
    FakeLiveTransport,
)
from kihachi_mcp.services.midi_candidate_builder import build_candidate
from kihachi_mcp.services.studio_interpreter import assemble_brief
from kihachi_mcp.services.studio_runtime import StudioRuntime


class MockClock:
    """Controllable clock for TTL boundary tests."""

    def __init__(self, start_time: float = 1000000.0) -> None:
        self.time = start_time

    def __call__(self) -> float:
        return self.time

    def advance(self, seconds: float) -> None:
        self.time += seconds


def _make_candidate(
    candidate_id: str = "p1safety123456",
    tempo: int = 120,
    bars: int = 32,
    seed: int = 42,
) -> MidiCandidate:
    brief = assemble_brief(
        extract_explicit(f"{bars}小節 {tempo} BPM Dマイナー。17小節目からドロップにして"),
        {
            "genre": "tech_house",
            "tempo": tempo,
            "bars": bars,
            "key": "Dm",
            "mood": "暗い",
            "hats_first_half": "sparse",
            "hats_second_half": "dense",
            "bass_register": "low",
            "note_density": "normal",
            "drop_start_bar": 17,
            "unhandled": [],
            "ambiguous": [],
        },
    )
    return build_candidate(brief, seed=seed, candidate_id=candidate_id)


def _setup_runtime(
    tmp_path: Path,
    *,
    live_connected: bool = True,
    is_playing: bool = False,
    tempo: float = 120.0,
    set_name: str = "Mutant Dub Circuit",
    track_count: int = 0,
    clock: Any | None = None,
    candidate: MidiCandidate | None = None,
) -> tuple[StudioRuntime, FakeLiveSet, FakeLiveTransport, MidiCandidate]:
    live_set = FakeLiveSet(
        live_version="12.4.3",
        tempo=tempo,
        set_name=set_name,
        is_playing=is_playing,
    )
    for i in range(track_count):
        live_set.add_track(f"Existing Track {i}", track_type="midi")

    transport = FakeLiveTransport(live_set if live_connected else None)
    inspector = LiveStateInspector(transport)
    approvals = gate(tmp_path)
    executor = LiveExecutionService(
        transport=transport, inspector=inspector, approval_gate=approvals
    )
    cand_dir = tmp_path / "candidates"
    cand_dir.mkdir(parents=True, exist_ok=True)
    runtime = StudioRuntime(
        transport=transport if live_connected else None,
        inspector=inspector if live_connected else None,
        executor=executor,
        gate=approvals,
        export_dir=tmp_path / "exports",
        candidate_dir=cand_dir,
        clock=clock,
    )
    cand = candidate or _make_candidate()
    runtime._store(cand)
    return runtime, live_set, transport, cand


# -----------------------------------------------------------------------------
# Task 1: Dry Run Expiration & Boundary Tests
# -----------------------------------------------------------------------------
def test_p1_dry_run_expired_refused(tmp_path: Path) -> None:
    """Task 1: Dry Run expires after 5 minutes (300s); write must be refused."""
    clock = MockClock(1000.0)
    runtime, _live_set, _, candidate = _setup_runtime(tmp_path, clock=clock)
    cid = candidate.candidate_id
    runtime.approve(cid)

    # 1. Perform Dry Run at t = 1000.0
    dry = runtime.dry_run(cid)
    assert dry["safety_verdict"] == "PASS"

    # 2. Advance time past 300 seconds (301s)
    clock.advance(301.0)

    # 3. Attempt send -> must be refused due to expiration
    res = runtime.send_to_ableton(cid, confirmed=True)
    assert res["ok"] is False
    assert "有効期限（5分）が切れています" in res["error"]

    # 4. Record must be invalidated
    assert cid not in runtime._dry_run_records


def test_p1_dry_run_ttl_boundary_conditions(tmp_path: Path) -> None:
    """Task 1: Test TTL exact boundaries: 299s (valid), 300s (valid), 301s (expired), negative (reversed)."""
    # Case A: 299 seconds -> Valid
    clock_a = MockClock(1000.0)
    runtime_a, _, _, cand_a = _setup_runtime(tmp_path / "a", clock=clock_a)
    runtime_a.approve(cand_a.candidate_id)
    runtime_a.dry_run(cand_a.candidate_id)
    clock_a.advance(299.0)
    res_a = runtime_a.send_to_ableton(cand_a.candidate_id, confirmed=True)
    assert res_a["ok"] is True

    # Case B: 300.0 seconds (exact boundary) -> Valid
    clock_b = MockClock(1000.0)
    runtime_b, _, _, cand_b = _setup_runtime(tmp_path / "b", clock=clock_b)
    runtime_b.approve(cand_b.candidate_id)
    runtime_b.dry_run(cand_b.candidate_id)
    clock_b.advance(300.0)
    res_b = runtime_b.send_to_ableton(cand_b.candidate_id, confirmed=True)
    assert res_b["ok"] is True

    # Case C: 300.01 seconds -> Expired
    clock_c = MockClock(1000.0)
    runtime_c, _, _, cand_c = _setup_runtime(tmp_path / "c", clock=clock_c)
    runtime_c.approve(cand_c.candidate_id)
    runtime_c.dry_run(cand_c.candidate_id)
    clock_c.advance(300.01)
    res_c = runtime_c.send_to_ableton(cand_c.candidate_id, confirmed=True)
    assert res_c["ok"] is False
    assert "有効期限" in res_c["error"]

    # Case D: Time went backwards (clock skew / tampering) -> Refused
    clock_d = MockClock(1000.0)
    runtime_d, _, _, cand_d = _setup_runtime(tmp_path / "d", clock=clock_d)
    runtime_d.approve(cand_d.candidate_id)
    runtime_d.dry_run(cand_d.candidate_id)
    clock_d.advance(-5.0)  # t = 995.0
    res_d = runtime_d.send_to_ableton(cand_d.candidate_id, confirmed=True)
    assert res_d["ok"] is False
    assert "時刻の逆行" in res_d["error"]


# -----------------------------------------------------------------------------
# Task 2: Live Set Target Confirmation When set_name is UNKNOWN
# -----------------------------------------------------------------------------
def test_p1_unknown_live_set_requires_confirmation(tmp_path: Path) -> None:
    """Task 2: When Live Set name is UNKNOWN, explicit target confirmation is mandatory."""
    runtime, _live_set, _, candidate = _setup_runtime(tmp_path, set_name="")
    cid = candidate.candidate_id
    runtime.approve(cid)

    # 1. Dry Run indicates UNKNOWN set name and requires_target_confirmation
    dry = runtime.dry_run(cid)
    assert dry["diff_report"]["live_set_name"] == "UNKNOWN"
    assert dry["diff_report"]["requires_target_confirmation"] is True
    assert dry["diff_report"]["target_confirmed"] is False

    # 2. Attempt send without target confirmation -> must be refused
    res = runtime.send_to_ableton(cid, confirmed=True, target_confirmed=False)
    assert res["ok"] is False
    assert "Live Set名がUNKNOWNです" in res["error"]
    assert res.get("requires_target_confirmation") is True

    # 3. Explicitly confirm target Live Set
    conf_res = runtime.confirm_target_live_set(cid)
    assert conf_res["ok"] is True
    assert conf_res["target_confirmed"] is True

    # 4. Now send should succeed
    res2 = runtime.send_to_ableton(cid, confirmed=True)
    assert res2["ok"] is True


def test_p1_live_fingerprint_change_invalidates_target_confirmation(tmp_path: Path) -> None:
    """Task 2: Changing Live Set fingerprint invalidates prior target confirmation."""
    runtime, live_set, _, candidate = _setup_runtime(tmp_path, set_name="")
    cid = candidate.candidate_id
    runtime.approve(cid)

    # 1. Dry Run & Target Confirm
    runtime.dry_run(cid)
    runtime.confirm_target_live_set(cid)
    snap, _ = runtime._snapshot()
    assert runtime.is_target_confirmed(cid, snap.set_fingerprint) is True

    # 2. Change Live state (tempo)
    live_set.tempo = 128.0

    # 3. Attempt send -> must refuse and invalidate confirmation
    res = runtime.send_to_ableton(cid, confirmed=True)
    assert res["ok"] is False
    assert "Live Setの状態または接続先が変更されました" in res["error"]
    assert cid not in runtime._target_confirmed_records


# -----------------------------------------------------------------------------
# Task 3: Partial Apply & Recovery Report
# -----------------------------------------------------------------------------
def test_p1_partial_application_handling_and_recovery_report(tmp_path: Path) -> None:
    """Task 3: Simulate mid-application failure, verify PARTIAL transition and recovery report."""
    runtime, _live_set, transport, candidate = _setup_runtime(tmp_path)
    cid = candidate.candidate_id
    runtime.approve(cid)

    dry = runtime.dry_run(cid)
    assert dry["safety_verdict"] == "PASS"

    # Monkeypatch transport.request to fail on the 3rd apply_operation payload
    original_request = transport.request
    op_counter = {"count": 0}

    def failing_request(payload: Any, *args: Any, **kwargs: Any) -> dict[str, Any]:
        if isinstance(payload, dict) and payload.get("method") == METHOD_APPLY_OPERATION:
            op_counter["count"] += 1
            if op_counter["count"] >= 3:
                raise LiveTransportError("live_dropped", "Live connection dropped mid-operation")
        return original_request(payload, *args, **kwargs)

    transport.request = failing_request

    # Send to Ableton
    res = runtime.send_to_ableton(cid, confirmed=True)

    # 1. Verify delivery status transitioned to PARTIAL
    assert res["delivery_status"] == "PARTIAL"
    assert res["partial"] is True

    # 2. Verify recovery diff report was generated
    assert "recovery_diff_report" in res
    report = res["recovery_diff_report"]
    assert report["status"] == "PARTIAL"
    assert report["candidate_id"] == cid
    assert report["completed_operations_count"] >= 1
    assert "Live connection dropped mid-operation" in report["error"]
    assert "自動ロールバックや削除は行いません" in report["manual_recovery_guide"]

    # 3. Verify get_delivery_status reflects PARTIAL
    audit = runtime.get_delivery_status(cid)
    assert audit["delivery_status"] == "PARTIAL"

    # 4. Verify auto-retry / duplicate send is strictly blocked
    res_retry = runtime.send_to_ableton(cid, confirmed=True)
    assert res_retry["ok"] is False
    assert (
        "すでに送信を試行済みです" in res_retry["error"]
        or "Live Setの状態または接続先が変更されました" in res_retry["error"]
        or "すでに適用を試行済みです" in res_retry["error"]
    )


def test_p1_existing_clips_protected_during_recovery(tmp_path: Path) -> None:
    """Task 3 & Safety: Verify existing tracks & clips remain completely intact after partial failure."""
    runtime, live_set, transport, candidate = _setup_runtime(tmp_path, track_count=3)
    cid = candidate.candidate_id

    # Place user clip on track 0
    live_set.arrangement_clips.append(
        FakeArrangementClip(
            track_index=0,
            name="Important User Audio",
            start_beats=0.0,
            length_beats=64.0,
        )
    )
    runtime.approve(cid)
    runtime.dry_run(cid)

    # Fail on 1st operation payload
    original_request = transport.request

    def fail_immediate(payload: Any, *args: Any, **kwargs: Any) -> dict[str, Any]:
        if isinstance(payload, dict) and payload.get("method") == METHOD_APPLY_OPERATION:
            raise LiveTransportError("fail_now", "Immediate failure")
        return original_request(payload, *args, **kwargs)

    # Apply fails
    transport.request = fail_immediate
    runtime.send_to_ableton(cid, confirmed=True)

    # Verify user clip was NEVER deleted or modified
    assert len(live_set.arrangement_clips) == 1
    assert live_set.arrangement_clips[0].name == "Important User Audio"
    assert live_set.arrangement_clips[0].start_beats == 0.0
    assert live_set.arrangement_clips[0].length_beats == 64.0
