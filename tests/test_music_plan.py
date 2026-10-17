import inspect
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from kihachi_mcp.models.instrument_spec import InstrumentSpec, slap_bass_spec
from kihachi_mcp.models.live_contract import SUPPORTED_OPS
from kihachi_mcp.models.midi_composer import ComposedMidiNote
from kihachi_mcp.models.music_plan import (
    GenerationScope,
    MusicClip,
    MusicNote,
    MusicPlan,
    MusicTrack,
)
from kihachi_mcp.models.production_brief import PART_ORDER
from kihachi_mcp.services import music_plan_compiler, slap_bass_slice
from kihachi_mcp.services.live_approval_gate import ApprovalError, ApprovalGate
from kihachi_mcp.services.music_plan_compiler import compile_music_plan, dry_run_summary
from kihachi_mcp.services.music_plan_validator import (
    MUSIC_VALIDATION_FAILED,
    SCOPE_VIOLATION,
    validate_music_plan,
)
from kihachi_mcp.services.musical_time import bar_start_beats
from kihachi_mcp.services.slap_bass_slice import TRACK_ID, compose_slap_bass_slice

FIXED_NOW = datetime(2026, 10, 3, tzinfo=UTC)


def _instruments() -> dict[str, InstrumentSpec]:
    return {TRACK_ID: slap_bass_spec()}


def _with_notes(plan: MusicPlan, notes: tuple[MusicNote, ...]) -> MusicPlan:
    track = plan.tracks[0]
    clip = track.clips[0]
    return replace(plan, tracks=(replace(track, clips=(replace(clip, notes=notes),)),))


def test_offline_slice_is_eight_bars_of_slap_bass_only() -> None:
    plan = compose_slap_bass_slice()
    notes = plan.tracks[0].clips[0].notes
    assert plan.schema == "kihachi.music_plan.v1"
    assert (plan.tempo, plan.key, plan.bars) == (110, "D#m", 8)
    assert [track.id for track in plan.tracks] == ["slap_bass"]
    assert plan.scope.preserve == ("kick", "drums", "percussion")
    assert notes
    assert all(28 <= note.pitch <= 64 for note in notes)
    assert MusicPlan.from_dict(plan.to_dict()) == plan
    result = validate_music_plan(plan, _instruments())
    assert result.ok, [issue.message for issue in result.issues]


def test_bar_positions_follow_the_meter() -> None:
    assert bar_start_beats(1, 4, 4) == 0
    assert bar_start_beats(17, 4, 4) == 64
    assert bar_start_beats(17, 3, 4) == 48
    scope = GenerationScope(
        tracks=(TRACK_ID,),
        start_bar=17,
        length_bars=2,
        numerator=3,
        denominator=4,
    )
    plan = MusicPlan(
        tempo=110,
        key="D#m",
        bars=18,
        numerator=3,
        denominator=4,
        tracks=(
            MusicTrack(
                TRACK_ID,
                "SLAP BASS",
                clips=(MusicClip(17, 2, (MusicNote(40, 0, 0.5, 80),)),),
            ),
        ),
        scope=scope,
    )
    live = compile_music_plan(plan, _instruments(), now=FIXED_NOW)
    clip = next(operation for operation in live.operations if operation.op == "create_session_clip")
    assert clip.arguments["length_beats"] == 6
    summary = dry_run_summary(plan, live)
    assert summary["range"] == {"start": 48, "end": 54}


def test_scope_and_pitch_violations_are_rejected() -> None:
    plan = compose_slap_bass_slice()
    kick = MusicTrack("kick", "KICK", clips=plan.tracks[0].clips)
    scoped = validate_music_plan(replace(plan, tracks=plan.tracks + (kick,)), _instruments())
    assert any(issue.code == SCOPE_VIOLATION for issue in scoped.issues)
    too_low = _with_notes(plan, (MusicNote(10, 0.5, 0.4, 80),))
    pitched = validate_music_plan(too_low, _instruments())
    assert any(issue.code == MUSIC_VALIDATION_FAILED for issue in pitched.issues)
    late = _with_notes(plan, (MusicNote(40, 31.8, 0.4, 80),))
    outside = validate_music_plan(late, _instruments())
    assert any(issue.code == MUSIC_VALIDATION_FAILED for issue in outside.issues)


def test_compiler_uses_only_existing_live_operations(tmp_path) -> None:
    plan = compose_slap_bass_slice()
    live = compile_music_plan(plan, _instruments(), now=FIXED_NOW)
    ops = [operation.op for operation in live.operations]
    assert ops == ["create_scene", "create_midi_track", "create_session_clip", "replace_clip_notes"]
    assert set(ops) <= SUPPORTED_OPS
    assert all(not operation.destructive for operation in live.operations)
    clip = next(operation for operation in live.operations if operation.op == "create_session_clip")
    assert clip.arguments["length_beats"] == 32
    summary = dry_run_summary(plan, live)
    assert summary["status"] == "approval_required"
    assert summary["range"] == {"start": 0, "end": 32}
    assert summary["playback"] == "UNKNOWN"
    assert summary["save"] is False
    assert summary["executed"] is False
    assert summary["live_changed"] is False
    changed = _with_notes(
        plan,
        (
            MusicNote(
                plan.tracks[0].clips[0].notes[0].pitch + 1,
                plan.tracks[0].clips[0].notes[0].start,
                plan.tracks[0].clips[0].notes[0].duration,
                plan.tracks[0].clips[0].notes[0].velocity,
            ),
            *plan.tracks[0].clips[0].notes[1:],
        ),
    )
    edited = compile_music_plan(changed, _instruments(), now=FIXED_NOW)
    assert live.source_plan_hash != edited.source_plan_hash
    gate = ApprovalGate(tmp_path / "approvals.json", clock=lambda: FIXED_NOW)
    token = gate.approve(live)
    gate.authorize(live, token)
    with pytest.raises(ApprovalError) as caught:
        gate.authorize(edited, token)
    assert caught.value.code == "plan_changed"


def test_all_parts_preview_uses_one_track_per_part() -> None:
    from kihachi_mcp.services.music_plan_preview import preview_music_plan

    preview = preview_music_plan(
        {"role": "全パート", "key": "D#m", "tempo": 124, "bars": 8, "grid": "1/16"}
    )
    assert preview["ok"] is True
    assert preview["paid_api"] is False
    assert preview["executed"] is False
    assert preview["live_changed"] is False
    music = preview["music"]
    assert isinstance(music, dict)
    assert [track["id"] for track in music["tracks"]] == list(PART_ORDER)
    live = compile_music_plan(MusicPlan.from_dict(music), now=FIXED_NOW)
    indexes = [
        operation.target["track_index"]
        for operation in live.operations
        if operation.op == "create_midi_track"
    ]
    assert indexes == list(range(len(PART_ORDER)))
    assert {operation.op for operation in live.operations} <= {
        "create_scene",
        "create_midi_track",
        "create_session_clip",
        "replace_clip_notes",
    }


def test_preview_records_direction_without_changing_notes() -> None:
    from kihachi_mcp.services.music_plan_preview import preview_music_plan

    body = {"role": "bass", "key": "D#m", "tempo": 110, "bars": 2, "grid": "1/16"}
    base = preview_music_plan(body)
    directed = preview_music_plan(
        {
            **body,
            "genre": "mutation_funk",
            "mood": "暗い",
            "prompt": "キックは短め。ミューテーションファンクを作って",
        }
    )
    assert "direction" not in base
    assert directed["ok"] is True
    assert directed["paid_api"] is False
    assert directed["executed"] is False
    assert directed["live_changed"] is False
    assert directed["direction"] == {
        "genre": "mutation_funk",
        "mood": "暗い",
        "prompt": "キックは短め。ミューテーションファンクを作って",
    }
    assert "方向の記録" in "".join(directed["music"]["warnings"])
    assert directed["music"]["tracks"][0]["clips"][0]["notes"] == base["music"]["tracks"][0]["clips"][0]["notes"]
    assert "genre" not in directed["music"]


def test_composer_slice_does_not_call_live() -> None:
    seen = {}

    class Recorder:
        def compose(self, request):
            seen["request"] = request
            return (ComposedMidiNote(40, 0.5, 0.4, 90),)

    plan = compose_slap_bass_slice(Recorder())
    assert seen["request"].constraints.role == "bass"
    assert plan.tracks[0].clips[0].notes[0].pitch == 40
    for module in (music_plan_compiler, slap_bass_slice):
        source = inspect.getsource(module)
        assert "send_to_ableton" not in source
        assert "execute_live" not in source
        assert "ApprovalGate" not in source
