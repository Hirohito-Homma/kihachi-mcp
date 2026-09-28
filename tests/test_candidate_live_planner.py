from dataclasses import replace

from live_fixtures import executor, fixed_clock, gate, snapshot_of

from kihachi_mcp.models.live_contract import OP_CREATE_SCENE, OP_REPLACE_CLIP_NOTES
from kihachi_mcp.models.midi_candidate import CandidateClip
from kihachi_mcp.models.production_brief import STUDIO_PARTS
from kihachi_mcp.services.brief_parser import extract_explicit
from kihachi_mcp.services.candidate_live_planner import CandidateLivePlanner
from kihachi_mcp.services.live_transport_fake import FakeLiveSet, FakeLiveTransport
from kihachi_mcp.services.midi_candidate_builder import build_candidate
from kihachi_mcp.services.studio_interpreter import assemble_brief


def _candidate(bars: int = 32, drop: int = 17):
    text = f"{bars}小節 125 BPM Dマイナー。{drop}小節目からドロップにして"
    brief = assemble_brief(
        extract_explicit(text),
        {
            "genre": "tech_house",
            "tempo": 125,
            "bars": bars,
            "key": "Dm",
            "mood": "暗い",
            "hats_first_half": "sparse",
            "hats_second_half": "dense",
            "bass_register": "low",
            "note_density": "normal",
            "drop_start_bar": drop,
            "unhandled": [],
            "ambiguous": [],
        },
    )
    return build_candidate(brief, seed=3, candidate_id="abcd1234ffff")


def test_apply_plan_uses_preview_notes_without_regenerating() -> None:
    candidate = _candidate()
    transport = FakeLiveTransport(FakeLiveSet(live_version="12.4.3", tempo=128))
    plan = CandidateLivePlanner().create_plan(candidate, snapshot_of(transport))
    planned = [
        operation.arguments["notes"]
        for operation in plan.operations
        if operation.op == OP_REPLACE_CLIP_NOTES
    ]
    preview = [[note.to_dict() for note in clip.notes] for clip in candidate.clips]
    assert planned == preview
    assert plan.conflicts == []
    assert any("テンポは変更しません" in warning for warning in plan.warnings)


def test_recording_blocks_the_plan() -> None:
    candidate = _candidate()
    transport = FakeLiveTransport(
        FakeLiveSet(live_version="12.4.3", is_recording=True)
    )
    plan = CandidateLivePlanner().create_plan(candidate, snapshot_of(transport))
    assert plan.status == "blocked"
    assert plan.conflicts[0].kind == "live_is_recording"


def test_different_part_splits_create_chronological_scenes_with_exact_notes() -> None:
    source = _candidate(160, 65)
    clips = []
    for part in STUDIO_PARTS:
        notes = source.clips_for_part(part)[0].notes[:1]
        windows = [("Drop", 65, 76), ("Outro", 141, 20)]
        if part == "Hats":
            windows = [("Drop", 65, 17), ("Drop", 82, 59), ("Outro", 141, 20)]
        clips.extend(
            CandidateClip(part, section, start, length, notes)
            for section, start, length in windows
        )
    candidate = replace(source, clips=tuple(clips))
    fingerprint = candidate.note_fingerprint
    live = FakeLiveSet(live_version="12.4.3")
    live.add_scene("User scene")
    transport = FakeLiveTransport(live)
    plan = CandidateLivePlanner(clock=fixed_clock()).create_plan(
        candidate, snapshot_of(transport), skip_instruments=True
    )
    expected_names = [
        "Drop 65 abcd1234 [KIHACHI]",
        "Drop 82 abcd1234 [KIHACHI]",
        "Outro 141 abcd1234 [KIHACHI]",
    ]
    assert [
        op.arguments["name"] for op in plan.operations if op.op == OP_CREATE_SCENE
    ] == expected_names
    approval_gate = gate()
    token = approval_gate.approve(plan)
    receipt = executor(transport, approval_gate).execute(
        plan, approved=True, approval_token=token
    )
    assert receipt.status == "verified"
    assert [scene["name"] for scene in live.scenes] == ["User scene", *expected_names]
    for clip in candidate.clips:
        scene_index = {65: 1, 82: 2, 141: 3}[clip.start_bar]
        actual = live.session_clips[(STUDIO_PARTS.index(clip.part), scene_index)]
        assert actual.notes == [note.to_dict() for note in clip.notes]
        assert actual.length_beats == clip.length_bars * 4
    assert candidate.note_fingerprint == fingerprint


def test_existing_scene_is_reused_without_moving_it() -> None:
    candidate = _candidate()
    live = FakeLiveSet(live_version="12.4.3")
    live.add_scene("User scene")
    live.add_scene("Intro 1 abcd1234 [KIHACHI]")
    transport = FakeLiveTransport(live)
    plan = CandidateLivePlanner().create_plan(
        candidate, snapshot_of(transport), skip_instruments=True
    )
    creations = [op for op in plan.operations if op.op == OP_CREATE_SCENE]
    assert all(op.arguments["name"] != live.scenes[1]["name"] for op in creations)
    assert all(op.arguments["index"] >= 2 for op in creations)
    intro_notes = [
        op for op in plan.operations
        if op.op == OP_REPLACE_CLIP_NOTES and op.target["scene_index"] == 1
    ]
    assert len(intro_notes) == len(STUDIO_PARTS)


def test_user_owned_clip_is_not_replaced() -> None:
    candidate = _candidate()
    live = FakeLiveSet(live_version="12.4.3")
    live.add_track("KIHACHI Kick abcd1234 [KIHACHI]")
    live.add_scene("Intro 1 abcd1234 [KIHACHI]")
    from kihachi_mcp.services.live_transport_fake import FakeSessionClip

    live.session_clips[(0, 0)] = FakeSessionClip(name="User Clip", length_beats=16)
    plan = CandidateLivePlanner().create_plan(
        candidate, snapshot_of(FakeLiveTransport(live))
    )
    assert any(conflict.kind == "user_owned_clip" for conflict in plan.conflicts)
