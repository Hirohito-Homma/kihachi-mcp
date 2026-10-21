"""Jarvis phase 1: structured intent, a plan, and read-only answers."""

from pathlib import Path

from test_studio_workflow import SMOKE_TEST, _create, _studio

from kihachi_mcp.models.live_mutation import LiveMutationOperation, LiveMutationPlan
from kihachi_mcp.services.jarvis import JarvisOrchestrator, context_from_runtime
from kihachi_mcp.services.jarvis.intent import (
    intent_from_model_json,
    parse_intent,
    validate_intent,
)
from kihachi_mcp.services.jarvis.plan import build_plan, validate_plan
from kihachi_mcp.services.jarvis.revision import revision_request
from kihachi_mcp.services.jarvis.state import JarvisStateMachine, JarvisTransitionError
from kihachi_mcp.services.jarvis.verification import settle_readback
from kihachi_mcp.services.jarvis.write_gate import request_gateway_write
from kihachi_mcp.services.live_approval_gate import ApprovalGate


def _project() -> dict:
    return {
        "ok": True,
        "project": {
            "candidate_id": "cand",
            "songspec": {"tempo": 120, "key": "Cm", "genre": "tech_house", "bars": 32},
            "arrangement": {
                "sections": [
                    {"name": "Intro", "start_bar": 1},
                    {"name": "Drop", "start_bar": 17},
                ]
            },
            "tracks": [
                {"part": "Bass", "label": "BASS", "note_count": 40},
                {"part": "Kick", "label": "KICK", "note_count": 32},
            ],
            "status": {"approved": False, "applied": False, "verification": None},
        },
        "live": {"connected": False, "state": "not_configured"},
        "ollama": {"ok": True, "state": "ready"},
    }


def test_status_question_becomes_a_read_intent():
    intent = parse_intent("今の曲どうなってる？")
    assert intent["type"] == "inspect_project"
    assert intent["write_requested"] is False
    validate_intent(intent)


def test_bass_question_stays_an_evaluation():
    intent = parse_intent("後半のベースちょっと多くない？")
    assert intent == {
        "type": "evaluate_arrangement",
        "target": "bass",
        "scope": "later_section",
        "range": None,
        "direction": None,
        "requested_action": "evaluate",
        "write_requested": False,
        "recognized": True,
    }


def test_ranged_edit_requests_a_write_and_stops_for_approval():
    result = JarvisOrchestrator(context_reader=_project).ask("512から544のベースを少し減らして")
    assert result["intent"]["type"] == "modify_arrangement"
    assert result["intent"]["range"] == {"start": 512, "end": 544}
    assert result["intent"]["direction"] == "reduce_density"
    assert result["intent"]["write_requested"] is True
    assert result["plan"]["requires_human_approval"] is True
    assert result["plan"]["proposed_writes"][0]["target"] == "bass"
    assert result["plan"]["executable"] is False
    assert result["state"] == "APPROVAL_REQUIRED"
    assert result["executed"] is False
    assert result["live_changed"] is False
    assert "EXECUTING" not in result["states"]


def test_delete_plan_is_high_risk_and_still_not_executed():
    plan = build_plan(parse_intent("ベースを消して"))
    assert plan["risk_level"] == "high"
    assert plan["requires_human_approval"] is True
    validate_plan(plan)


def test_unknown_utterance_does_not_complete():
    result = JarvisOrchestrator(context_reader=_project).ask("あの感じでよろしく")
    assert result["ok"] is False
    assert result["state"] == "UNKNOWN"
    assert result["live_changed"] is False


def test_unreadable_context_is_unknown_not_success():
    def explode():
        raise RuntimeError("bridge down")

    result = JarvisOrchestrator(context_reader=explode).ask("今のプロジェクトどうなってる？")
    assert result["state"] == "UNKNOWN"
    assert result["ok"] is False


def test_failed_context_does_not_become_completed():
    result = JarvisOrchestrator(context_reader=lambda: {"ok": False}).ask("今の曲どうなってる？")
    assert result["state"] == "FAILED"
    assert result["ok"] is False
    machine = JarvisStateMachine()
    machine.transition("UNDERSTANDING")
    machine.transition("FAILED")
    try:
        machine.transition("COMPLETED")
    except JarvisTransitionError:
        return
    raise AssertionError("FAILED became COMPLETED")


def test_execution_transition_is_rejected():
    machine = JarvisStateMachine()
    machine.transition("UNDERSTANDING")
    machine.transition("READING_STATE")
    machine.transition("PLANNING")
    try:
        machine.transition("EXECUTING")
    except JarvisTransitionError:
        return
    raise AssertionError("PLANNING entered EXECUTING")


def test_missing_fields_stay_unknown_and_a_real_candidate_is_reported(tmp_path: Path):
    empty = JarvisOrchestrator().ask("今の曲どうなってる？")
    assert empty["state"] == "COMPLETED"
    assert empty["context"]["tempo"] == "UNKNOWN"
    assert empty["context"]["live_state"] == "UNKNOWN"
    assert "候補がありません" in empty["reply"]

    runtime, live = _studio(tmp_path)
    candidate_id = _create(runtime, SMOKE_TEST)
    result = JarvisOrchestrator(context_reader=lambda: context_from_runtime(runtime)).ask(
        "今の曲どうなってる？"
    )
    assert result["state"] == "COMPLETED"
    assert result["context"]["candidate_id"] == candidate_id
    assert result["context"]["tempo"] == 120
    assert result["context"]["bars"] == 32
    assert "120" in result["reply"]
    assert result["live_changed"] is False
    assert live.tracks == []


def _live_plan(*, destructive: bool = False) -> LiveMutationPlan:
    return LiveMutationPlan(
        request_id="jarvis-req",
        idempotency_key="jarvis-key",
        source_plan_hash="source",
        set_fingerprint="uninspected",
        expires_at="2099-01-01T00:00:00+00:00",
        operations=[
            LiveMutationOperation(
                operation_id="001-set_tempo",
                op="set_tempo",
                arguments={"tempo": 110},
                destructive=destructive,
                expected_readback={"tempo": 110},
            )
        ],
    )


def test_confirm_true_alone_does_not_call_the_gateway_tool():
    calls: list[dict] = []

    def tool(**kwargs):
        calls.append(kwargs)
        return {"ok": True, "live_changed": True}

    result = JarvisOrchestrator(context_reader=_project).request_gateway_write(
        tool,
        _live_plan(),
        ApprovalGate(),
        confirm=True,
    )
    assert calls == []
    assert result["called"] is False
    assert result["executed"] is False
    assert result["live_changed"] is False
    assert result["error"] == "confirm=true だけでは送りません。人の承認が必要です。"
    assert "EXECUTING" not in result["states"]


def test_destructive_write_without_approval_does_not_call_the_tool():
    calls: list[dict] = []

    def tool(**kwargs):
        calls.append(kwargs)
        return {"ok": True, "live_changed": True}

    result = request_gateway_write(tool, _live_plan(destructive=True), ApprovalGate(), confirm=True)
    assert calls == []
    assert result["ok"] is False
    assert "破壊的な操作" in result["error"]
    assert result["live_changed"] is False


def test_approved_plan_is_not_sent_unless_execute_is_explicit():
    plan = _live_plan()
    gate = ApprovalGate()
    gate.approve(plan)
    calls: list[dict] = []

    def tool(**kwargs):
        calls.append(kwargs)
        return {"ok": True, "live_changed": False}

    held = request_gateway_write(tool, plan, gate, confirm=True, execute=False)
    assert calls == []
    assert held["authorized"] is True
    assert held["executed"] is False
    assert held["live_changed"] is False

    sent = request_gateway_write(tool, plan, gate, confirm=False, execute=True)
    assert calls == [{"confirm": True}]
    assert sent["called"] is True
    assert sent["live_changed"] is False


def _to_executing(machine: JarvisStateMachine) -> None:
    machine.transition("UNDERSTANDING")
    machine.transition("READING_STATE")
    machine.transition("PLANNING")
    machine.transition("APPROVAL_REQUIRED")
    machine.transition("APPROVED")
    machine.transition("EXECUTING")


def test_matched_readback_is_not_completed():
    orchestrator = JarvisOrchestrator(context_reader=_project)
    asked = orchestrator.ask("512から544のベースを少し減らして")
    assert asked["state"] == "APPROVAL_REQUIRED"
    settled = orchestrator.settle_readback("matched")
    assert settled["state"] == "HUMAN_AUDITION_REQUIRED"
    assert "VERIFYING" in settled["states"]
    assert "COMPLETED" not in settled["states"]
    assert settled["musical_quality_claimed"] is False
    assert settled["executed"] is False
    assert settled["live_changed"] is False


def test_failed_readback_cannot_become_completed():
    machine = JarvisStateMachine()
    _to_executing(machine)
    settle_readback(machine, "mismatched")
    assert machine.state == "FAILED"
    try:
        machine.transition("COMPLETED")
    except JarvisTransitionError:
        return
    raise AssertionError("FAILED became COMPLETED")


def test_pending_readback_is_not_success():
    orchestrator = JarvisOrchestrator(context_reader=_project)
    orchestrator.ask("ベースを消して")
    result = orchestrator.settle_readback("pending")
    assert result["ok"] is False
    assert result["state"] == "UNKNOWN"
    assert "COMPLETED" not in result["states"]
    assert "LIVE VERIFICATION PENDING" in result["reply"]


def test_executing_cannot_skip_verification():
    machine = JarvisStateMachine()
    _to_executing(machine)
    try:
        machine.transition("VERIFIED")
    except JarvisTransitionError:
        return
    raise AssertionError("EXECUTING skipped VERIFYING")


def test_dub_request_goes_to_brain_and_does_not_touch_live(tmp_path: Path):
    calls: list[dict] = []

    def reviewer(songspec):
        calls.append(songspec)
        return {"ok": True, "live_changed": False, "musical_quality_claimed": False, "score": 0}

    runtime, live = _studio(tmp_path)
    _create(runtime, SMOKE_TEST)
    result = JarvisOrchestrator(
        context_reader=lambda: context_from_runtime(runtime),
        reviewer=reviewer,
    ).ask("もっと Dub っぽく")
    assert result["intent"]["type"] == "musical_judgment"
    assert result["intent"]["write_requested"] is False
    assert result["plan"]["proposed_writes"] == []
    assert result["plan"]["brain_review"]["score"] == 0
    assert calls and calls[0]["genre"]
    assert result["live_changed"] is False
    assert result["musical_quality_claimed"] is False
    assert live.tracks == []


def test_brain_notes_become_a_plan_that_does_not_write():
    spec = {
        "genre": "tech_house",
        "tempo": 124,
        "key": "D#m",
        "bars": 8,
        "length_minutes": 1,
        "tracks": ["Kick", "Bass"],
    }

    def reader():
        return {"ok": True, "project": {"songspec": spec}, "live": {}, "ollama": {"ok": True}}

    result = JarvisOrchestrator(context_reader=reader).ask("もっと Dub っぽく")
    assert result["state"] == "COMPLETED"
    assert result["plan"]["proposed_writes"] == []
    assert result["plan"]["executable"] is False
    assert result["plan"]["requires_human_approval"] is False
    assert any("FX" in step for step in result["plan"]["proposal"])
    assert "Liveには書いていません" in result["reply"]
    assert "EXECUTING" not in result["states"]
    assert result["live_changed"] is False
    assert result["musical_quality_claimed"] is False


def test_studio_songspec_fields_reach_the_brain_plan():
    spec = {
        "genre": "techno",
        "tempo": 125,
        "key": "Dm",
        "bars": 96,
        "duration_minutes": 3.07,
    }
    arrangement = {
        "sections": [
            {"active_tracks": ["Kick", "Hats", "Bass", "Stab"]},
        ]
    }

    def reader():
        return {
            "ok": True,
            "project": {"songspec": spec, "arrangement": arrangement},
            "live": {},
            "ollama": {"ok": True},
        }

    result = JarvisOrchestrator(context_reader=reader).ask("もっと Dub っぽく")
    proposal = " / ".join(result["plan"]["proposal"])
    assert "tracks must not be empty" not in proposal
    assert "length_minutes must be positive" not in proposal
    assert "FX" in proposal
    assert result["plan"]["proposed_writes"] == []
    assert result["live_changed"] is False


def test_change_request_maps_to_an_existing_revision_or_says_why_not():
    thin = revision_request(parse_intent("33-48小節のハットを減らして"))
    assert thin == {"ok": True, "scopes": ["thin"], "parts": ["Hats"], "bars": (33, 48)}
    clear = revision_request(parse_intent("17から24のキックを消して"))
    assert clear["scopes"] == ["clear"] and clear["parts"] == ["Kick"]
    more = revision_request(parse_intent("33-48小節のハットを増やして"))
    assert more["scopes"] == ["thicken"]
    assert revision_request(parse_intent("33-48小節のキックを増やして"))["ok"] is False
    assert revision_request(parse_intent("ハットを減らして"))["ok"] is False
    assert revision_request(parse_intent("今の曲どうなってる？"))["ok"] is False


def test_more_parts_are_understood_and_open_hat_is_not_hats():
    snare = parse_intent("9-12小節のスネアを減らして")
    assert snare["target"] == "snare"
    assert revision_request(snare)["parts"] == ["Snare"]
    assert parse_intent("9-12小節のオープンハットを消して")["target"] == "openhat"
    assert parse_intent("後半のパッドを消して")["target"] == "pad"


def test_evaluation_answers_with_the_density_of_the_named_bars():
    hats = [2] * 32 + [6] * 16 + [2] * 48

    def reader():
        return {
            "ok": True,
            "project": {
                "songspec": {"tempo": 125, "key": "Dm", "genre": "techno", "bars": 96},
                "arrangement": {"bars": 96, "sections": []},
                "tracks": [{"part": "Hats", "label": "HATS", "note_count": sum(hats), "bar_notes": hats}],
            },
            "live": {},
            "ollama": {"ok": True},
        }

    result = JarvisOrchestrator(context_reader=reader).ask("33-48小節のハット多くない？")
    assert result["intent"]["type"] == "evaluate_arrangement"
    assert "33–48小節は1小節あたり6.0音、それ以外は2.0音です。" in result["reply"]
    assert result["live_changed"] is False
    later = JarvisOrchestrator(context_reader=reader).ask("後半のハット多くない？")
    assert "49–96小節は1小節あたり2.0音、それ以外は3.3音です。" in later["reply"]


def test_project_bar_notes_add_up_to_each_part(tmp_path: Path):
    runtime, _live = _studio(tmp_path)
    candidate_id = _create(runtime, SMOKE_TEST)
    project = runtime.project(candidate_id)
    for track in project["tracks"]:
        assert len(track["bar_notes"]) == project["songspec"]["bars"]
        assert sum(track["bar_notes"]) == track["note_count"]


def test_section_words_become_bars_only_from_the_open_song():
    song = {
        "bars": 96,
        "sections": [
            {"name": "Intro", "start_bar": 1, "end_bar": 32},
            {"name": "Break", "start_bar": 33, "end_bar": 56},
            {"name": "Drop", "start_bar": 57, "end_bar": 80},
        ],
    }
    assert revision_request(parse_intent("後半のハットを増やして"), song)["bars"] == (49, 96)
    assert revision_request(parse_intent("前半のハットを減らして"), song)["bars"] == (1, 48)
    assert revision_request(parse_intent("リエントリーのハットを減らして"), song)["bars"] == (57, 80)
    assert revision_request(parse_intent("後半のハットを増やして"))["ok"] is False


def test_voice_transcript_uses_the_same_jarvis_turn():
    page = (
        Path(__file__).resolve().parents[1] / "src/kihachi_mcp/studio/static/index.html"
    ).read_text(encoding="utf-8")
    assert "dialogue-utterance').value=data.transcript" in page
    assert "if(shouldAutoSend)$('dialogue-send').click();" in page
    assert "/api/jarvis/turn" in page
    assert "parseVoiceIntent" not in page
    assert "musical_judgment" in page


def test_inspect_uses_safe_gateway_reads_and_skips_writes():
    calls: list[str] = []

    def status():
        calls.append("ableton_status")
        return {"ok": True, "tempo": 110, "error": None}

    def tracks():
        calls.append("ableton_list_tracks")
        return {"ok": True, "tracks": [], "error": None}

    def write(confirm: bool = False):
        calls.append(f"set_tempo:{confirm}")
        return {"ok": True, "live_changed": True}

    result = JarvisOrchestrator(
        context_reader=_project,
        live_client={
            "ableton_status": status,
            "ableton_list_tracks": tracks,
            "set_tempo": write,
        },
    ).ask("今の曲どうなってる？")
    assert result["state"] == "COMPLETED"
    assert result["live_changed"] is False
    assert calls == ["ableton_status", "ableton_list_tracks"]
    assert result["context"]["gateway_read"]["ableton_status"]["tempo"] == 110


def test_failed_gateway_read_is_not_success():
    result = JarvisOrchestrator(
        context_reader=_project,
        live_client={"ableton_status": lambda: {"ok": False, "error": "down"}},
    ).ask("今のプロジェクトどうなってる？")
    assert result["ok"] is False
    assert result["state"] == "UNKNOWN"
    assert result["live_changed"] is False


def test_approved_plan_runs_only_on_the_fake_transport():
    from live_fixtures import empty_live_set, gate, planner, project_plan, snapshot_of

    from kihachi_mcp.services.jarvis.fake_execution import run_approved_plan_on_fake
    from kihachi_mcp.services.live_transport_fake import FakeLiveTransport

    transport = FakeLiveTransport(empty_live_set(tempo=120.0))
    subject = gate()
    plan = planner().create_session_plan(project_plan(), snapshot_of(transport))
    refused = run_approved_plan_on_fake(plan, subject, transport)
    assert refused["executed"] is False
    assert transport.live_set.tracks == []

    subject.approve(plan)
    ran = run_approved_plan_on_fake(plan, subject, transport)
    assert ran["status"] == "verified"
    assert ran["state"] == "HUMAN_AUDITION_REQUIRED"
    assert "VERIFYING" in ran["states"]
    assert "COMPLETED" not in ran["states"]
    assert ran["musical_quality_claimed"] is False
    assert "実機は見ていません" in ran["reply"]
    assert transport.live_set.tracks

    blocked = run_approved_plan_on_fake(plan, subject, object())
    assert blocked["live_changed"] is False
    assert "実機には送っていません" in blocked["reply"]


def test_unread_music_plan_finishes_only_after_rebind_on_fake() -> None:
    from live_fixtures import empty_live_set

    from kihachi_mcp.services.jarvis.fake_execution import (
        rebind_uninspected_plan,
        run_approved_plan_on_fake,
    )
    from kihachi_mcp.services.live_transport_fake import FakeLiveTransport
    from kihachi_mcp.services.music_plan_preview import prepare_music_plan

    prepared = prepare_music_plan({"role": "bass", "key": "D#m", "tempo": 110, "bars": 2})
    assert prepared.live is not None
    transport = FakeLiveTransport(empty_live_set(tempo=110.0))
    gate = ApprovalGate()
    gate.approve(prepared.live)
    refused = run_approved_plan_on_fake(prepared.live, gate, transport)
    assert refused["live_changed"] is False
    assert transport.live_set.tracks == []

    rebound = rebind_uninspected_plan(prepared.live, transport)
    assert gate.holds(prepared.live.plan_hash) is True
    assert gate.holds(rebound.plan_hash) is False
    gate.approve(rebound)
    finished = run_approved_plan_on_fake(rebound, gate, transport)
    assert finished["status"] == "verified"
    assert finished["state"] == "HUMAN_AUDITION_REQUIRED"
    assert "COMPLETED" not in finished["states"]
    assert finished["musical_quality_claimed"] is False
    assert "実機は見ていません" in finished["reply"]
    assert transport.live_set.tracks
    blocked = run_approved_plan_on_fake(rebound, gate, object())
    assert blocked["live_changed"] is False


def test_broken_model_json_is_unknown_and_does_not_write():
    broken = intent_from_model_json("{")
    assert broken["recognized"] is False
    assert broken["write_requested"] is False
    array = intent_from_model_json("[1, 2]")
    assert array["recognized"] is False
    disguised = intent_from_model_json(
        '{"type":"evaluate_arrangement","write_requested":true,"target":"bass"}'
    )
    assert disguised["recognized"] is False


def test_disagreeing_live_reads_are_not_success():
    result = JarvisOrchestrator(
        context_reader=lambda: {
            "ok": True,
            "project": _project()["project"],
            "live": {"connected": True, "state": "ready", "tempo": 110, "track_count": 2},
            "ollama": {"ok": True, "state": "ready"},
        },
        live_client={
            "ableton_status": lambda: {"ok": True, "tempo": 124, "track_count": 2, "error": None},
        },
    ).ask("今の曲どうなってる？")
    assert result["ok"] is False
    assert result["state"] == "UNKNOWN"
    assert result["live_changed"] is False
    assert "tempo" in result["reply"]


def _sample_clip_payload() -> dict:
    notes = [
        {"pitch": 39, "start_time": 0.0, "duration": 0.5, "velocity": 110},
        {"pitch": 39, "start_time": 1.5, "duration": 0.25, "velocity": 50},
        {"pitch": 51, "start_time": 4.0, "duration": 0.5, "velocity": 100},
        {"pitch": 46, "start_time": 6.5, "duration": 0.25, "velocity": 80},
        {"pitch": 39, "start_time": 8.0, "duration": 0.5, "velocity": 105},
        {"pitch": 51, "start_time": 9.0, "duration": 0.25, "velocity": 95},
        {"pitch": 46, "start_time": 10.5, "duration": 0.25, "velocity": 45},
        {"pitch": 39, "start_time": 12.0, "duration": 0.75, "velocity": 115},
    ]
    return {
        "ok": True,
        "status": "SUCCESS",
        "complete": True,
        "live_changed": False,
        "track_index": 1,
        "track": "SLAP BASS",
        "clip_index": 0,
        "clip": "Funk Groove A",
        "length_beats": 16.0,
        "notes": notes,
        "note_count": len(notes),
        "truncated": False,
    }


def test_midi_clip_read_computes_objective_facts_only_and_uses_only_get_midi_clip_notes():
    clip_calls: list[dict] = []
    other_live_calls: list[str] = []

    def forbid_context_reader():
        other_live_calls.append("context_reader")
        raise AssertionError("context_reader must not be called for Session MIDI clip slice")

    def clip_reader(**kwargs):
        clip_calls.append(kwargs)
        return _sample_clip_payload()

    orchestrator = JarvisOrchestrator(
        context_reader=forbid_context_reader,
        live_client={
            "ableton_status": lambda: other_live_calls.append("ableton_status") or {"ok": True},
            "ableton_list_tracks": lambda: other_live_calls.append("ableton_list_tracks") or {"ok": True},
        },
        clip_reader=clip_reader,
    )
    result = orchestrator.ask("トラック1のクリップ0を評価して")
    assert result["ok"] is True
    assert result["complete"] is True
    assert result["state"] == "COMPLETED"
    assert result["live_changed"] is False
    assert result["executed"] is False
    assert result["musical_quality_claimed"] is False
    assert other_live_calls == []
    assert clip_calls == [{"track_index": 1, "track_name": None, "clip_index": 0}]

    ev = result["clip_evaluation"]
    assert ev["note_count"] == 8
    assert ev["length_beats"] == 16.0
    assert ev["bars"] == 4
    assert ev["bar_notes"] == [2, 2, 3, 1]
    assert sum(ev["bar_notes"]) == ev["note_count"]
    assert ev["notes_per_bar"] == 2.0
    assert ev["pitch_range"] == {"min": 39, "max": 51, "span": 12}
    assert ev["unique_pitches"] == [39, 46, 51]
    assert ev["average_duration_beats"] == 0.4062
    assert ev["velocity_range"] == {"min": 45, "max": 115, "average": 87.5}
    assert ev["offbeat_note_count"] == 3
    assert ev["ghost_note_count"] == 2
    assert ev["timbre"] == "UNKNOWN"
    assert ev["perceived_audio_quality"] == "UNKNOWN"
    assert ev["key"] == "UNKNOWN"
    assert ev["tempo"] == "UNKNOWN"
    assert ev["musical_quality_claimed"] is False


def test_midi_clip_revision_preview_keeps_original_notes_and_never_writes_live():
    payload = _sample_clip_payload()
    original_snapshot = [dict(n) for n in payload["notes"]]

    orchestrator = JarvisOrchestrator(clip_reader=lambda **_kw: payload)
    result = orchestrator.ask("トラック「SLAP BASS」のクリップ0の後半を減らして")
    assert result["ok"] is True
    assert result["complete"] is True
    assert result["state"] == "APPROVAL_REQUIRED"
    assert result["intent"]["type"] == "modify_midi_clip"
    assert result["intent"]["write_requested"] is True
    assert result["plan"]["requires_human_approval"] is True
    assert result["plan"]["executable"] is False
    assert result["live_changed"] is False
    assert result["executed"] is False

    preview = result["clip_preview"]
    assert preview["preview_only"] is True
    assert preview["action"] == "reduce_density"
    assert preview["target_bars"] == {"start": 3, "end": 4}
    assert preview["original_notes"] == original_snapshot
    assert payload["notes"] == original_snapshot
    assert preview["note_count_before"] == 8
    assert preview["note_count_after"] < preview["note_count_before"]
    assert preview["bar_notes_before"][:2] == preview["bar_notes_after"][:2]
    assert sum(preview["bar_notes_after"][2:]) < sum(preview["bar_notes_before"][2:])
    assert preview["applied_to_live"] is False
    assert preview["live_changed"] is False
    assert preview["saved"] is False
    assert preview["played"] is False
    assert preview["exported"] is False


def test_midi_clip_ambiguous_duplicate_invalid_or_non_midi_targets_stay_unknown():
    calls: list[dict] = []

    def clip_reader(**kwargs):
        calls.append(kwargs)
        return _sample_clip_payload()

    orchestrator = JarvisOrchestrator(clip_reader=clip_reader)

    for bad_utterance in (
        "クリップ0を読んで",
        "トラック1のクリップを読んで",
        "トラック0と1のクリップ0を読んで",
        "トラック0のクリップ0と1を読んで",
        "トラック0「BASS」のクリップ0を読んで",
        "トラック-1のクリップ0を読んで",
        "トラック0のクリップ0の4から2を減らして",
    ):
        res = orchestrator.ask(bad_utterance)
        assert res["ok"] is False, bad_utterance
        assert res["state"] == "UNKNOWN", bad_utterance
        assert res["complete"] is False, bad_utterance
        assert res["clip_evaluation"] is None, bad_utterance
        assert res["clip_preview"] is None, bad_utterance
        assert res["live_changed"] is False, bad_utterance
    assert calls == []

    both_specified = orchestrator.inspect_midi_clip(track_index=0, track_name="BASS", clip_index=0)
    assert both_specified["ok"] is False and both_specified["state"] == "UNKNOWN"
    conflict = orchestrator.ask("トラック0のクリップ0を読んで", track_index=1, clip_index=0)
    assert conflict["ok"] is False and conflict["state"] == "UNKNOWN"
    assert calls == []

    non_midi = JarvisOrchestrator(
        clip_reader=lambda **_kw: {
            "ok": False,
            "status": "FAILED",
            "complete": False,
            "live_changed": False,
            "error": "target clip slot does not contain a MIDI clip",
        }
    ).ask("トラック0のクリップ0を読んで")
    assert non_midi["ok"] is False
    assert non_midi["state"] == "UNKNOWN"
    assert non_midi["complete"] is False
    assert non_midi["clip_evaluation"] is None
    assert non_midi["clip_preview"] is None
    assert "MIDI" in non_midi["reply"]


def test_midi_clip_incomplete_truncated_malformed_or_failed_reads_stop_evaluation():
    truncated = JarvisOrchestrator(
        clip_reader=lambda **_kw: {
            **_sample_clip_payload(),
            "ok": False,
            "status": "PARTIAL",
            "complete": False,
            "truncated": True,
            "note_count": 5000,
        }
    ).ask("トラック1のクリップ0を読んで")
    assert truncated["ok"] is False
    assert truncated["state"] == "UNKNOWN"
    assert truncated["complete"] is False
    assert truncated["clip_evaluation"] is None
    assert truncated["clip_preview"] is None

    incomplete = JarvisOrchestrator(
        clip_reader=lambda **_kw: {
            **_sample_clip_payload(),
            "complete": False,
        }
    ).ask("トラック1のクリップ0の後半を減らして")
    assert incomplete["ok"] is False
    assert incomplete["state"] == "UNKNOWN"
    assert incomplete["complete"] is False
    assert incomplete["clip_evaluation"] is None
    assert incomplete["clip_preview"] is None

    malformed = JarvisOrchestrator(
        clip_reader=lambda **_kw: {
            **_sample_clip_payload(),
            "notes": [{"pitch": 200, "start_time": 0.0, "duration": 0.5, "velocity": 100}],
            "note_count": 1,
        }
    ).ask("トラック1のクリップ0を読んで")
    assert malformed["ok"] is False
    assert malformed["state"] == "UNKNOWN"
    assert malformed["complete"] is False
    assert malformed["clip_evaluation"] is None
    assert malformed["clip_preview"] is None

    def explode(**_kw):
        raise RuntimeError("socket dropped mid-read")

    crashed = JarvisOrchestrator(clip_reader=explode).ask("トラック1のクリップ0を読んで")
    assert crashed["ok"] is False
    assert crashed["state"] == "UNKNOWN"
    assert crashed["complete"] is False
    assert crashed["clip_evaluation"] is None
    assert crashed["clip_preview"] is None
    assert crashed["live_changed"] is False


def test_midi_clip_identity_mismatch_or_missing_is_not_another_clips_data():
    payload = _sample_clip_payload()
    cases = [
        ("トラック1のクリップ0を読んで", {**payload, "track_index": 2}),
        ("トラック1のクリップ0を読んで", {**payload, "clip_index": 1}),
        ("トラック「SLAP BASS」のクリップ0を読んで", {**payload, "track": "SUB BASS"}),
        ("トラック1のクリップ0を読んで", {k: v for k, v in payload.items() if k != "track_index"}),
        ("トラック1のクリップ0を読んで", {k: v for k, v in payload.items() if k != "clip_index"}),
        ("トラック1のクリップ0を読んで", {k: v for k, v in payload.items() if k != "clip"}),
        ("トラック「SLAP BASS」のクリップ0を読んで", {k: v for k, v in payload.items() if k != "track"}),
    ]
    for utterance, raw in cases:
        res = JarvisOrchestrator(clip_reader=lambda _raw=raw, **_kw: _raw).ask(utterance)
        assert res["ok"] is False, raw
        assert res["state"] == "UNKNOWN", raw
        assert res["complete"] is False, raw
        assert res["clip_evaluation"] is None, raw
        assert res["clip_preview"] is None, raw
        assert res["live_changed"] is False
        assert "識別情報" in res["reply"]


def test_midi_clip_bar_range_beyond_clip_length_is_not_clamped():
    payload = _sample_clip_payload()  # 16 beats = 4 bars
    original = [dict(n) for n in payload["notes"]]
    for start, end in ((2, 8), (5, 8)):
        res = JarvisOrchestrator(clip_reader=lambda **_kw: payload).inspect_midi_clip(
            track_index=1, clip_index=0, direction="reduce_density", bar_range={"start": start, "end": end}
        )
        assert res["ok"] is False
        assert res["state"] == "UNKNOWN"
        assert "COMPLETED" not in res["states"]
        assert "APPROVAL_REQUIRED" not in res["states"]
        assert f"{start}–{end}小節" in res["reply"] and "4小節" in res["reply"]
        selected = res["clip_evaluation"]["selected_bars"]
        assert selected["out_of_range"] is True
        assert (selected["start_bar"], selected["end_bar"]) == (start, end)
        preview = res["clip_preview"]
        assert preview["out_of_range"] is True
        assert preview["requested_bars"] == {"start": start, "end": end}
        assert preview["target_bars"] is None
        assert preview["changed"] is False
        assert preview["preview_notes"] == preview["original_notes"] == original
        assert res["live_changed"] is False

    inside = JarvisOrchestrator(clip_reader=lambda **_kw: payload).inspect_midi_clip(
        track_index=1, clip_index=0, direction="reduce_density", bar_range={"start": 3, "end": 4}
    )
    assert inside["ok"] is True and inside["state"] == "APPROVAL_REQUIRED"
    assert inside["clip_preview"]["target_bars"] == {"start": 3, "end": 4}
    assert inside["clip_preview"]["out_of_range"] is False
