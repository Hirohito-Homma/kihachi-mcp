import json
from pathlib import Path

from live_fixtures import gate

from kihachi_mcp.services.brief_parser import extract_explicit
from kihachi_mcp.services.live_execution_service import LiveExecutionService
from kihachi_mcp.services.live_state_inspector import LiveStateInspector
from kihachi_mcp.services.live_transport_fake import FakeLiveSet, FakeLiveTransport
from kihachi_mcp.services.midi_candidate_builder import build_candidate
from kihachi_mcp.services.studio_interpreter import assemble_brief
from kihachi_mcp.services.studio_runtime import StudioRuntime


def _candidate():
    brief = assemble_brief(
        extract_explicit("32小節 125 BPM Dマイナー。17小節目からドロップにして"),
        {
            "genre": "tech_house",
            "tempo": 125,
            "bars": 32,
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
    return build_candidate(brief, seed=4, candidate_id="runtime01deadbeef")


def _runtime(
    tmp_path: Path,
    live: FakeLiveSet | None = None,
    candidate_dir: Path | None = None,
    **transport_kwargs,
):
    transport = FakeLiveTransport(
        live or FakeLiveSet(live_version="12.4.3", tempo=125),
        **transport_kwargs,
    )
    inspector = LiveStateInspector(transport)
    approvals = gate(tmp_path)
    executor = LiveExecutionService(
        transport=transport, inspector=inspector, approval_gate=approvals
    )
    runtime = StudioRuntime(
        transport=transport,
        inspector=inspector,
        executor=executor,
        gate=approvals,
        export_dir=tmp_path / "exports",
        candidate_dir=candidate_dir,
    )
    candidate = _candidate()
    runtime._store(candidate)
    return runtime, candidate, transport


def test_export_and_apply_keep_preview_notes(tmp_path: Path) -> None:
    runtime, candidate, _transport = _runtime(tmp_path)
    exported = runtime.export_midi(candidate.candidate_id)
    assert exported["ok"] is True
    preview = runtime.apply_preview(candidate.candidate_id)
    assert preview["ok"] is True
    assert preview["notes_match_preview"] is True
    assert preview["summary"]["note_count"] == candidate.note_count()
    result = runtime.apply(candidate.candidate_id, confirmed=True)
    assert result["ok"] is True
    assert result["receipt"]["status"] == "verified"
    assert result["musical_quality_claimed"] is False
    assert result["sound_coverage"]["has_missing_sounds"] is False
    assert any(
        "Drum Rack" in item for item in result["sound_coverage"]["automatic"]
    )


def test_duplicate_apply_is_refused(tmp_path: Path) -> None:
    runtime, candidate, _transport = _runtime(tmp_path)
    first = runtime.apply(candidate.candidate_id, confirmed=True)
    second = runtime.apply(candidate.candidate_id, confirmed=True)
    assert first["ok"] is True
    assert second["ok"] is False
    assert "すでに適用" in second["error"]


def test_partial_failure_is_reported_and_not_retried(tmp_path: Path) -> None:
    runtime, candidate, transport = _runtime(tmp_path, disconnect_after=2)
    result = runtime.apply(candidate.candidate_id, confirmed=True)
    assert result["ok"] is False
    assert result["receipt"]["status"] in {"partially_applied", "blocked"}
    if result["receipt"]["status"] == "partially_applied":
        assert result["partial"] is True
        assert result["receipt"]["completed_operations"]
    again = runtime.apply(candidate.candidate_id, confirmed=True)
    assert again["ok"] is False
    assert transport.request_count >= 2


def test_fill_drum_samples_occupies_empty_pads(tmp_path: Path) -> None:
    runtime, candidate, transport = _runtime(tmp_path)
    runtime.apply(candidate.candidate_id, confirmed=True)
    kick = next(
        track for track in transport.live_set.tracks if "Kick" in track["name"]
    )
    assert {int(pad["note"]) for pad in kick["occupied_pads"]} == {36}


def test_inspect_coverage_lists_leftover_kihachi_tracks(tmp_path: Path) -> None:
    live = FakeLiveSet(live_version="12.4.3", tempo=125)
    live.add_track("KIHACHI Kick ca90283e [KIHACHI]")
    runtime, candidate, _transport = _runtime(tmp_path, live)
    runtime.apply(candidate.candidate_id, confirmed=True)
    coverage = runtime.inspect_coverage(candidate.candidate_id)
    assert coverage["ok"] is True
    leftovers = coverage["sound_coverage"]["leftover_tracks"]
    assert "KIHACHI Kick ca90283e [KIHACHI]" in leftovers
    assert all(candidate.candidate_id[:8] not in name for name in leftovers)


def test_materialize_rebuilds_clips_without_ollama(tmp_path: Path) -> None:
    runtime, candidate, _transport = _runtime(tmp_path)
    rebuilt = runtime.materialize(candidate.brief, seed=candidate.seed)
    assert rebuilt["ok"] is True
    assert rebuilt["candidate"]["seed"] == candidate.seed
    assert rebuilt["candidate"]["candidate_id"] != candidate.candidate_id
    assert rebuilt["candidate"]["note_counts"]["total"] == candidate.note_count()


def test_unconfirmed_apply_does_nothing(tmp_path: Path) -> None:
    runtime, candidate, transport = _runtime(tmp_path)
    result = runtime.apply(candidate.candidate_id, confirmed=False)
    assert result["ok"] is False
    assert transport.applied_operation_ids == []


def _restarted(tmp_path: Path, transport: FakeLiveTransport, candidate_dir: Path):
    """A second runtime over the same Live and candidate folder, nothing stored."""
    inspector = LiveStateInspector(transport)
    approvals = gate(tmp_path)
    return StudioRuntime(
        transport=transport,
        inspector=inspector,
        executor=LiveExecutionService(
            transport=transport, inspector=inspector, approval_gate=approvals
        ),
        gate=approvals,
        export_dir=tmp_path / "exports",
        candidate_dir=candidate_dir,
    )


def test_a_saved_candidate_survives_a_restart_with_the_same_notes(tmp_path: Path) -> None:
    saved = tmp_path / "candidates"
    _runtime_a, candidate, transport = _runtime(tmp_path, candidate_dir=saved)
    restarted = _restarted(tmp_path, transport, saved)
    reloaded = restarted.get_candidate(candidate.candidate_id)
    assert reloaded is not None
    assert reloaded.note_fingerprint == candidate.note_fingerprint
    assert restarted.selected_candidate() == reloaded
    preview = restarted.apply_preview(candidate.candidate_id)
    assert preview["notes_match_preview"] is True


def test_a_restart_does_not_allow_a_second_apply(tmp_path: Path) -> None:
    saved = tmp_path / "candidates"
    runtime, candidate, transport = _runtime(tmp_path, candidate_dir=saved)
    assert runtime.apply(candidate.candidate_id, confirmed=True)["ok"] is True
    before = list(transport.applied_operation_ids)
    again = _restarted(tmp_path, transport, saved).apply(
        candidate.candidate_id, confirmed=True
    )
    assert again["ok"] is False
    assert "すでに適用" in again["error"]
    assert transport.applied_operation_ids == before


def test_a_saved_candidate_whose_notes_changed_is_not_loaded(tmp_path: Path) -> None:
    saved = tmp_path / "candidates"
    _runtime_a, candidate, transport = _runtime(tmp_path, candidate_dir=saved)
    path = saved / f"{candidate.candidate_id}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["clips"][0]["notes"][0]["pitch"] += 1
    path.write_text(json.dumps(data), encoding="utf-8")
    assert _restarted(tmp_path, transport, saved).get_candidate(candidate.candidate_id) is None


def test_a_candidate_id_cannot_name_a_path_outside_the_folder(tmp_path: Path) -> None:
    saved = tmp_path / "candidates"
    runtime, _candidate_a, _transport = _runtime(tmp_path, candidate_dir=saved)
    (tmp_path / "outside.json").write_text("{}", encoding="utf-8")
    assert runtime.get_candidate("../outside") is None


def _applied_tracks_set(short_id: str, *, playing: bool = False) -> FakeLiveSet:
    live = FakeLiveSet(live_version="12.4.3", tempo=120, is_playing=playing)
    for part in ("Kick", "Hats", "Bass", "Stab"):
        track = live.add_track(f"KIHACHI {part} {short_id} [KIHACHI]")
        if part in {"Kick", "Hats"}:
            track["device_names"].append("Drum Rack")
    return live


def test_samples_go_onto_applied_tracks_by_short_id_after_a_restart(tmp_path: Path) -> None:
    live = _applied_tracks_set("abab0001")
    runtime, _candidate_a, transport = _runtime(tmp_path, live)
    result = runtime.fill_drum_samples("abab0001", confirmed=True)
    assert result["ok"] is True
    assert result["receipt"]["status"] == "verified"
    assert result["musical_quality_claimed"] is False
    pads = {
        track["name"]: {int(pad["note"]) for pad in track.get("occupied_pads") or []}
        for track in transport.live_set.tracks
    }
    assert pads["KIHACHI Kick abab0001 [KIHACHI]"] == {36}
    assert pads["KIHACHI Hats abab0001 [KIHACHI]"] == {42}
    assert pads["KIHACHI Bass abab0001 [KIHACHI]"] == set()


def test_short_id_fill_leaves_an_occupied_pad_alone(tmp_path: Path) -> None:
    live = _applied_tracks_set("abab0002")
    kick = live.tracks[0]
    kick["occupied_pads"] = [{"note": 36, "name": "user kick", "chain_count": 1}]
    runtime, _candidate_a, transport = _runtime(tmp_path, live)
    runtime.fill_drum_samples("abab0002", confirmed=True)
    assert transport.live_set.tracks[0]["occupied_pads"] == [
        {"note": 36, "name": "user kick", "chain_count": 1}
    ]


def test_short_id_fill_is_refused_while_live_plays(tmp_path: Path) -> None:
    runtime, _candidate_a, transport = _runtime(
        tmp_path, _applied_tracks_set("abab0003", playing=True)
    )
    result = runtime.fill_drum_samples("abab0003", confirmed=True)
    assert result["ok"] is False
    assert "再生" in result["error"]
    assert transport.applied_operation_ids == []


def test_short_id_fill_needs_confirmation_and_a_real_short_id(tmp_path: Path) -> None:
    runtime, _candidate_a, transport = _runtime(tmp_path, _applied_tracks_set("abab0004"))
    assert runtime.fill_drum_samples("abab0004", confirmed=False)["ok"] is False
    assert runtime.fill_drum_samples("ABAB0004", confirmed=True)["ok"] is False
    assert runtime.fill_drum_samples("abab", confirmed=True)["ok"] is False
    assert transport.applied_operation_ids == []


def test_short_id_fill_with_no_matching_tracks_says_so(tmp_path: Path) -> None:
    runtime, _candidate_a, transport = _runtime(tmp_path, _applied_tracks_set("abab0005"))
    result = runtime.fill_drum_samples("cdcd0005", confirmed=True)
    assert result["ok"] is False
    assert any("cdcd0005" in warning for warning in result["warnings"])
    assert transport.applied_operation_ids == []
