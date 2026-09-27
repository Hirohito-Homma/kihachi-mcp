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


def _runtime(tmp_path: Path, live: FakeLiveSet | None = None, **transport_kwargs):
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
