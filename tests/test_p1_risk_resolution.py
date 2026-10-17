# -*- coding: utf-8 -*-
"""P1 Residual Risk Resolution Test Suite.

Covers:
- Task 1: Target Live Set confirmation API and state invalidation on fingerprint change.
- Task 2: Atomic delivery audit persistence, crash recovery (APPLYING -> UNKNOWN),
          and strict anti-replay protection.
- Task 3: set_fingerprint calculation audit and mutation detection / limitation boundaries.
- Task 4: Error injection for persistence failure, readback recovery without rollback.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Any
from unittest.mock import patch

import pytest

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from live_fixtures import gate

from kihachi_mcp.models.live_contract import managed_track_name
from kihachi_mcp.models.midi_candidate import CandidateClip, MidiCandidate
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
from kihachi_mcp.services.studio_runtime import DRY_RUN_TTL_SECONDS, StudioRuntime


class MockClock:
    def __init__(self, start_time: float = 1000000.0) -> None:
        self.time = start_time

    def __call__(self) -> float:
        return self.time

    def advance(self, seconds: float) -> None:
        self.time += seconds


def _make_candidate(
    candidate_id: str = "riskres123456",
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
    set_name: str = "Mutant Dub Circuit",
    tempo: float = 120.0,
    clock: Any | None = None,
    candidate: MidiCandidate | None = None,
) -> tuple[StudioRuntime, FakeLiveSet, FakeLiveTransport, MidiCandidate]:
    live_set = FakeLiveSet(
        live_version="12.4.3",
        tempo=tempo,
        set_name=set_name,
        is_playing=False,
    )
    transport = FakeLiveTransport(live_set)
    inspector = LiveStateInspector(transport)
    approvals = gate(tmp_path)
    executor = LiveExecutionService(
        transport=transport, inspector=inspector, approval_gate=approvals
    )
    cand_dir = tmp_path / "candidates"
    cand_dir.mkdir(parents=True, exist_ok=True)
    runtime = StudioRuntime(
        transport=transport,
        inspector=inspector,
        executor=executor,
        gate=approvals,
        export_dir=tmp_path / "exports",
        candidate_dir=cand_dir,
        clock=clock,
    )
    cand = candidate or _make_candidate()
    runtime._store(cand)
    return runtime, live_set, transport, cand


def test_task1_target_confirmation_api_and_flow(tmp_path: Path) -> None:
    """Task 1: Explicit target confirmation works via confirm_target_live_set and is verified."""
    runtime, live_set, _, candidate = _setup_runtime(tmp_path, set_name="")  # set_name empty -> "UNKNOWN"
    cid = candidate.candidate_id
    runtime.approve(cid)

    # Dry run shows requires_target_confirmation = True and target_confirmed = False
    dry = runtime.dry_run(cid)
    assert dry["ok"] is True
    assert dry["diff_report"]["requires_target_confirmation"] is True
    assert dry["diff_report"]["target_confirmed"] is False

    # Attempting to send without target confirmation fails
    send_res = runtime.send_to_ableton(cid, confirmed=True)
    assert send_res["ok"] is False
    assert "Live Set名がUNKNOWN" in send_res["error"]
    assert send_res.get("requires_target_confirmation") is True

    # User explicitly confirms target Live Set
    conf_res = runtime.confirm_target_live_set(cid)
    assert conf_res["ok"] is True
    assert conf_res["target_confirmed"] is True
    current_fp = runtime._inspector.snapshot().set_fingerprint
    assert conf_res["set_fingerprint"] == current_fp

    # Re-running dry_run shows target_confirmed = True
    dry2 = runtime.dry_run(cid)
    assert dry2["diff_report"]["target_confirmed"] is True


def test_task1_fingerprint_change_invalidates_target_confirmation(tmp_path: Path) -> None:
    """Task 1: When Live fingerprint changes, previous target confirmation is invalidated."""
    runtime, live_set, _, candidate = _setup_runtime(tmp_path, set_name="")
    cid = candidate.candidate_id
    runtime.approve(cid)

    runtime.dry_run(cid)
    runtime.confirm_target_live_set(cid)
    fp_before = runtime._inspector.snapshot().set_fingerprint
    assert runtime.is_target_confirmed(cid, fp_before) is True

    # Live Set changes (add track)
    live_set.add_track("External Track")
    fp_after = runtime._inspector.snapshot().set_fingerprint
    assert fp_after != fp_before

    # Confirmation is now invalid for new fingerprint
    assert runtime.is_target_confirmed(cid, fp_after) is False

    # Sending is refused because fingerprint changed & target confirmation is invalidated
    send_res = runtime.send_to_ableton(cid, confirmed=True)
    assert send_res["ok"] is False
    assert "Live Setの状態または接続先が変更されました" in send_res["error"]


def test_task2_delivery_audit_persisted_before_write(tmp_path: Path) -> None:
    """Task 2: APPLYING state is persisted to disk before any Live write operations start."""
    runtime, live_set, _, candidate = _setup_runtime(tmp_path, set_name="Mutant Dub Circuit")
    cid = candidate.candidate_id
    runtime.approve(cid)

    dry = runtime.dry_run(cid)
    assert dry["safety_verdict"] == "PASS"

    audit_file = runtime._delivery_audit_path(cid)
    assert audit_file is not None
    assert not audit_file.exists()

    # Intercept execute to check disk file state while APPLYING
    persisted_during_write = None
    original_execute = runtime._executor.execute

    def checking_execute(*args: Any, **kwargs: Any) -> Any:
        nonlocal persisted_during_write
        if audit_file.exists():
            persisted_during_write = json.loads(audit_file.read_text(encoding="utf-8"))
        return original_execute(*args, **kwargs)

    runtime._executor.execute = checking_execute

    send_res = runtime.send_to_ableton(cid, confirmed=True)
    assert send_res.get("ok") is True, f"send_to_ableton failed: {send_res.get('error')}"
    assert persisted_during_write is not None
    assert persisted_during_write.get("delivery_status") == "APPLYING"
    assert persisted_during_write.get("content_hash") == candidate.content_hash

    # After completion, disk file holds VERIFIED status
    final_audit = json.loads(audit_file.read_text(encoding="utf-8"))
    assert final_audit.get("delivery_status") == "VERIFIED"


def test_task2_persistence_failure_aborts_write(tmp_path: Path) -> None:
    """Task 2: If persisting the audit record fails before writing, write is aborted immediately."""
    runtime, live_set, _, candidate = _setup_runtime(tmp_path, set_name="Mutant Dub Circuit")
    cid = candidate.candidate_id
    runtime.approve(cid)
    runtime.dry_run(cid)

    # Force persistence failure
    execute_called = False

    def mock_execute(*args: Any, **kwargs: Any) -> Any:
        nonlocal execute_called
        execute_called = True
        return None

    runtime._executor.execute = mock_execute

    with patch.object(runtime, "_save_delivery_audit", side_effect=OSError("Disk full / read-only filesystem")):
        send_res = runtime.send_to_ableton(cid, confirmed=True)

    assert send_res["ok"] is False
    assert "配信監査記録の永続化に失敗したため、Liveへの書き込みを拒否しました" in send_res["error"]
    assert execute_called is False  # Live write was never initiated!


def test_task2_crash_recovery_restores_applying_as_unknown(tmp_path: Path) -> None:
    """Task 2: Crash recovery restores in-flight APPLYING state from disk as UNKNOWN and blocks replay."""
    candidate = _make_candidate("candcrash001")

    # Simulate crash before studio restart: write .delivery.json with APPLYING
    cand_dir = tmp_path / "candidates"
    cand_dir.mkdir(parents=True, exist_ok=True)
    (cand_dir / f"{candidate.candidate_id}.json").write_text(json.dumps(candidate.to_dict()), encoding="utf-8")
    audit_file = cand_dir / f"{candidate.candidate_id}.delivery.json"
    audit_file.write_text(
        json.dumps({
            "candidate_id": candidate.candidate_id,
            "delivery_status": "APPLYING",
            "started_at": 100000.0,
            "content_hash": candidate.content_hash,
        }),
        encoding="utf-8",
    )

    # Now create runtime simulating restart
    runtime, live_set, _, candidate = _setup_runtime(tmp_path, candidate=candidate, set_name="Mutant Dub Circuit")

    # Status loaded from disk
    status = runtime.get_delivery_status(candidate.candidate_id)
    assert status["ok"] is True
    assert status["delivery_status"] == "UNKNOWN"
    assert status.get("recovered_from_crash") is True

    # Replay is strictly forbidden
    assert runtime._is_applied_or_in_progress(candidate.candidate_id) is True
    dry = runtime.dry_run(candidate.candidate_id)
    assert any("すでに送信を試行済みです" in b for b in dry["blockers"])
    assert dry["safety_verdict"] == "FAIL"

    apply_res = runtime.apply(candidate.candidate_id, confirmed=True)
    assert apply_res["ok"] is False
    assert "すでに適用を試行済みです" in apply_res["error"]


def test_task2_partial_status_blocks_resend_and_provides_recovery_report(tmp_path: Path) -> None:
    """Task 2: PARTIAL status blocks auto-resend and recover_delivery provides read-back report without rollback."""
    runtime, live_set, transport, candidate = _setup_runtime(tmp_path, set_name="Mutant Dub Circuit")
    cid = candidate.candidate_id
    runtime.approve(cid)

    dry = runtime.dry_run(cid)
    assert dry["safety_verdict"] == "PASS"

    original_request = transport.request
    op_counter = {"count": 0}

    def failing_request(payload: Any, *args: Any, **kwargs: Any) -> dict[str, Any]:
        if isinstance(payload, dict) and payload.get("method") == METHOD_APPLY_OPERATION:
            op_counter["count"] += 1
            if op_counter["count"] >= 3:
                raise LiveTransportError("live_dropped", "Live connection dropped mid-operation")
        return original_request(payload, *args, **kwargs)

    transport.request = failing_request

    # Send causes partial failure
    send_res = runtime.send_to_ableton(cid, confirmed=True)
    assert send_res["delivery_status"] == "PARTIAL"
    assert "recovery_diff_report" in send_res
    report = send_res["recovery_diff_report"]
    assert report["status"] == "PARTIAL"
    assert "自動ロールバックや削除は行いません" in report["manual_recovery_guide"]

    # Re-sending is strictly blocked
    re_dry = runtime.dry_run(cid)
    assert any("すでに送信を試行済みです" in b for b in re_dry["blockers"])

    # Manual recovery inspection API
    recovery = runtime.recover_delivery(cid)
    assert recovery["ok"] is True
    assert recovery["delivery_status"] == "PARTIAL"
    assert "自動削除やロールバックは行いません" in recovery["notice"]


def test_task3_live_fingerprint_audit_and_boundaries(tmp_path: Path) -> None:
    """Task 3: Audit Live fingerprint calculation. Verify mutations detected and document boundaries."""
    runtime, live_set, _, _ = _setup_runtime(tmp_path, set_name="Base Set", tempo=124.0)
    fp_base = runtime._inspector.snapshot().set_fingerprint

    # 1. Track addition is detected
    live_set.add_track("New Track")
    fp_add_track = runtime._inspector.snapshot().set_fingerprint
    assert fp_add_track != fp_base

    # 2. Track rename is detected
    live_set.tracks[-1]["name"] = "Renamed Track"
    fp_rename_track = runtime._inspector.snapshot().set_fingerprint
    assert fp_rename_track != fp_add_track

    # 3. Arrangement clip addition is detected
    clip = FakeArrangementClip(track_index=0, name="Verse Clip", start_beats=0.0, length_beats=16.0)
    live_set.arrangement_clips.append(clip)
    fp_add_clip = runtime._inspector.snapshot().set_fingerprint
    assert fp_add_clip != fp_rename_track

    # 4. Arrangement clip movement is detected
    clip.start_beats = 16.0
    fp_move_clip = runtime._inspector.snapshot().set_fingerprint
    assert fp_move_clip != fp_add_clip

    # 5. Tempo change is detected
    live_set.tempo = 128.0
    fp_tempo = runtime._inspector.snapshot().set_fingerprint
    assert fp_tempo != fp_move_clip
