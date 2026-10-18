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


def test_kick_replacement_uses_compact_snapshot_for_plan_and_execute(
    tmp_path: Path,
) -> None:
    class RecordingTransport(FakeLiveTransport):
        def __init__(self) -> None:
            super().__init__(FakeLiveSet(live_version="12.4.3", tempo=125))
            self.state_payloads: list[dict] = []

        def request(
            self, message: dict, *, timeout_seconds: float | None = None
        ) -> dict:
            if message.get("method") == "get_state":
                self.state_payloads.append(dict(message.get("payload") or {}))
            return super().request(message, timeout_seconds=timeout_seconds)

    transport = RecordingTransport()
    runtime = StudioRuntime(
        transport=transport,
        gate=gate(tmp_path),
        export_dir=tmp_path / "exports",
    )
    candidate = _candidate()
    runtime._store(candidate)
    assert runtime.apply(candidate.candidate_id, confirmed=True)["ok"] is True
    transport.state_payloads.clear()

    replacement = tmp_path / "fat-kick.wav"
    replacement.write_bytes(b"fat")
    preview = runtime.sample_replacement_preview(candidate.candidate_id, str(replacement))
    assert preview["ok"] is True
    result = runtime.apply_sample_replacement(preview["replacement_id"], confirmed=True)
    assert result["ok"] is True
    assert transport.state_payloads
    assert all(payload["include_session_clips"] is False for payload in transport.state_payloads)


def test_kick_replacement_refuses_occupied_pad_without_readable_sample(
    tmp_path: Path,
) -> None:
    runtime, candidate, transport = _runtime(tmp_path)
    assert runtime.apply(candidate.candidate_id, confirmed=True)["ok"] is True
    kick = next(track for track in transport.live_set.tracks if "Kick" in track["name"])
    kick["occupied_pads"][0]["sample_path"] = ""
    replacement = tmp_path / "fat-kick.wav"
    replacement.write_bytes(b"fat")

    preview = runtime.sample_replacement_preview(candidate.candidate_id, str(replacement))

    assert preview["ok"] is False
    assert "現在サンプルを読み取れません" in preview["error"]
    assert kick["occupied_pads"][0]["sample_path"] == ""


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


def test_explicit_selection_survives_newer_candidate_and_restart(tmp_path: Path) -> None:
    saved = tmp_path / "candidates"
    runtime, original, transport = _runtime(tmp_path, candidate_dir=saved)
    newer = build_candidate(original.brief, seed=5, candidate_id="runtime02deadbeef")
    runtime._store(newer)
    assert runtime.select(original.candidate_id)["ok"] is True
    restarted = _restarted(tmp_path, transport, saved)
    assert restarted.selected_id() == original.candidate_id
    assert restarted.selected_candidate().note_fingerprint == original.note_fingerprint


def test_invalid_saved_selection_falls_back_to_candidate(tmp_path: Path) -> None:
    saved = tmp_path / "candidates"
    _runtime_a, candidate, transport = _runtime(tmp_path, candidate_dir=saved)
    (saved / ".kihachi-selected.json").write_text(
        '{"candidate_id":"../outside"}', encoding="utf-8"
    )
    assert _restarted(tmp_path, transport, saved).selected_id() == candidate.candidate_id


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


def _expanded(tmp_path: Path, candidate_dir: Path | None = None):
    runtime, candidate, transport = _runtime(tmp_path, candidate_dir=candidate_dir)
    assert runtime.apply(candidate.candidate_id, confirmed=True)["ok"] is True
    result = runtime.expand_arrangement(candidate.candidate_id, confirmed=True)
    assert result["ok"] is True, result
    return runtime, candidate, transport


def _arrangement_check(runtime: StudioRuntime, candidate_id: str) -> tuple[dict, dict]:
    report = runtime.verify(candidate_id)
    line = next(item for item in report["lines"] if item["name"] == "Arrangement")
    return report, line


def test_expansion_is_confirmed_by_a_second_independent_readback(tmp_path: Path) -> None:
    runtime, candidate, transport = _runtime(tmp_path)
    runtime.apply(candidate.candidate_id, confirmed=True)
    reads_before = transport.request_count

    result = runtime.expand_arrangement(candidate.candidate_id, confirmed=True)

    assert result["ok"] is True
    assert result["execution_status"] == "verified"
    assert result["receipt"]["status"] == "verified"
    placements = len(_arrangement_placements(transport))
    locators = len(transport.live_set.locators)
    # preview read + execution read + one call per operation + fingerprint
    # read after execution + the independent re-read.
    assert transport.request_count - reads_before == placements + locators + 4


def test_an_untouched_arrangement_passes_with_counts(tmp_path: Path) -> None:
    runtime, candidate, _transport = _expanded(tmp_path)

    report, line = _arrangement_check(runtime, candidate.candidate_id)

    assert line["status"] == "PASS", line
    assert f"クリップ {len(candidate.clips)} 個" in line["detail"]
    assert f"ロケーター {len(candidate.brief.sections)} 個" in line["detail"]
    assert report["status"] == "PROJECT READY"


def _first_managed_clip(transport: FakeLiveTransport):
    return min(_arrangement_placements(transport), key=lambda c: (c.track_index, c.start_beats))


def test_a_renamed_arrangement_clip_fails(tmp_path: Path) -> None:
    runtime, candidate, transport = _expanded(tmp_path)
    clip = _first_managed_clip(transport)
    original = clip.name
    clip.name = "renamed by hand"

    report, line = _arrangement_check(runtime, candidate.candidate_id)

    assert line["status"] == "FAIL"
    assert f"name: 計画 {original} / Live renamed by hand" in line["detail"]
    assert report["status"] == "VERIFICATION FAILED"


def test_a_moved_arrangement_clip_fails(tmp_path: Path) -> None:
    runtime, candidate, transport = _expanded(tmp_path)
    clip = _first_managed_clip(transport)
    original = clip.start_beats
    clip.start_beats = original + 0.5

    _report, line = _arrangement_check(runtime, candidate.candidate_id)

    assert line["status"] == "FAIL"
    assert f"start_beats: 計画 {original} / Live {original + 0.5}" in line["detail"]


def test_a_shortened_arrangement_clip_fails(tmp_path: Path) -> None:
    runtime, candidate, transport = _expanded(tmp_path)
    clip = _first_managed_clip(transport)
    original = clip.length_beats
    clip.length_beats = original - 4.0

    _report, line = _arrangement_check(runtime, candidate.candidate_id)

    assert line["status"] == "FAIL"
    assert f"length_beats: 計画 {original} / Live {original - 4.0}" in line["detail"]


def test_a_deleted_arrangement_clip_fails(tmp_path: Path) -> None:
    runtime, candidate, transport = _expanded(tmp_path)
    clip = _first_managed_clip(transport)
    transport.live_set.arrangement_clips.remove(clip)

    _report, line = _arrangement_check(runtime, candidate.candidate_id)

    assert line["status"] == "FAIL"
    assert f"name: 計画 {clip.name} / Live なし" in line["detail"]


def test_a_renamed_locator_fails(tmp_path: Path) -> None:
    runtime, candidate, transport = _expanded(tmp_path)
    locator = transport.live_set.locators[-1]
    original = locator["name"]
    locator["name"] = "my marker"

    _report, line = _arrangement_check(runtime, candidate.candidate_id)

    assert line["status"] == "FAIL"
    assert f"name: 計画 {original} / Live my marker" in line["detail"]


def test_a_moved_locator_fails(tmp_path: Path) -> None:
    runtime, candidate, transport = _expanded(tmp_path)
    locator = transport.live_set.locators[-1]
    original = locator["beats"]
    locator["beats"] = original + 2.0

    _report, line = _arrangement_check(runtime, candidate.candidate_id)

    assert line["status"] == "FAIL"
    assert f"beats: 計画 {original} / Live {original + 2.0}" in line["detail"]


def test_a_deleted_locator_fails(tmp_path: Path) -> None:
    runtime, candidate, transport = _expanded(tmp_path)
    removed = transport.live_set.locators.pop()

    _report, line = _arrangement_check(runtime, candidate.candidate_id)

    assert line["status"] == "FAIL"
    assert f"name: 計画 {removed['name']} / Live なし" in line["detail"]


def test_unreported_locators_are_unconfirmed_not_passed(tmp_path: Path) -> None:
    runtime, candidate, transport = _expanded(tmp_path)
    transport.reports_locators = False

    report, line = _arrangement_check(runtime, candidate.candidate_id)

    assert line["status"] == "FAIL"
    assert "未確認: Liveがロケーター一覧を返しません" in line["detail"]
    assert report["ok"] is False


def test_an_unreadable_arrangement_list_is_unconfirmed_not_passed(tmp_path: Path) -> None:
    runtime, candidate, transport = _expanded(tmp_path)
    honest = transport.snapshot_payload

    def damaged(options=None):
        payload = honest(options)
        # Lenient parsing reads None as beat 0.0, which is where the first
        # clip really is; only the strict check stops it from matching.
        first = min(payload["arrangement_clips"], key=lambda clip: clip["start_beats"])
        assert first["start_beats"] == 0.0
        first["start_beats"] = None
        return payload

    transport.snapshot_payload = damaged

    report, line = _arrangement_check(runtime, candidate.candidate_id)

    assert line["status"] == "FAIL"
    assert "未確認: LiveがArrangementクリップ一覧を読める形で返しません" in line["detail"]
    assert report["ok"] is False


def test_a_duplicated_clip_cannot_stand_in_for_the_planned_one(tmp_path: Path) -> None:
    from kihachi_mcp.services.live_transport_fake import FakeArrangementClip

    runtime, candidate, transport = _expanded(tmp_path)
    clip = _first_managed_clip(transport)
    transport.live_set.arrangement_clips.append(
        FakeArrangementClip(clip.track_index, clip.name, clip.start_beats, clip.length_beats)
    )

    _report, line = _arrangement_check(runtime, candidate.candidate_id)

    assert line["status"] == "FAIL"
    assert "1 件は同じ名前・位置の候補が複数あり特定できません" in line["detail"]


def test_an_expansion_without_its_record_is_not_passed(tmp_path: Path) -> None:
    runtime, candidate, _transport = _expanded(tmp_path)
    runtime._arrangement_records.clear()

    report, line = _arrangement_check(runtime, candidate.candidate_id)

    assert line["status"] == "FAIL"
    assert "未確認: 展開時の計画と結果の記録がない" in line["detail"]
    assert report["ok"] is False


def test_the_arrangement_record_survives_a_restart(tmp_path: Path) -> None:
    saved = tmp_path / "candidates"
    _runtime_before, candidate, transport = _expanded(tmp_path, candidate_dir=saved)
    assert (saved / f"{candidate.candidate_id}.arrangement.json").is_file()

    restarted = _restarted(tmp_path, transport, saved)
    _report, line = _arrangement_check(restarted, candidate.candidate_id)

    assert line["status"] == "PASS", line


def test_expansion_reports_failure_when_the_independent_readback_cannot_confirm(
    tmp_path: Path,
) -> None:
    runtime, candidate, _transport = _runtime(tmp_path, reports_locators=False)
    runtime.apply(candidate.candidate_id, confirmed=True)

    result = runtime.expand_arrangement(candidate.candidate_id, confirmed=True)

    assert result["execution_status"] == "verified"
    assert result["ok"] is False
    assert result["receipt"]["status"] == "verification_failed"
    assert "did not report locators" in result["receipt"]["error"]
    assert {item["field_name"] for item in result["receipt"]["mismatches"]} == {
        "locators_reported"
    }


def test_a_partial_expansion_never_passes(tmp_path: Path) -> None:
    runtime, candidate, transport = _runtime(tmp_path)
    runtime.apply(candidate.candidate_id, confirmed=True)
    operations = runtime.arrangement_preview(candidate.candidate_id)["plan"]["operations"]
    transport.failures[operations[1]["operation_id"]] = ("operation_failed", "simulated")

    result = runtime.expand_arrangement(candidate.candidate_id, confirmed=True)
    report, line = _arrangement_check(runtime, candidate.candidate_id)

    assert result["ok"] is False and result["partial"] is True
    assert result["receipt"]["status"] == "partially_applied"
    assert line["status"] == "FAIL"
    assert line["detail"].startswith("展開は partially_applied で終わっています（成功 1 / 試行 2")
    assert report["ok"] is False


def test_a_record_that_cannot_be_saved_is_reported_and_never_passes_after_restart(
    tmp_path: Path, monkeypatch
) -> None:
    from kihachi_mcp.services import studio_runtime

    saved = tmp_path / "candidates"
    runtime, candidate, transport = _runtime(tmp_path, candidate_dir=saved)
    runtime.apply(candidate.candidate_id, confirmed=True)
    honest_write = studio_runtime._write_json_atomic

    def failing_write(path, payload):
        if path.name.endswith(".arrangement.json"):
            raise OSError("disk full")
        honest_write(path, payload)

    monkeypatch.setattr(studio_runtime, "_write_json_atomic", failing_write)

    result = runtime.expand_arrangement(candidate.candidate_id, confirmed=True)

    assert result["ok"] is False
    assert result["receipt"]["status"] == "verified"
    assert "展開記録を保存できませんでした" in result["error"]
    assert not (saved / f"{candidate.candidate_id}.arrangement.json").exists()
    restarted = _restarted(tmp_path, transport, saved)
    report, line = _arrangement_check(restarted, candidate.candidate_id)
    assert line["status"] == "FAIL"
    assert "未確認: 展開時の計画と結果の記録がない" in line["detail"]
    assert report["ok"] is False


def test_a_saved_record_without_its_marker_still_counts_as_expanded(tmp_path: Path) -> None:
    saved = tmp_path / "candidates"
    _before, candidate, transport = _expanded(tmp_path, candidate_dir=saved)
    (saved / f"{candidate.candidate_id}.arranged").unlink()

    restarted = _restarted(tmp_path, transport, saved)
    _report, line = _arrangement_check(restarted, candidate.candidate_id)
    again = restarted.expand_arrangement(candidate.candidate_id, confirmed=True)

    assert line["status"] == "PASS", line
    assert again["ok"] is False and "試行済み" in again["error"]


def test_studio_readback_ignores_note_counts_live_does_not_report(tmp_path: Path) -> None:
    runtime, candidate, transport = _expanded(tmp_path)
    for clip in transport.live_set.arrangement_clips:
        clip.note_count = 0

    _report, line = _arrangement_check(runtime, candidate.candidate_id)

    assert line["status"] == "PASS", line
