"""P0 Test Suite: Studio project state, approval state, and Live delivery integrity."""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Ensure kihachi_mcp is importable
TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from live_fixtures import gate

from kihachi_mcp.models.midi_candidate import CandidateClip, MidiCandidate
from kihachi_mcp.services.brief_parser import extract_explicit
from kihachi_mcp.services.live_execution_service import LiveExecutionService
from kihachi_mcp.services.live_state_inspector import LiveStateInspector
from kihachi_mcp.services.live_transport_fake import FakeLiveSet, FakeLiveTransport
from kihachi_mcp.services.midi_candidate_builder import build_candidate
from kihachi_mcp.services.session_pattern_builder import MidiNote
from kihachi_mcp.services.studio_interpreter import assemble_brief
from kihachi_mcp.services.studio_runtime import StudioRuntime


def _make_candidate(
    candidate_id: str = "p0candidate123456",
    tempo: int = 125,
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
            "unhandled": ["歌声生成は今回の対象外です"],
            "ambiguous": [],
        },
    )
    return build_candidate(brief, seed=seed, candidate_id=candidate_id)


def _setup_runtime(
    tmp_path: Path,
    *,
    live_connected: bool = True,
    is_playing: bool = False,
    tempo: float = 125.0,
    candidate: MidiCandidate | None = None,
) -> tuple[StudioRuntime, FakeLiveTransport, MidiCandidate]:
    live_set = FakeLiveSet(live_version="12.4.3", tempo=tempo, is_playing=is_playing)
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
    return runtime, transport, cand


# -----------------------------------------------------------------------------
# P0 Task 1: Storage and Restoration Integrity
# -----------------------------------------------------------------------------
def test_p0_task1_storage_and_restoration_integrity(tmp_path: Path) -> None:
    """Task 1: Verify save and restore pathways for project_id, SongSpec, MIDI, Arrangement, and approval."""
    runtime1, transport, candidate = _setup_runtime(tmp_path)
    cid = candidate.candidate_id

    # 1. Verify project initial state
    project_view = runtime1.project(cid)
    assert project_view["ok"] is True
    assert project_view["candidate_id"] == cid
    assert project_view["songspec"]["tempo"] == 125
    assert project_view["songspec"]["key"] == "Dm"
    assert len(project_view["arrangement"]["sections"]) > 0
    assert project_view["note_count"] == candidate.note_count()
    assert project_view["status"]["approved"] is False

    # 2. Approve candidate
    app_res = runtime1.approve(cid)
    assert app_res["ok"] is True
    assert app_res["approved"] is True
    assert runtime1.is_approved(cid) is True

    # Check persistence on disk
    cand_path = runtime1._candidate_path(cid)
    app_path = runtime1._candidate_path(cid, ".approved")
    assert cand_path is not None and cand_path.is_file()
    assert app_path is not None and app_path.is_file()

    # 3. Simulate process restart by instantiating a new StudioRuntime with same candidate_dir
    cand_dir = tmp_path / "candidates"
    approvals2 = gate(tmp_path)
    runtime2 = StudioRuntime(
        transport=transport,
        inspector=runtime1._inspector,
        executor=runtime1._executor,
        gate=approvals2,
        export_dir=tmp_path / "exports",
        candidate_dir=cand_dir,
    )

    restored = runtime2._lookup(cid)
    assert restored is not None
    assert restored.candidate_id == cid
    assert restored.content_hash == candidate.content_hash
    assert restored.note_fingerprint == candidate.note_fingerprint
    assert restored.brief.tempo.value == candidate.brief.tempo.value
    assert restored.brief.key.value == candidate.brief.key.value
    assert restored.note_count() == candidate.note_count()

    # Approval state must be safely restored with matching hash
    assert runtime2.is_approved(cid) is True


# -----------------------------------------------------------------------------
# P0 Task 2: J.A.R.V.I.S. Instruction Does Not Mutate Existing Project
# -----------------------------------------------------------------------------
def test_p0_task2_jarvis_new_instruction_does_not_mutate_existing_project(tmp_path: Path) -> None:
    """Task 2: New JARVIS instructions/turn must not mutate or corrupt existing candidate/project."""
    runtime, transport, candidate = _setup_runtime(tmp_path)
    cid = candidate.candidate_id

    # Approve existing project
    runtime.approve(cid)
    initial_hash = candidate.content_hash
    initial_fp = candidate.note_fingerprint
    initial_notes = candidate.note_count()

    # Simulate JARVIS proposing a revision based on a user chat instruction
    rev_res = runtime.propose_revision(
        cid,
        scopes=["drums"],
    )
    assert rev_res["ok"] is True
    rev_id = rev_res["revision"]["revision_id"]

    # Verify original candidate has NOT changed in any way
    original = runtime._lookup(cid)
    assert original is not None
    assert original.content_hash == initial_hash
    assert original.note_fingerprint == initial_fp
    assert original.note_count() == initial_notes
    assert runtime.is_approved(cid) is True

    # The revision is pending and distinct
    assert rev_id in runtime._pending_revisions
    child_id = runtime._pending_revisions[rev_id]["child_candidate_id"]
    assert child_id != cid

    # Original project remains approved and untouched
    assert runtime.is_approved(cid) is True
    assert runtime.is_approved(child_id) is False
    assert len(transport.applied_operation_ids) == 0


# -----------------------------------------------------------------------------
# P0 Task 3: Content Hash Verification Covers SongSpec, MIDI, Arrangement
# -----------------------------------------------------------------------------
def test_p0_task3_hash_verification_covers_songspec_midi_arrangement(tmp_path: Path) -> None:
    """Task 3: Hash verification must strictly cover SongSpec, MIDI notes, and Arrangement sections."""
    cand = _make_candidate(candidate_id="p0cand_baseline", tempo=120, bars=32)
    base_hash = cand.content_hash
    base_fp = cand.note_fingerprint

    # 1. Modify SongSpec (tempo changed from 120 to 128)
    cand_tempo = _make_candidate(candidate_id="p0cand_baseline", tempo=128, bars=32)
    assert cand_tempo.content_hash != base_hash

    # 2. Modify Arrangement (bars / sections changed)
    cand_bars = _make_candidate(candidate_id="p0cand_baseline", tempo=120, bars=64)
    assert cand_bars.content_hash != base_hash

    # 3. Modify MIDI (change pitch or duration of a note)
    first_clip = cand.clips[0]
    modified_note = MidiNote(pitch=99, start_beats=0.0, duration_beats=1.0, velocity=100)
    modified_clip = CandidateClip(
        part=first_clip.part,
        section_name=first_clip.section_name,
        start_bar=first_clip.start_bar,
        length_bars=first_clip.length_bars,
        notes=(modified_note,),
    )
    cand_midi = MidiCandidate(
        candidate_id="p0cand_baseline",
        seed=cand.seed,
        brief=cand.brief,
        clips=(modified_clip, *cand.clips[1:]),
    )
    assert cand_midi.content_hash != base_hash
    assert cand_midi.note_fingerprint != base_fp


# -----------------------------------------------------------------------------
# P0 Task 4: Invalidation of Approval on Data Change
# -----------------------------------------------------------------------------
def test_p0_task4_approval_invalidated_when_data_modified_after_approval(tmp_path: Path) -> None:
    """Task 4: When target project data changes after approval, approval must be invalidated immediately."""
    runtime, transport, candidate = _setup_runtime(tmp_path)
    cid = candidate.candidate_id

    # 1. Approve candidate
    runtime.approve(cid)
    assert runtime.is_approved(cid) is True

    # 2. Tamper candidate data on disk (simulate out-of-band edit or modified notes/songspec)
    cand_path = runtime._candidate_path(cid)
    assert cand_path is not None and cand_path.is_file()
    cand_dict = json.loads(cand_path.read_text(encoding="utf-8"))

    # Alter tempo in the brief
    cand_dict["brief"]["tempo"]["value"] = 140
    # Overwrite on disk
    cand_path.write_text(json.dumps(cand_dict), encoding="utf-8")

    # Clear memory cache to force reload from disk
    runtime._candidates.clear()

    # 3. is_approved must detect the mismatch, invalidate the approval, and return False
    assert runtime.is_approved(cid) is False

    # 4. .approved file must be cleared or invalidated
    app_path = runtime._candidate_path(cid, ".approved")
    assert app_path is None or not app_path.is_file()

    # 5. Attempting to send modified candidate to Live MUST be rejected
    send_res = runtime.send_to_ableton(cid, confirmed=True)
    assert send_res["ok"] is False
    assert "承認" in send_res["error"]

    # Live mutations must remain strictly zero
    assert len(transport.applied_operation_ids) == 0


# -----------------------------------------------------------------------------
# P0 Task 5: Live Delivery Dry Run Diff Report
# -----------------------------------------------------------------------------
def test_p0_task5_live_delivery_dry_run_diff_report(tmp_path: Path) -> None:
    """Task 5: Dry Run must provide a full diff report without writing to Live."""
    runtime, transport, candidate = _setup_runtime(tmp_path, tempo=120.0)
    cid = candidate.candidate_id

    # Execute dry run (not yet approved)
    dry_res = runtime.dry_run(cid, change_tempo=True)
    assert dry_res["ok"] is True
    assert dry_res["changes_live"] is False
    assert dry_res["approved"] is False
    assert dry_res["can_send"] is False  # Blocked because not approved

    # Check diff_report structure
    diff = dry_res.get("diff_report")
    assert isinstance(diff, dict)
    assert diff["candidate_id"] == cid
    assert diff["songspec"]["tempo"] == candidate.brief.tempo.value
    assert diff["songspec"]["key"] == candidate.brief.key.value
    assert diff["tempo_changed"] is True
    assert diff["content_hash"] == candidate.content_hash
    assert diff["note_fingerprint"] == candidate.note_fingerprint
    assert len(diff["tracks_diff"]) == len(candidate.parts)
    assert diff["total_clips"] == len(candidate.clips)
    assert diff["total_notes"] == candidate.note_count()
    assert diff["safe_to_deliver"] is False
    assert len(diff["blockers"]) > 0

    # Ensure zero live mutations occurred
    assert len(transport.applied_operation_ids) == 0


# -----------------------------------------------------------------------------
# P0 Task 6: Live Track and Part Mapping Validation
# -----------------------------------------------------------------------------
def test_p0_task6_live_track_part_mapping_validation(tmp_path: Path) -> None:
    """Task 6: Validate mapping between generated parts and Live tracks."""
    runtime, transport, candidate = _setup_runtime(tmp_path)
    cid = candidate.candidate_id

    mapping_res = runtime.validate_track_part_mapping(cid)
    assert mapping_res["ok"] is True
    assert mapping_res["valid"] is True
    assert mapping_res["candidate_id"] == cid
    mapping = mapping_res["mapping"]

    # Check that all candidate parts have a mapping entry
    for part in candidate.parts:
        assert part in mapping
        assert "track_name" in mapping[part]
        assert mapping[part]["matched"] is True

    # Live mutations must remain zero
    assert len(transport.applied_operation_ids) == 0


# -----------------------------------------------------------------------------
# P0 Task 7: Write Refusal Matrix (Zero Live Mutations)
# -----------------------------------------------------------------------------
def test_p0_task7_unapproved_candidate_write_refused(tmp_path: Path) -> None:
    """Task 7a: An unapproved candidate must be rejected from writing to Live."""
    runtime, transport, candidate = _setup_runtime(tmp_path)
    cid = candidate.candidate_id

    # Not approved
    assert runtime.is_approved(cid) is False
    result = runtime.send_to_ableton(cid, confirmed=True)
    assert result["ok"] is False
    assert "承認されていない" in result["error"]
    assert len(transport.applied_operation_ids) == 0


def test_p0_task7_hash_mismatch_write_refused(tmp_path: Path) -> None:
    """Task 7b: A candidate with mismatched content hash must be rejected from writing."""
    runtime, transport, candidate = _setup_runtime(tmp_path)
    cid = candidate.candidate_id

    runtime.approve(cid)
    assert runtime.is_approved(cid) is True

    # Modify content hash in approval record to simulate hash discrepancy
    runtime._approval_records[cid]["content_hash"] = "tampered_fake_hash"

    result = runtime.send_to_ableton(cid, confirmed=True)
    assert result["ok"] is False
    assert "変更" in result["error"] or "承認" in result["error"]
    assert len(transport.applied_operation_ids) == 0


def test_p0_task7_disconnected_live_write_refused(tmp_path: Path) -> None:
    """Task 7c: When Live is disconnected or abnormal, write must be rejected."""
    runtime, transport, candidate = _setup_runtime(tmp_path, live_connected=False)
    cid = candidate.candidate_id

    runtime.approve(cid)
    result = runtime.send_to_ableton(cid, confirmed=True)
    assert result["ok"] is False
    assert "Live" in result["error"]
    assert len(transport.applied_operation_ids) == 0


def test_p0_task7_playing_transport_conflict_write_refused(tmp_path: Path) -> None:
    """Task 7d: When Live transport is playing, write must be blocked by conflict."""
    runtime, transport, candidate = _setup_runtime(tmp_path, is_playing=True)
    cid = candidate.candidate_id

    runtime.approve(cid)
    runtime.dry_run(cid)
    result = runtime.send_to_ableton(cid, confirmed=True)
    assert result["ok"] is False
    assert (
        "再生中" in result.get("error", "")
        or "Dry Runの安全判定がPASSではありません" in result.get("error", "")
        or result.get("status") == "blocked"
    )
    assert len(transport.applied_operation_ids) == 0


def test_p0_task7e_legacy_approval_without_hash_refuses_write(tmp_path: Path) -> None:
    """Task 7e: Legacy .approved file without content_hash must be rejected from writing."""
    runtime, transport, candidate = _setup_runtime(tmp_path)
    cid = candidate.candidate_id

    # Manually write legacy .approved file (timestamp only)
    app_path = runtime._candidate_path(cid, ".approved")
    assert app_path is not None
    app_path.write_text("1700000000\n", encoding="utf-8")
    runtime._approval_records.clear()

    # is_approved may report legacy presence, but write MUST be blocked
    result = runtime.send_to_ableton(cid, confirmed=True)
    assert result["ok"] is False
    assert "レガシー" in result["error"] or "再承認" in result["error"]
    assert len(transport.applied_operation_ids) == 0


def test_p0_task7f_live_state_change_after_dry_run_refuses_write(tmp_path: Path) -> None:
    """Task 7f: When Live state changes after Dry Run, pre-write gate must reject the write."""
    runtime, transport, candidate = _setup_runtime(tmp_path, tempo=125.0)
    cid = candidate.candidate_id

    runtime.approve(cid)

    # 1. Run Dry Run
    dry_res = runtime.dry_run(cid)
    assert dry_res["ok"] is True
    assert dry_res["changes_live"] is False

    # 2. Simulate external change in Live Set (e.g. user changed tempo in Ableton)
    transport.live_set.tempo = 130.0

    # 3. Attempt to send to Ableton must be detected and refused
    result = runtime.send_to_ableton(cid, confirmed=True)
    assert result["ok"] is False
    assert "Dry Run後" in result["error"] or "変更" in result["error"]
    assert len(transport.applied_operation_ids) == 0
