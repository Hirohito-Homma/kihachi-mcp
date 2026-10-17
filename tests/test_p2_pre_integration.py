# -*- coding: utf-8 -*-
"""P2 Pre-Integration Verification Test Suite.

Covers:
- Task 1: End-to-end Studio API / UI flow verification:
  1. Select approved candidate
  2. Confirm target Live Set (POST /api/ableton/confirm-target)
  3. Verify confirmed state matches backend
  4. Execute dry run (POST /api/ableton/dry-run)
  5. Verify diff_report structure and contents
  6. Simulate Live fingerprint mutation in FakeLiveSet
  7. Verify target confirmation and dry run are invalidated
- Task 2: Crash recovery end-to-end verification:
  1. Create in-flight APPLYING state on disk
  2. Terminate / restart Studio runtime
  3. Verify restoration as UNKNOWN with recovered_from_crash=True
  4. Verify strict rejection of resending the same candidate
  5. Verify recover_delivery diff report and read-back data
"""

from __future__ import annotations

import json
from pathlib import Path
import sys
import threading
import time
from typing import Any
import urllib.request
import urllib.error

import pytest

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from live_fixtures import gate

from kihachi_mcp.models.midi_candidate import MidiCandidate
from kihachi_mcp.services.brief_parser import extract_explicit
from kihachi_mcp.services.live_execution_service import LiveExecutionService
from kihachi_mcp.services.live_state_inspector import LiveStateInspector
from kihachi_mcp.services.live_transport_fake import FakeLiveSet, FakeLiveTransport
from kihachi_mcp.services.midi_candidate_builder import build_candidate
from kihachi_mcp.services.studio_interpreter import assemble_brief
from kihachi_mcp.services.studio_runtime import StudioRuntime
from kihachi_mcp.studio.app import StudioApp


def _make_candidate(
    candidate_id: str = "p2integ123456",
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


def _create_app(tmp_path: Path, set_name: str = "") -> tuple[StudioApp, StudioRuntime, FakeLiveSet]:
    live_set = FakeLiveSet(
        live_version="12.4.3",
        tempo=120.0,
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
    )
    app = StudioApp(runtime=runtime)
    return app, runtime, live_set


# -----------------------------------------------------------------------------
# Task 1: UI / API Workflow Verification
# -----------------------------------------------------------------------------
def test_task1_studio_ui_api_flow(tmp_path: Path) -> None:
    """Task 1: Complete end-to-end sequence of candidate selection, target confirmation,

    dry run diff report, and invalidation upon Live fingerprint mutation.
    """
    app, runtime, live_set = _create_app(tmp_path, set_name="")  # set_name empty -> UNKNOWN
    candidate = _make_candidate("p2candui001")
    runtime._store(candidate)
    cid = candidate.candidate_id

    # 1. Candidate is approved
    runtime.approve(cid)
    assert runtime.is_approved(cid) is True

    # 2. Target confirmation action (confirm_target_live_set)
    conf_res = runtime.confirm_target_live_set(cid)
    assert conf_res["ok"] is True
    assert conf_res["target_confirmed"] is True
    assert conf_res["candidate_id"] == cid
    initial_fp = conf_res["set_fingerprint"]
    assert initial_fp == runtime._inspector.snapshot().set_fingerprint

    # 3. Verify confirmed state matches backend delivery status
    delivery_status = runtime.get_delivery_status(cid)
    assert delivery_status["ok"] is True
    assert runtime.is_target_confirmed(cid, initial_fp) is True

    # 4. Execute Dry Run
    dry_res = runtime.dry_run(cid)
    assert dry_res["ok"] is True

    # 5. Display & verify diff report contents
    diff = dry_res.get("diff_report", {})
    assert diff["candidate_id"] == cid
    assert diff["live_set_name"] == "UNKNOWN"
    assert diff["requires_target_confirmation"] is True
    assert diff["target_confirmed"] is True
    assert diff["safety_verdict"] == "PASS"
    assert diff["safe_to_deliver"] is True
    assert len(diff["tracks_diff"]) > 0
    assert diff["total_clips"] > 0
    assert diff["total_notes"] > 0
    assert "conflicts" in diff
    assert len(diff["conflicts"]) == 0

    # 6. Simulate Live fingerprint change in FakeLiveSet (e.g. adding a user track)
    live_set.add_track("User Recording Audio", track_type="audio")
    new_fp = runtime._inspector.snapshot().set_fingerprint
    assert new_fp != initial_fp

    # 7. Verify target confirmation and Dry Run are invalidated
    # Prior confirmation is invalid for new fingerprint
    assert runtime.is_target_confirmed(cid, new_fp) is False

    # Attempting to send immediately detects state change, invalidates dry run & confirmation
    send_res = runtime.send_to_ableton(cid, confirmed=True)
    assert send_res["ok"] is False
    assert "Live Setの状態または接続先が変更されました" in send_res["error"]
    assert cid not in runtime._dry_run_records
    assert cid not in runtime._target_confirmed_records

    # Re-running dry_run shows target confirmation is reset to False
    dry_after = runtime.dry_run(cid)
    assert dry_after["diff_report"]["target_confirmed"] is False


# -----------------------------------------------------------------------------
# Task 2: Crash Recovery Verification
# -----------------------------------------------------------------------------
def test_task2_crash_recovery_flow(tmp_path: Path) -> None:
    """Task 2: Simulate process crash while in APPLYING state, verify restart restoration

    as UNKNOWN, verify replay rejection, and verify recovery_diff_report availability.
    """
    candidate = _make_candidate("p2crash002")
    cid = candidate.candidate_id
    cand_dir = tmp_path / "candidates"
    cand_dir.mkdir(parents=True, exist_ok=True)
    (cand_dir / f"{cid}.json").write_text(json.dumps(candidate.to_dict()), encoding="utf-8")

    # 1. Simulate mid-operation crash: .delivery.json left in APPLYING
    audit_path = cand_dir / f"{cid}.delivery.json"
    audit_path.write_text(
        json.dumps({
            "candidate_id": cid,
            "delivery_status": "APPLYING",
            "started_at": 1700000000.0,
            "content_hash": candidate.content_hash,
            "set_fingerprint_before": "fp_prior_crash",
            "change_tempo": False,
            "skip_instruments": False,
        }, indent=2),
        encoding="utf-8",
    )

    # 2. Restart Studio runtime (new instance reading candidate_dir)
    app, runtime, live_set = _create_app(tmp_path, set_name="Recovery Set")

    # 3. Verify restoration as UNKNOWN with crash recovery marker
    audit = runtime.get_delivery_status(cid)
    assert audit["ok"] is True
    assert audit["delivery_status"] == "UNKNOWN"
    assert audit.get("recovered_from_crash") is True
    assert audit.get("candidate_id") == cid

    # 4. Verify same candidate resend is strictly blocked
    assert runtime._is_applied_or_in_progress(cid) is True
    dry = runtime.dry_run(cid)
    assert any("すでに送信を試行済みです" in b for b in dry["blockers"])
    assert dry["safety_verdict"] == "FAIL"

    apply_res = runtime.apply(cid, confirmed=True)
    assert apply_res["ok"] is False
    assert "すでに適用を試行済みです" in apply_res["error"]

    # 5. Verify recovery_diff_report and readback inspection via recover_delivery
    recovery = runtime.recover_delivery(cid)
    assert recovery["ok"] is True
    assert recovery["candidate_id"] == cid
    assert recovery["delivery_status"] == "UNKNOWN"
    assert "自動削除やロールバックは行いません" in recovery["notice"]
    assert "verification" in recovery
    assert "current_live_fingerprint" in recovery
