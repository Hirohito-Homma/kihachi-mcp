# -*- coding: utf-8 -*-
"""P1 Test Suite: Live Set synchronization, 37-track audit, and strict Dry Run integrity verification."""

from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Any

import pytest

# Ensure kihachi_mcp is importable
TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from live_fixtures import gate

from kihachi_mcp.models.live_contract import managed_track_name
from kihachi_mcp.models.midi_candidate import CandidateClip, MidiCandidate
from kihachi_mcp.models.production_brief import ProductionBrief
from kihachi_mcp.services.brief_parser import extract_explicit
from kihachi_mcp.services.live_execution_service import LiveExecutionService
from kihachi_mcp.services.live_state_inspector import LiveStateInspector
from kihachi_mcp.services.live_transport_fake import (
    FakeArrangementClip,
    FakeLiveSet,
    FakeLiveTransport,
)
from kihachi_mcp.services.midi_candidate_builder import build_candidate
from kihachi_mcp.services.session_pattern_builder import MidiNote
from kihachi_mcp.services.studio_interpreter import assemble_brief
from kihachi_mcp.services.studio_runtime import StudioRuntime


def _make_candidate(
    candidate_id: str = "p1candidate123456",
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
    )
    cand = candidate or _make_candidate()
    runtime._store(cand)
    return runtime, live_set, transport, cand


# -----------------------------------------------------------------------------
# P1 Task 1: Live Set Identification & UNKNOWN Handling
# -----------------------------------------------------------------------------
def test_p1_task1_live_set_identification_and_unknown(tmp_path: Path) -> None:
    """Verify Live Set name retrieval and UNKNOWN fallback when name is empty."""
    # Case A: Live Set name is known
    runtime1, live_set1, _, candidate1 = _setup_runtime(
        tmp_path / "c1", set_name="Mutant Dub Circuit"
    )
    cid1 = candidate1.candidate_id
    dry1 = runtime1.dry_run(cid1)
    assert dry1["ok"] is True
    diff1 = dry1["diff_report"]
    assert diff1["live_set_name"] == "Mutant Dub Circuit"
    assert diff1["project_id"] == cid1
    assert diff1["candidate_id"] == cid1

    # Case B: Live Set name is empty -> must be UNKNOWN
    runtime2, live_set2, _, candidate2 = _setup_runtime(
        tmp_path / "c2", set_name=""
    )
    cid2 = candidate2.candidate_id
    dry2 = runtime2.dry_run(cid2)
    assert dry2["ok"] is True
    diff2 = dry2["diff_report"]
    assert diff2["live_set_name"] == "UNKNOWN"


# -----------------------------------------------------------------------------
# Strict Dry Run Preconditions Verification
# -----------------------------------------------------------------------------
def test_p1_send_refused_without_dry_run(tmp_path: Path) -> None:
    """Requirement 1: Refuse write if Dry Run was never executed."""
    runtime, live_set, _, candidate = _setup_runtime(tmp_path)
    cid = candidate.candidate_id
    runtime.approve(cid)
    assert runtime.is_approved(cid) is True

    # Send without dry_run
    res = runtime.send_to_ableton(cid, confirmed=True)
    assert res["ok"] is False
    assert "Dry Runが実行されていません" in res["error"]


def test_p1_send_refused_when_dry_run_fail(tmp_path: Path) -> None:
    """Requirement 2: Refuse write if Dry Run safety verdict is FAIL."""
    candidate = _make_candidate()
    cid = candidate.candidate_id
    short_id = cid[:8]

    runtime, live_set, _, _ = _setup_runtime(tmp_path, candidate=candidate)
    # Create conflicting track & clip
    kick_track_name = managed_track_name(f"KIHACHI Kick {short_id}")
    track = live_set.add_track(kick_track_name, track_type="midi")
    live_set.arrangement_clips.append(
        FakeArrangementClip(
            track_index=track["index"],
            name="Existing Audio",
            start_beats=0.0,
            length_beats=32.0,
        )
    )
    runtime.approve(cid)

    # Dry run should yield FAIL verdict
    dry = runtime.dry_run(cid)
    assert dry["safety_verdict"] == "FAIL"

    # Send must be refused
    res = runtime.send_to_ableton(cid, confirmed=True)
    assert res["ok"] is False
    assert "Dry Runの安全判定がPASSではありません" in res["error"]


def test_p1_send_refused_when_dry_run_unknown(tmp_path: Path) -> None:
    """Requirement 2: Refuse write if Dry Run safety verdict is UNKNOWN (e.g. disconnected)."""
    runtime, _, _, candidate = _setup_runtime(tmp_path, live_connected=False)
    cid = candidate.candidate_id
    runtime.approve(cid)

    dry = runtime.dry_run(cid)
    assert dry["safety_verdict"] == "UNKNOWN"

    res = runtime.send_to_ableton(cid, confirmed=True)
    assert res["ok"] is False
    # Either Dry Run safety verdict check or Live connection check blocks write
    assert (
        "Dry Runの安全判定がPASSではありません" in res.get("error", "")
        or "Live未接続" in res.get("error", "")
    )


def test_p1_send_refused_when_candidate_modified_after_dry_run(tmp_path: Path) -> None:
    """Requirement 3 & 5: Refuse write if candidate content_hash changed after Dry Run."""
    runtime, live_set, _, candidate = _setup_runtime(tmp_path)
    cid = candidate.candidate_id
    runtime.approve(cid)

    # Dry run with original candidate
    dry = runtime.dry_run(cid)
    assert dry["safety_verdict"] == "PASS"

    # Modify candidate in runtime store (e.g. modify clip notes)
    modified_clips = list(candidate.clips)
    first_clip = modified_clips[0]
    extra_note = MidiNote(pitch=60, start_beats=0.0, duration_beats=1.0, velocity=100)
    new_first_clip = CandidateClip(
        section_name=first_clip.section_name,
        part=first_clip.part,
        start_bar=first_clip.start_bar,
        length_bars=first_clip.length_bars,
        notes=first_clip.notes + (extra_note,),
    )
    modified_clips[0] = new_first_clip
    candidate_mod = MidiCandidate(
        candidate_id=cid,
        brief=candidate.brief,
        clips=tuple(modified_clips),
        seed=42,
    )
    runtime._store(candidate_mod)

    # Attempt to send -> must refuse due to content_hash mismatch with Dry Run or approval
    res = runtime.send_to_ableton(cid, confirmed=True)
    assert res["ok"] is False
    assert (
        "Dry Run後に候補の内容が変更されました" in res["error"]
        or "承認後に候補の内容" in res["error"]
        or "承認されていない候補はLiveへ送りません" in res["error"]
    )


def test_p1_send_refused_when_live_state_modified_after_dry_run(tmp_path: Path) -> None:
    """Requirement 4 & 5: Refuse write if Live Set state changed after Dry Run."""
    runtime, live_set, _, candidate = _setup_runtime(tmp_path)
    cid = candidate.candidate_id
    runtime.approve(cid)

    # Dry run
    dry = runtime.dry_run(cid)
    assert dry["safety_verdict"] == "PASS"
    assert cid in runtime._dry_run_records

    # Change Live state (tempo)
    live_set.tempo = 135.0

    # Send must be refused and dry run record invalidated
    res = runtime.send_to_ableton(cid, confirmed=True)
    assert res["ok"] is False
    assert "Live Setの状態または接続先が変更されました" in res["error"]
    assert cid not in runtime._dry_run_records


def test_p1_send_refused_when_apply_options_mismatch(tmp_path: Path) -> None:
    """Requirement 5 & 6: Refuse write if apply options differ from Dry Run options."""
    runtime, live_set, _, candidate = _setup_runtime(tmp_path)
    cid = candidate.candidate_id
    runtime.approve(cid)

    # Dry run with change_tempo=False, skip_instruments=False
    dry = runtime.dry_run(cid, change_tempo=False, skip_instruments=False)
    assert dry["safety_verdict"] == "PASS"

    # Attempt send with change_tempo=True
    res = runtime.send_to_ableton(cid, confirmed=True, change_tempo=True, skip_instruments=False)
    assert res["ok"] is False
    assert "Dry Run実行時と適用オプション" in res["error"]


def test_p1_send_conditions_established_on_dry_run_pass(tmp_path: Path) -> None:
    """Requirement 7: Verify write conditions pass and execute safely when all preconditions match."""
    runtime, live_set, _, candidate = _setup_runtime(tmp_path)
    cid = candidate.candidate_id
    runtime.approve(cid)

    # 1. Dry run
    dry = runtime.dry_run(cid, change_tempo=False, skip_instruments=False)
    assert dry["ok"] is True
    assert dry["safety_verdict"] == "PASS"
    assert dry["can_send"] is True
    assert dry["diff_report"]["safe_to_deliver"] is True

    # 2. Preconditions verified -> send succeeds in simulator
    res = runtime.send_to_ableton(cid, confirmed=True, change_tempo=False, skip_instruments=False)
    assert res["ok"] is True
    assert "receipt" in res


# -----------------------------------------------------------------------------
# P1 Task 2: 37 Tracks Read-Only Audit
# -----------------------------------------------------------------------------
def test_p1_task2_37_tracks_read_only_audit(tmp_path: Path) -> None:
    """Verify 37-track read-only audit accurately reports track count, names, and clips."""
    runtime, live_set, _, candidate = _setup_runtime(
        tmp_path, track_count=37, set_name="Mutant Dub Circuit"
    )
    cid = candidate.candidate_id

    live_set.arrangement_clips.append(
        FakeArrangementClip(
            track_index=3,
            name="Kick Clip 1",
            start_beats=0.0,
            length_beats=16.0,
            note_count=16,
        )
    )

    mapping_res = runtime.validate_track_part_mapping(cid)
    assert mapping_res["ok"] is True
    assert mapping_res["live_track_count"] == 37
    assert len(mapping_res["live_tracks_audit"]) == 37

    audit_tr3 = mapping_res["live_tracks_audit"][3]
    assert audit_tr3["index"] == 3
    assert audit_tr3["name"] == "Existing Track 3"
    assert audit_tr3["arrangement_clips_count"] == 1

    dry = runtime.dry_run(cid)
    diff = dry["diff_report"]
    assert diff["live_track_count"] == 37
    assert len(diff["live_tracks_audit"]) == 37


# -----------------------------------------------------------------------------
# P1 Task 3: Arrangement Difference & Conflict Detection
# -----------------------------------------------------------------------------
def test_p1_task3_arrangement_conflict_detection(tmp_path: Path) -> None:
    """Verify that overlapping clips on destination track are detected as collisions, blocking send."""
    candidate = _make_candidate()
    cid = candidate.candidate_id
    short_id = cid[:8]

    runtime, live_set, _, _ = _setup_runtime(tmp_path, candidate=candidate)
    kick_track_name = managed_track_name(f"KIHACHI Kick {short_id}")
    track = live_set.add_track(kick_track_name, track_type="midi")
    track_idx = track["index"]

    live_set.arrangement_clips.append(
        FakeArrangementClip(
            track_index=track_idx,
            name="User Existing Audio",
            start_beats=0.0,
            length_beats=32.0,
            note_count=0,
        )
    )

    runtime.approve(cid)

    mapping_res = runtime.validate_track_part_mapping(cid)
    assert mapping_res["ok"] is True
    assert mapping_res["valid"] is False  # Conflict detected!
    assert len(mapping_res["conflicts"]) > 0
    assert mapping_res["safety_verdict"] == "FAIL"

    first_conflict = mapping_res["conflicts"][0]
    assert first_conflict["type"] == "arrangement_range_occupied"
    assert first_conflict["existing_clip"] == "User Existing Audio"

    dry = runtime.dry_run(cid)
    assert dry["safety_verdict"] == "FAIL"
    assert dry["can_send"] is False
    assert dry["diff_report"]["safe_to_deliver"] is False
    assert len(dry["diff_report"]["conflicts"]) > 0

    send_res = runtime.send_to_ableton(cid, confirmed=True)
    assert send_res["ok"] is False
    assert (
        "Dry Runの安全判定がPASSではありません" in send_res["error"]
        or "衝突が検出されたため" in send_res["error"]
    )


def test_p1_default_inspector_arrangement_boundary_and_fingerprint(tmp_path: Path) -> None:
    """When StudioRuntime uses default _PlanningInspector (include_arrangement=False),
    confirm_target_live_set, validate_track_part_mapping, dry_run, and send_to_ableton
    must still inspect Arrangement with include_arrangement=True and block on failure or collision.
    """
    candidate = _make_candidate()
    cid = candidate.candidate_id
    short_id = cid[:8]

    live_set = FakeLiveSet(
        live_version="12.4.3",
        tempo=120.0,
        set_name="",  # UNKNOWN set name
        is_playing=False,
    )
    transport = FakeLiveTransport(live_set)
    # Note: Do NOT pass inspector=... so StudioRuntime uses _PlanningInspector for self._inspector
    # and _ArrangementInspector for self._arrangement_inspector, matching production StudioApp.
    runtime = StudioRuntime(
        transport=transport,
        gate=gate(tmp_path),
        export_dir=tmp_path / "exports",
        candidate_dir=tmp_path / "candidates",
    )
    runtime._store(candidate)
    runtime.approve(cid)

    # 1. Planning snapshot omits arrangement clips, while arrangement snapshot includes them
    kick_track_name = managed_track_name(f"KIHACHI Kick {short_id}")
    track = live_set.add_track(kick_track_name, track_type="midi")
    live_set.arrangement_clips.append(
        FakeArrangementClip(
            track_index=track["index"],
            name="Arrangement Blocker",
            start_beats=0.0,
            length_beats=16.0,
            note_count=4,
        )
    )
    planning_snap, _ = runtime._snapshot()
    arr_snap, _ = runtime._arrangement_snapshot()
    assert planning_snap is not None and arr_snap is not None
    assert planning_snap.arrangement_clips == []
    assert len(arr_snap.arrangement_clips) == 1
    assert planning_snap.set_fingerprint != arr_snap.set_fingerprint

    # 2. Collision on Arrangement must be detected even with default _PlanningInspector
    mapping = runtime.validate_track_part_mapping(cid)
    assert mapping["ok"] is True
    assert mapping["valid"] is False
    assert mapping["safety_verdict"] == "FAIL"
    assert len(mapping["conflicts"]) > 0

    dry_conflict = runtime.dry_run(cid)
    assert dry_conflict["safety_verdict"] == "FAIL"
    assert dry_conflict["can_send"] is False
    refused_conflict = runtime.send_to_ableton(cid, confirmed=True)
    assert refused_conflict["ok"] is False
    assert transport.applied_operation_ids == []

    # 3. Clear collision, confirm target, then add a non-colliding Arrangement clip ->
    #    Arrangement fingerprint change must invalidate both Dry Run and target confirmation
    live_set.arrangement_clips.clear()
    other_track = live_set.add_track("External Audio", track_type="audio")
    dry_pass = runtime.dry_run(cid)
    assert dry_pass["safety_verdict"] == "PASS"
    conf = runtime.confirm_target_live_set(cid)
    assert conf["ok"] is True

    # Mutate ONLY Arrangement on an unrelated track
    live_set.arrangement_clips.append(
        FakeArrangementClip(
            track_index=other_track["index"],
            name="External Arrangement Change",
            start_beats=64.0,
            length_beats=16.0,
        )
    )
    refused_arr_change = runtime.send_to_ableton(cid, confirmed=True)
    assert refused_arr_change["ok"] is False
    assert "Live Setの状態または接続先が変更されました" in refused_arr_change["error"]
    assert cid not in runtime._target_confirmed_records
    assert transport.applied_operation_ids == []
