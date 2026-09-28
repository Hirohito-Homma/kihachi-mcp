"""Studio workflow: review, revision, approval, dry run, send and read-back."""

import json
from pathlib import Path

import pytest
from live_fixtures import gate

from kihachi_mcp.knowledge.genre_database import match_genres
from kihachi_mcp.services.live_execution_service import LiveExecutionService
from kihachi_mcp.services.live_state_inspector import LiveStateInspector
from kihachi_mcp.services.live_transport_fake import FakeLiveSet, FakeLiveTransport
from kihachi_mcp.services.studio_interpreter import (
    InferenceCancelled,
    InterpretationError,
    interpret_with_fallback,
    parse_model_json,
)
from kihachi_mcp.services.studio_runtime import StudioRuntime
from kihachi_mcp.services.studio_workflow import readback_verification

GOLDEN_PATH_1 = """Create a 64-bar Tech House / Dub Techno track.
122 BPM
D minor
Deep bass
Four-on-the-floor kick
Dub chord stabs
Minimal synth
DJ-friendly intro and outro."""

KIHACHI_GOLDEN_PATH = """110 BPM、D# minor。
Mutation Funk × Dub × Tech House。
ファンキーなスラップベース。シンコペーション。Ghost notes。Octave movement。
4つ打ちKick。Tight snare。
Dub chord。Mutation synth。Vocoder。
Swing 54%。
Energetic、Deep、Funky、Underground。
約5分。"""

SMOKE_TEST = "KIHACHI STUDIO SMOKE TEST。120 BPM、Cマイナー、32小節"


def _studio(tmp_path: Path, live: FakeLiveSet | None = None, saved: bool = True):
    live = live or FakeLiveSet(live_version="12.4.3", tempo=120)
    transport = FakeLiveTransport(live)
    inspector = LiveStateInspector(transport)
    approvals = gate(tmp_path)
    runtime = StudioRuntime(
        transport=transport,
        inspector=inspector,
        executor=LiveExecutionService(
            transport=transport, inspector=inspector, approval_gate=approvals
        ),
        gate=approvals,
        export_dir=tmp_path / "exports",
        candidate_dir=tmp_path / "candidates" if saved else None,
    )
    runtime.update_settings({"ai_provider": "deterministic"}, persist=False)
    return runtime, live


def _create(runtime: StudioRuntime, brief: str) -> str:
    result = runtime.generate(brief, seed=7)
    assert result["ok"] is True, result
    return result["candidate"]["candidate_id"]


def test_health_names_every_component_with_a_word(tmp_path: Path) -> None:
    runtime, _live = _studio(tmp_path)
    health = runtime.health()
    statuses = {item["name"]: item["status"] for item in health["statuses"]}
    assert set(statuses) == {"Ollama", "kihachi-mcp", "AbletonGPT", "Ableton Live", "Project"}
    assert statuses["Ableton Live"] == "READY"
    assert set(statuses.values()) <= {"READY", "WARNING", "OFFLINE"}


def test_studio_without_live_reports_offline_and_keeps_working(tmp_path: Path) -> None:
    runtime = StudioRuntime(candidate_dir=tmp_path / "candidates")
    runtime.update_settings({"ai_provider": "deterministic"}, persist=False)
    statuses = {item["name"]: item["status"] for item in runtime.health()["statuses"]}
    assert statuses["Ableton Live"] == "OFFLINE"
    candidate_id = _create(runtime, SMOKE_TEST)
    assert runtime.review(candidate_id)["ok"] is True
    runtime.approve(candidate_id)
    dry = runtime.dry_run(candidate_id)
    assert dry["ok"] is True and dry["can_send"] is False
    assert any("EXTERNAL VERIFICATION REQUIRED" in item for item in dry["blockers"])
    assert dry["plan"]["tempo"] == 120
    assert runtime.verify(candidate_id)["status"] == "EXTERNAL VERIFICATION REQUIRED"


def test_send_refuses_an_unapproved_candidate(tmp_path: Path) -> None:
    runtime, live = _studio(tmp_path)
    candidate_id = _create(runtime, SMOKE_TEST)
    result = runtime.send_to_ableton(candidate_id, confirmed=True)
    assert result["ok"] is False
    assert "承認" in result["error"]
    assert live.tracks == []


def test_smoke_test_sends_and_reads_back_from_live(tmp_path: Path) -> None:
    runtime, live = _studio(tmp_path)
    candidate_id = _create(runtime, SMOKE_TEST)
    runtime.approve(candidate_id)
    dry = runtime.dry_run(candidate_id, change_tempo=True)
    assert dry["can_send"] is True, dry["blockers"]
    assert dry["changes_live"] is False and live.tracks == []
    result = runtime.send_to_ableton(candidate_id, confirmed=True, change_tempo=True)
    assert result["receipt"]["status"] == "verified"
    verification = result["verification"]
    lines = {line["name"]: line["status"] for line in verification["lines"]}
    assert lines == {
        "Tempo": "PASS",
        "Tracks": "PASS",
        "Clips": "PASS",
        "Notes": "PASS",
        "Arrangement": "SKIP",
    }
    assert verification["status"] == "PROJECT READY"
    assert runtime.send_to_ableton(candidate_id, confirmed=True)["ok"] is False


def test_verification_fails_when_live_lost_a_note(tmp_path: Path) -> None:
    runtime, live = _studio(tmp_path)
    candidate_id = _create(runtime, SMOKE_TEST)
    runtime.approve(candidate_id)
    runtime.send_to_ableton(candidate_id, confirmed=True, change_tempo=True)
    clip = next(iter(live.session_clips.values()))
    clip.notes.pop()
    report = runtime.verify(candidate_id)
    notes = next(line for line in report["lines"] if line["name"] == "Notes")
    assert notes["status"] == "FAIL"
    assert report["status"] == "VERIFICATION FAILED"


def test_verification_survives_a_restart(tmp_path: Path) -> None:
    runtime, live = _studio(tmp_path)
    candidate_id = _create(runtime, SMOKE_TEST)
    runtime.approve(candidate_id)
    runtime.send_to_ableton(candidate_id, confirmed=True, change_tempo=True)
    restarted, _ = _studio(tmp_path, live=live)
    assert restarted.is_approved(candidate_id)
    report = restarted.verify(candidate_id)
    assert report["status"] == "PROJECT READY"


def test_tempo_left_unchanged_is_skip_not_pass(tmp_path: Path) -> None:
    runtime, _live = _studio(tmp_path, live=FakeLiveSet(live_version="12.4.3", tempo=128))
    candidate_id = _create(runtime, SMOKE_TEST)
    runtime.approve(candidate_id)
    result = runtime.send_to_ableton(candidate_id, confirmed=True)
    tempo = next(line for line in result["verification"]["lines"] if line["name"] == "Tempo")
    assert tempo["status"] == "SKIP"
    assert result["verification"]["ok"] is True


def test_revision_is_a_proposal_until_the_human_accepts(tmp_path: Path) -> None:
    runtime, _live = _studio(tmp_path)
    parent = _create(runtime, SMOKE_TEST)
    runtime.approve(parent)
    proposal = runtime.propose_revision(parent, scopes=["bass"], bars=(9, 16))
    assert proposal["ok"] is True
    assert runtime.selected_id() == parent
    decided = runtime.decide_revision(proposal["revision"]["revision_id"], accept=True)
    child = decided["selected_candidate_id"]
    assert child != parent
    assert runtime.is_approved(child) is False
    before = runtime.get_candidate(parent)
    after = runtime.get_candidate(child)
    for old, new in zip(before.clips, after.clips, strict=True):
        if old.part != "Bass" or not 9 <= old.start_bar <= 16:
            assert old == new
    changed = [
        clip for old, clip in zip(before.clips, after.clips, strict=True) if old != clip
    ]
    assert changed and all(clip.part == "Bass" for clip in changed)
    project = runtime.project(child)
    assert [item["decision"] for item in project["revisions"]] == ["accepted"]
    history = json.loads((tmp_path / "candidates" / ".kihachi-revisions.json").read_text())
    assert history[-1]["child_candidate_id"] == child


def test_rejected_revision_keeps_the_parent(tmp_path: Path) -> None:
    runtime, _live = _studio(tmp_path)
    parent = _create(runtime, SMOKE_TEST)
    proposal = runtime.propose_revision(parent, scopes=["drums"])
    decided = runtime.decide_revision(proposal["revision"]["revision_id"], accept=False)
    assert decided["decision"] == "rejected"
    assert runtime.selected_id() == parent
    assert runtime.decide_revision(proposal["revision"]["revision_id"], accept=True)["ok"] is False


def test_ignored_issue_leaves_the_review_and_cannot_be_revised(tmp_path: Path) -> None:
    runtime, _live = _studio(tmp_path)
    candidate_id = _create(runtime, KIHACHI_GOLDEN_PATH)
    issues = runtime.review(candidate_id)["issues"]
    assert issues, "the KIHACHI fixture is expected to raise at least one issue"
    target = issues[0]["id"]
    review = runtime.ignore_issue(candidate_id, target)["review"]
    assert target not in {item["id"] for item in review["issues"]}
    assert target in {item["id"] for item in review["ignored"]}
    assert runtime.propose_revision(candidate_id, issue_ids=[target])["ok"] is False


def test_the_recommended_kick_bass_fix_clears_its_issue(tmp_path: Path) -> None:
    runtime, _live = _studio(tmp_path)
    parent = _create(runtime, KIHACHI_GOLDEN_PATH)
    if "kick-bass" not in {item["id"] for item in runtime.review(parent)["issues"]}:
        pytest.skip("this seed has no kick/bass collision")
    proposal = runtime.propose_revision(parent, issue_ids=["kick-bass"])
    assert proposal["ok"] is True, proposal
    child = runtime.decide_revision(proposal["revision"]["revision_id"], accept=True)
    after = runtime.review(child["selected_candidate_id"])
    assert "kick-bass" not in {item["id"] for item in after["issues"]}


def test_golden_path_1_reaches_project_ready(tmp_path: Path) -> None:
    runtime, _live = _studio(tmp_path)
    candidate_id = _create(runtime, GOLDEN_PATH_1)
    project = runtime.project(candidate_id)
    spec = project["songspec"]
    assert (spec["tempo"], spec["key"], spec["bars"]) == (122, "Dm", 64)
    sections = project["arrangement"]["sections"]
    assert sections[0]["name"] == "Intro" and sections[-1]["name"] == "Outro"
    assert sections[0]["start_bar"] == 1
    assert sections[-1]["end_bar"] == 64
    _send_expand_verify(runtime, candidate_id)


def test_kihachi_golden_path_shapes_the_notes(tmp_path: Path) -> None:
    runtime, _live = _studio(tmp_path)
    candidate_id = _create(runtime, KIHACHI_GOLDEN_PATH)
    candidate = runtime.get_candidate(candidate_id)
    project = runtime.project(candidate_id)
    spec = project["songspec"]
    assert (spec["tempo"], spec["key"], spec["genre"]) == (110, "D#m", "mutation_funk")
    assert spec["swing"] == pytest.approx(0.54)
    assert 4.5 <= spec["duration_minutes"] <= 5.5
    assert "Swing" not in " ".join(candidate.brief.interpretations)
    labels = {track["label"] for track in project["tracks"]}
    assert {"KICK", "DRUMS", "SLAP BASS", "DUB CHORDS", "MUTATION SYNTH"} <= labels
    names = [section["name"] for section in project["arrangement"]["sections"]]
    assert "Break" in names
    energies = {s["name"]: s["energy"] for s in project["arrangement"]["sections"]}
    assert energies["Break"] < energies["Intro"] + 0.2 < max(energies.values())
    bass = [note for clip in candidate.clips_for_part("Bass") for note in clip.notes]
    assert max(n.pitch for n in bass) - min(n.pitch for n in bass) >= 12, "octave movement"
    assert min(n.velocity for n in bass) < 70 <= max(n.velocity for n in bass), "ghost notes"
    offbeats = {
        round(note.start_beats % 1, 2)
        for clip in candidate.clips
        for note in clip.notes
        if 0.3 < note.start_beats % 1 < 0.7
    }
    assert 0.54 in offbeats, "54% swing moves the offbeat eighth"
    _send_expand_verify(runtime, candidate_id)


def _send_expand_verify(runtime: StudioRuntime, candidate_id: str) -> None:
    runtime.approve(candidate_id)
    assert runtime.dry_run(candidate_id, change_tempo=True)["can_send"] is True
    sent = runtime.send_to_ableton(candidate_id, confirmed=True, change_tempo=True)
    assert sent["verification"]["status"] == "PROJECT READY", sent["verification"]
    assert runtime.expand_arrangement(candidate_id, confirmed=True)["ok"] is True
    report = runtime.verify(candidate_id)
    assert {line["name"]: line["status"] for line in report["lines"]} == {
        "Tempo": "PASS",
        "Tracks": "PASS",
        "Clips": "PASS",
        "Notes": "PASS",
        "Arrangement": "PASS",
    }
    assert report["status"] == "PROJECT READY"


def test_settings_accept_only_installed_models(tmp_path: Path, monkeypatch) -> None:
    runtime = StudioRuntime()
    monkeypatch.setattr(
        "kihachi_mcp.services.ai_provider.OllamaProvider.list_models",
        lambda self: ["gemma4:latest", "qwen3:4b"],
    )
    refused = runtime.update_settings({"ollama_model": "llama-huge:70b"})
    assert refused["ok"] is False and "ダウンロード" in refused["error"]
    assert runtime.update_settings({"ollama_url": "http://example.com:11434"})["ok"] is False
    saved = runtime.update_settings({"ollama_model": "qwen3:4b"})
    assert saved["ok"] is True
    stored = json.loads(Path(__import__("os").environ["KIHACHI_SETTINGS_FILE"]).read_text())
    assert stored == {"ai_provider": "ollama", "ollama_url": "http://127.0.0.1:11434", "ollama_model": "qwen3:4b"}
    assert StudioRuntime().settings()["ollama_model"] == "qwen3:4b"


def test_swing_amount_is_not_the_swing_genre() -> None:
    names = [match.genre.slug for match in match_genres("Mutation Funk。Swing 54%。")]
    assert names == ["mutation_funk"]
    assert "swing" in [match.genre.slug for match in match_genres("Swing jazz の曲")]


def test_model_json_is_repaired_only_around_one_object() -> None:
    assert parse_model_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_model_json('Here: {"a": 1} done') == {"a": 1}
    with pytest.raises(InterpretationError):
        parse_model_json("no json at all")


class _Client:
    def __init__(self, replies: list[str], calls: list[int]) -> None:
        self._replies = replies
        self._calls = calls

    def chat(self, payload):
        self._calls.append(1)
        return {"message": {"content": self._replies[min(len(self._calls) - 1, len(self._replies) - 1)]}}


def _intent(**changes) -> str:
    intent = {
        "genre": "tech_house", "tempo": 125, "bars": 64, "key": "Dm", "mood": "",
        "hats_first_half": "normal", "hats_second_half": "normal", "bass_register": "mid",
        "note_density": "normal", "drop_start_bar": 0, "unhandled": [], "ambiguous": [],
    }
    intent.update(changes)
    return json.dumps(intent)


def test_invalid_model_output_is_retried_then_falls_back() -> None:
    calls: list[int] = []
    brief = interpret_with_fallback(
        "122 BPM、Dマイナー、64小節", lambda: _Client(["not json", "{\"tempo\": 1}"], calls), attempts=2
    )
    assert len(calls) == 2
    assert brief.provider == "deterministic"
    assert brief.tempo.value == 122
    assert any("2回とも使えなかった" in item for item in brief.unhandled)


def test_second_attempt_can_succeed() -> None:
    calls: list[int] = []
    brief = interpret_with_fallback(
        "Dマイナー", lambda: _Client(["broken", "```" + _intent(tempo=124) + "```"], calls), attempts=3
    )
    assert len(calls) == 2
    assert brief.tempo.value == 124
    assert brief.provider != "deterministic"


def test_offline_brief_does_not_blame_a_model_that_never_ran() -> None:
    from kihachi_mcp.services.studio_interpreter import interpret_brief_offline

    brief = interpret_brief_offline("120 BPM、Cマイナー、32小節")
    assert brief.contradictions == ()
    assert brief.tempo.value == 120


def test_bare_field_names_from_the_model_are_not_shown() -> None:
    reply = _intent(unhandled=["duration", "ボーカルは入れられません"], ambiguous=["bars"])
    brief = interpret_with_fallback("約5分", lambda: _Client([reply], []), attempts=1)
    assert "duration" not in brief.unhandled
    assert "ボーカルは入れられません" in brief.unhandled
    assert brief.ambiguous == ()


def test_attempts_are_capped_and_cancel_is_not_retried() -> None:
    calls: list[int] = []
    interpret_with_fallback("x", lambda: _Client(["bad"], calls), attempts=99)
    assert len(calls) == 3

    class _Cancelled:
        def chat(self, payload):
            raise InferenceCancelled("stop")

    with pytest.raises(InferenceCancelled):
        interpret_with_fallback("x", _Cancelled, attempts=2)


def test_readback_reports_missing_tracks(tmp_path: Path) -> None:
    runtime, _live = _studio(tmp_path)
    candidate = runtime.get_candidate(_create(runtime, SMOKE_TEST))
    snapshot = runtime._verify_inspector.snapshot()
    report = readback_verification(candidate, snapshot, None, tempo_changed=True, arranged=False)
    tracks = next(line for line in report["lines"] if line["name"] == "Tracks")
    assert tracks["status"] == "FAIL"
    assert report["ok"] is False
