"""Golden Path E2E Test Suite for KIHACHI MUSIC AI.

Verifies the single, stable production workflow from Japanese brief to verified Ableton Live state:
1. Natural language Japanese brief input -> Music Brain & Brief Interpretation
2. SongSpec & Production Plan generation (110 BPM, D# minor, Mutation Funk x DUB x Tech House)
3. Multi-role editable MIDI Composer (Kick, Snare, Hats, Perc, Bass, Sub, Chords, Synth Stabs, ARP)
4. DRY RUN: Pre-flight safety inspection without altering Live
5. Human Approval: Strict token & plan hash bound authorization
6. Ableton Live Write: Exactly one confirmed non-destructive write
7. Readback & Verification: Verifying tempo, tracks, clips, and note integrity against expected state
8. VERIFIED result: Production candidate ready for human DAW fine-tuning
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure test fixtures are importable
TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from live_fixtures import gate

from kihachi_mcp.models.live_contract import STATUS_VERIFIED
from kihachi_mcp.services.brief_parser import extract_explicit
from kihachi_mcp.services.live_execution_service import LiveExecutionService
from kihachi_mcp.services.live_state_inspector import LiveStateInspector
from kihachi_mcp.services.live_transport_fake import FakeLiveSet, FakeLiveTransport
from kihachi_mcp.services.midi_candidate_builder import build_candidate
from kihachi_mcp.services.studio_interpreter import assemble_brief
from kihachi_mcp.services.studio_runtime import StudioRuntime


def test_kihachi_golden_path_mutant_dub_e2e(tmp_path: Path) -> None:
    """Golden Path E2E test verifying the complete production lifecycle."""
    # 1. Target Live Set setup (Initial Live state at 120 BPM before KIHACHI intervention)
    live_set = FakeLiveSet(
        live_version="12.4.5",
        tempo=120.0,
        set_name="Mutant Dub Circuit",
        is_playing=False,
    )
    # Add a user-existing audio track to confirm non-destructive isolation
    live_set.add_track("User Background Audio", track_type="audio")

    transport = FakeLiveTransport(live_set)
    inspector = LiveStateInspector(transport)
    approval_gate = gate(tmp_path)
    executor = LiveExecutionService(
        transport=transport, inspector=inspector, approval_gate=approval_gate
    )

    cand_dir = tmp_path / "candidates"
    cand_dir.mkdir(parents=True, exist_ok=True)
    export_dir = tmp_path / "exports"
    export_dir.mkdir(parents=True, exist_ok=True)

    runtime = StudioRuntime(
        transport=transport,
        inspector=inspector,
        executor=executor,
        gate=approval_gate,
        export_dir=export_dir,
        candidate_dir=cand_dir,
    )

    # 2. Input Japanese Brief (Target Reference: Mutation Funk x DUB x Tech House, 110 BPM, D# minor)
    raw_prompt = (
        "110 BPM、D# minor。Mutation Funk × DUB × Tech House。"
        "Bootsy Collins的なspace bass、Vocoder、Dub Chord Stabs、Tight Snare、Swing 54%。32小節。"
    )
    explicit_parsed = extract_explicit(raw_prompt)
    brief = assemble_brief(
        explicit_parsed,
        {
            "genre": "tech_house",
            "tempo": 110,
            "bars": 32,
            "key": "D#m",
            "mood": "Psychedelic Space Funk",
            "hats_first_half": "sparse",
            "hats_second_half": "dense",
            "bass_register": "low",
            "note_density": "normal",
            "drop_start_bar": 17,
            "unhandled": [],
            "ambiguous": [],
        },
    )

    # 3. Music Brain & Candidate Builder -> Materialize candidate
    candidate = build_candidate(brief, seed=20261022, candidate_id="mutantdub_cand_01")
    runtime._store(candidate)
    cid = candidate.candidate_id
    assert cid == "mutantdub_cand_01"

    # Verify Candidate Specs
    cand_view = runtime.get_candidate(cid)
    assert cand_view is not None
    assert cand_view.brief.tempo.value == 110
    assert cand_view.brief.key.value == "D#m"
    assert cand_view.brief.bars.value == 32
    assert len(cand_view.clips) > 0
    # Confirm generated clips include essential layers (Kick, Bass, Hats, Chords, etc.)
    clip_parts = {c.part for c in cand_view.clips}
    assert "Kick" in clip_parts
    assert "Bass" in clip_parts

    # 4. DRY RUN Before Approval (Live inspection without modification)
    pre_dry_tempo = live_set.tempo
    pre_dry = runtime.dry_run(cid, change_tempo=True)
    # Before approval, safety verdict is FAIL with blocker "まだ承認されていません"
    assert pre_dry["safety_verdict"] == "FAIL"
    assert pre_dry["can_send"] is False
    assert any("承認" in b for b in pre_dry["blockers"])
    assert pre_dry["candidate_id"] == cid
    assert pre_dry["diff_report"]["tempo_changed"] is True
    assert pre_dry["diff_report"]["songspec"]["tempo"] == 110
    assert len(pre_dry["diff_report"]["tracks_diff"]) > 0

    # Live must remain UNCHANGED during Dry Run
    assert live_set.tempo == pre_dry_tempo
    assert len(live_set.tracks) == 1  # Only User Background Audio

    # 5. Safety Gate: Write is blocked WITHOUT Human Approval
    unapproved_write = runtime.send_to_ableton(cid, confirmed=True, change_tempo=True)
    assert unapproved_write["ok"] is False
    assert "承認" in unapproved_write["error"]
    assert live_set.tempo == 120.0

    # 6. Human Approval
    app_res = runtime.approve(cid)
    assert app_res["ok"] is True
    assert app_res["candidate_id"] == cid

    # Re-run Dry Run with active approval -> Verdict becomes PASS and can_send becomes True!
    dry_approved = runtime.dry_run(cid, change_tempo=True)
    assert dry_approved["safety_verdict"] == "PASS"
    assert dry_approved["can_send"] is True

    # 7. Write Execution to Ableton Live (Exactly once, confirmed)
    exec_res = runtime.send_to_ableton(cid, confirmed=True, change_tempo=True)
    assert exec_res["ok"] is True
    receipt = exec_res.get("receipt") or exec_res
    assert receipt["status"] == STATUS_VERIFIED

    # Verify Live Mutations
    assert live_set.tempo == 110.0
    assert len(live_set.tracks) > 1  # Kihachi tracks added
    user_track = live_set.tracks[0]
    track_name = user_track.get("name") if isinstance(user_track, dict) else user_track.name
    assert track_name == "User Background Audio"  # Existing track preserved

    # 8. Readback & Verification
    ver_res = runtime.verify(cid)
    assert ver_res["ok"] is True
    # In StudioRuntime, human-facing status is 'PROJECT READY' when readback matches expected plan
    assert ver_res["status"] == "PROJECT READY"
    assert "lines" in ver_res
    lines_by_name = {line["name"]: line for line in ver_res["lines"]}
    assert lines_by_name["Tempo"]["status"] == "PASS"
    assert lines_by_name["Tracks"]["status"] == "PASS"
    assert lines_by_name["Clips"]["status"] == "PASS"
    assert lines_by_name["Notes"]["status"] == "PASS"

    # 9. Verify Safety: Double-writing without re-approval & re-dry-run is blocked
    second_write = runtime.send_to_ableton(cid, confirmed=True, change_tempo=True)
    assert second_write["ok"] is False
    assert any(
        phrase in second_write["error"]
        for phrase in (
            "承認",
            "有効期限",
            "Live Setの状態または接続先が変更されました",
            "再度のDry Runが必要です",
        )
    )
