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


def test_kick_replacement_requires_preview_then_changes_only_kick(tmp_path: Path) -> None:
    runtime, candidate, transport = _runtime(tmp_path)
    assert runtime.apply(candidate.candidate_id, confirmed=True)["ok"] is True
    replacement = tmp_path / "heavy-kick.wav"
    replacement.write_bytes(b"heavy")
    kick = next(track for track in transport.live_set.tracks if "Kick" in track["name"])
    hats = next(track for track in transport.live_set.tracks if "Hats" in track["name"])
    hats_before = [dict(pad) for pad in hats["occupied_pads"]]

    preview = runtime.sample_replacement_preview(candidate.candidate_id, str(replacement))

    assert preview["ok"] is True
    assert preview["applied_to_live"] is False
    assert kick["occupied_pads"][0]["sample_path"] != str(replacement)
    refused = runtime.apply_sample_replacement(preview["replacement_id"], confirmed=False)
    assert refused["ok"] is False
    result = runtime.apply_sample_replacement(preview["replacement_id"], confirmed=True)
    assert result["ok"] is True
    assert kick["occupied_pads"][0]["sample_path"] == str(replacement)
    assert hats["occupied_pads"] == hats_before


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


def _arrangement_placements(transport: FakeLiveTransport) -> list:
    return [
        clip
        for clip in transport.live_set.arrangement_clips
        if "[K:" in clip.name
    ]


def test_an_applied_candidate_expands_to_its_bars_in_the_arrangement(tmp_path: Path) -> None:
    runtime, candidate, transport = _runtime(tmp_path)
    assert runtime.apply(candidate.candidate_id, confirmed=True)["ok"] is True
    preview = runtime.arrangement_preview(candidate.candidate_id)
    assert preview["ok"] is True
    assert preview["summary"]["clip_count"] == len(candidate.clips)
    assert preview["summary"]["bars"] == candidate.brief.bars.value
    result = runtime.expand_arrangement(candidate.candidate_id, confirmed=True)
    assert result["ok"] is True
    assert result["receipt"]["status"] == "verified"
    assert result["musical_quality_claimed"] is False
    placed = _arrangement_placements(transport)
    assert len(placed) == len(candidate.clips)
    beats = candidate.brief.beats_per_bar
    expected = sorted(
        ((clip.start_bar - 1) * beats, clip.length_bars * beats, len(clip.notes))
        for clip in candidate.clips
    )
    assert sorted((c.start_beats, c.length_beats, c.note_count) for c in placed) == expected
    locators = {item["beats"] for item in transport.live_set.locators}
    assert locators == {(section.start_bar - 1) * beats for section in candidate.brief.sections}


def test_arrangement_expansion_runs_once_even_after_a_restart(tmp_path: Path) -> None:
    saved = tmp_path / "candidates"
    runtime, candidate, transport = _runtime(tmp_path, candidate_dir=saved)
    runtime.apply(candidate.candidate_id, confirmed=True)
    assert runtime.expand_arrangement(candidate.candidate_id, confirmed=True)["ok"] is True
    count = len(transport.live_set.arrangement_clips)
    again = runtime.expand_arrangement(candidate.candidate_id, confirmed=True)
    assert again["ok"] is False
    assert "試行済み" in again["error"]
    restarted = _restarted(tmp_path, transport, saved)
    assert restarted.expand_arrangement(candidate.candidate_id, confirmed=True)["ok"] is False
    assert restarted.arrangement_preview(candidate.candidate_id)["summary"]["already_expanded"]
    assert len(transport.live_set.arrangement_clips) == count


def test_arrangement_expansion_needs_the_session_clips_first(tmp_path: Path) -> None:
    runtime, candidate, transport = _runtime(tmp_path)
    preview = runtime.arrangement_preview(candidate.candidate_id)
    assert preview["ok"] is False
    assert any(item["kind"] == "session_clip_missing" for item in preview["conflicts"])
    assert runtime.expand_arrangement(candidate.candidate_id, confirmed=True)["ok"] is False
    assert transport.live_set.arrangement_clips == []


def test_arrangement_expansion_never_overlaps_an_existing_clip(tmp_path: Path) -> None:
    from kihachi_mcp.services.live_transport_fake import FakeArrangementClip

    runtime, candidate, transport = _runtime(tmp_path)
    runtime.apply(candidate.candidate_id, confirmed=True)
    kick = next(t for t in transport.live_set.tracks if "Kick" in t["name"])
    mine = FakeArrangementClip(kick["index"], "my edit", 8.0, 4.0)
    transport.live_set.arrangement_clips.append(mine)
    result = runtime.expand_arrangement(candidate.candidate_id, confirmed=True)
    assert result["ok"] is False
    assert any(
        item["kind"] == "arrangement_range_occupied" for item in result["conflicts"]
    )
    assert transport.live_set.arrangement_clips == [mine]
    assert transport.live_set.locators == []


def test_arrangement_expansion_is_refused_while_live_plays(tmp_path: Path) -> None:
    runtime, candidate, transport = _runtime(tmp_path)
    runtime.apply(candidate.candidate_id, confirmed=True)
    transport.live_set.is_playing = True
    result = runtime.expand_arrangement(candidate.candidate_id, confirmed=True)
    assert result["ok"] is False
    assert transport.live_set.arrangement_clips == []


def test_unconfirmed_arrangement_expansion_does_nothing(tmp_path: Path) -> None:
    runtime, candidate, transport = _runtime(tmp_path)
    runtime.apply(candidate.candidate_id, confirmed=True)
    assert runtime.expand_arrangement(candidate.candidate_id, confirmed=False)["ok"] is False
    assert transport.live_set.arrangement_clips == []


def test_the_default_runtime_expands_beside_existing_arrangement_clips(
    tmp_path: Path,
) -> None:
    """Planning and execution must read the Set the same way.

    The Set fingerprint covers Arrangement clips. If execution re-read Live
    without them, any Set with an Arrangement clip would look changed and the
    expansion would be refused every time.
    """
    from kihachi_mcp.services.live_transport_fake import FakeArrangementClip

    live = FakeLiveSet(live_version="12.4.3", tempo=125)
    user = live.add_track("My Synth")
    live.arrangement_clips.append(FakeArrangementClip(user["index"], "user take", 0.0, 64.0))
    transport = FakeLiveTransport(live)
    runtime = StudioRuntime(transport=transport, gate=gate(tmp_path))
    candidate = _candidate()
    runtime._store(candidate)
    assert runtime.apply(candidate.candidate_id, confirmed=True)["ok"] is True
    result = runtime.expand_arrangement(candidate.candidate_id, confirmed=True)
    assert result["ok"] is True, result.get("error") or result.get("receipt")
    assert len(_arrangement_placements(transport)) == len(candidate.clips)


def test_skip_instruments_leaves_the_tracks_for_another_loader(tmp_path: Path) -> None:
    """AbletonGPT's kit loader refuses a track that already has an instrument."""
    runtime, candidate, transport = _runtime(tmp_path)
    preview = runtime.apply_preview(candidate.candidate_id, skip_instruments=True)
    ops = {operation["op"] for operation in preview["plan"]["operations"]}
    assert "load_live_device" not in ops
    assert "load_drum_pad_sample" not in ops
    assert preview["summary"]["skip_instruments"] is True
    result = runtime.apply(candidate.candidate_id, confirmed=True, skip_instruments=True)
    assert result["ok"] is True
    kihachi = [t for t in transport.live_set.tracks if "[KIHACHI]" in t["name"]]
    assert len(kihachi) == len(candidate.parts)
    assert all(track["device_names"] == [] for track in kihachi)
    assert result["sound_coverage"]["has_missing_sounds"] is True
