from live_fixtures import snapshot_of

from kihachi_mcp.models.live_contract import OP_REPLACE_CLIP_NOTES
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
