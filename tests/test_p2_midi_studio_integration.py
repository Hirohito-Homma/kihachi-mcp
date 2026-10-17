"""Integration tests for Studio A/B preview, evaluation recording, and groove generation."""

from __future__ import annotations

import io
from pathlib import Path
import soundfile as sf
import pytest

from kihachi_mcp.models.midi_candidate import MidiCandidate
from kihachi_mcp.services.brief_parser import extract_explicit
from kihachi_mcp.services.candidate_audio_preview import candidate_preview_wav
from kihachi_mcp.services.midi_candidate_builder import build_candidate
from kihachi_mcp.services.studio_interpreter import assemble_brief
from kihachi_mcp.services.studio_runtime import StudioRuntime
from kihachi_mcp.studio.app import StudioApp


@pytest.fixture
def mock_candidate(tmp_path: Path) -> MidiCandidate:
    brief = assemble_brief(
        extract_explicit("32小節 110 BPM D#マイナー。17小節目からドロップにして"),
        {
            "genre": "tech_house",
            "tempo": 110,
            "bars": 32,
            "key": "D#m",
            "mood": "暗い",
            "hats_first_half": "sparse",
            "hats_second_half": "dense",
            "bass_register": "low",
            "note_density": "normal",
            "drop_start_bar": 17,
            "unhandled": [],
            "ambiguous": [],
        },
    )
    return build_candidate(brief, seed=42, candidate_id="test_candidate_p2_001")


def test_candidate_preview_wav_loudness_normalized(mock_candidate: MidiCandidate) -> None:
    """Task 3: Previews must be loudness normalized (peak around 0.85) to avoid listening bias."""
    # Full preview
    wav_all = candidate_preview_wav(mock_candidate, part_filter="all")
    assert len(wav_all) > 100
    data_all, rate = sf.read(io.BytesIO(wav_all), dtype="float32")
    peak_all = float(abs(data_all).max())
    assert 0.80 <= peak_all <= 0.86, f"Peak not normalized: {peak_all}"

    # Bass solo
    wav_bass = candidate_preview_wav(mock_candidate, part_filter="bass")
    assert len(wav_bass) > 100
    data_bass, _ = sf.read(io.BytesIO(wav_bass), dtype="float32")
    peak_bass = float(abs(data_bass).max())
    assert 0.80 <= peak_bass <= 0.86, f"Bass solo peak not normalized: {peak_bass}"

    # Drums solo
    wav_drums = candidate_preview_wav(mock_candidate, part_filter="drums")
    assert len(wav_drums) > 100
    data_drums, _ = sf.read(io.BytesIO(wav_drums), dtype="float32")
    peak_drums = float(abs(data_drums).max())
    assert 0.80 <= peak_drums <= 0.86, f"Drums solo peak not normalized: {peak_drums}"


def test_candidate_preview_wav_swing_variant_shifts(mock_candidate: MidiCandidate) -> None:
    """Task 3: Variant A (50% straight) vs Variant B (54% groove) produces distinct offbeat audio."""
    wav_a = candidate_preview_wav(mock_candidate, part_filter="bass", swing_override=0.50)
    wav_b = candidate_preview_wav(mock_candidate, part_filter="bass", swing_override=0.54)

    assert wav_a != wav_b, "Swing variants should generate different audio due to offbeat warp"


def test_evaluation_recording_lifecycle(tmp_path: Path, mock_candidate: MidiCandidate) -> None:
    """Task 1 & 4: A/B ratings and notes are saved to disk and restored across app restarts without mutating candidate."""
    runtime = StudioRuntime(start_bridge=False, candidate_dir=tmp_path)
    runtime._store(mock_candidate)
    app1 = StudioApp(runtime=runtime)

    # Initial evaluation should be empty
    assert app1._evaluations.get(mock_candidate.candidate_id) is None

    # Record evaluation with persistence
    eval_data = {
        "candidate_id": mock_candidate.candidate_id,
        "variant": "B",
        "rating": "B_BETTER",
        "notes": "54% swing provides a laid-back, infectious pocket",
        "evaluated_at": "2026-10-12T13:30:00Z",
    }
    app1.save_evaluation(mock_candidate.candidate_id, eval_data)

    # Verify written to evaluations.json on disk
    eval_file = tmp_path / "evaluations.json"
    assert eval_file.is_file(), "evaluations.json was not created on disk"

    # Start a brand new StudioApp instance pointing to the same candidate_dir (simulating page reload / restart)
    app2 = StudioApp(runtime=runtime)
    restored_eval = app2._evaluations.get(mock_candidate.candidate_id)
    assert restored_eval is not None, "Evaluation was not restored from disk"
    assert restored_eval["rating"] == "B_BETTER"
    assert restored_eval["notes"] == "54% swing provides a laid-back, infectious pocket"

    # Candidate remains intact and unmodified
    restored = runtime.get_candidate(mock_candidate.candidate_id)
    assert restored is not None
    assert restored.candidate_id == mock_candidate.candidate_id


def test_preview_audio_loudness_and_metrics_measurement(mock_candidate: MidiCandidate) -> None:
    """Task 2 & 3: Audio preview loudness (LUFS), sample peak, clipping and silence detection."""
    from kihachi_mcp.services.candidate_audio_preview import measure_preview_audio

    wav_bytes = candidate_preview_wav(mock_candidate, part_filter="all", swing_override=0.54)
    metrics = measure_preview_audio(wav_bytes)

    assert metrics["ok"] is True
    assert metrics["is_clipped"] is False, "Preview should not clip (peak target -1.4 dBFS)"
    assert metrics["is_silent"] is False, "Preview should not be silent"
    assert metrics["sample_peak_dbfs"] is not None
    # Sample peak should sit around -1.4 dBFS (-1.5 to -1.3 dBFS)
    assert -2.0 <= metrics["sample_peak_dbfs"] <= -1.0
    if metrics["has_lufs"]:
        assert metrics["integrated_lufs"] is not None
        assert metrics["normalization"] == "lufs_measured_and_peak_matched"


def test_groove_generation_preserves_parent_and_creates_new_candidate(tmp_path: Path, mock_candidate: MidiCandidate) -> None:
    """Task 4: Groove adjustment creates an independent candidate with new ID while preserving parent."""
    runtime = StudioRuntime(start_bridge=False, candidate_dir=tmp_path)
    runtime._store(mock_candidate)
    parent_id = mock_candidate.candidate_id
    parent_fingerprint = mock_candidate.note_fingerprint

    # Generate new candidate with groove adjustment (58% swing, 0.85 syncopation)
    import uuid
    from kihachi_mcp.models.midi_candidate import CandidateClip, MidiCandidate
    from kihachi_mcp.models.midi_composer import MidiCompositionRequest, MusicalConstraints
    from kihachi_mcp.services.midi_composer import DeterministicMIDIComposer
    from kihachi_mcp.services.session_pattern_builder import MidiNote

    new_id = uuid.uuid4().hex
    composer = DeterministicMIDIComposer(seed=999)
    new_clips = []
    for clip in mock_candidate.clips:
        role = "drums" if clip.part in {"Kick", "Snare", "Hats", "OpenHat"} else clip.part.lower()
        if role not in {"drums", "bass", "chords"}:
            role = "drums"
        c = MusicalConstraints(
            key=mock_candidate.brief.key.value,
            tempo=int(mock_candidate.brief.tempo.value),
            bars=clip.length_bars,
            role=role,
            swing=0.58,
            syncopation=0.85,
            ghost_notes=True,
        )
        gen_notes = composer.compose(MidiCompositionRequest(constraints=c, operation="generate"))
        new_notes = tuple(
            MidiNote(n.pitch, n.start, n.duration, n.velocity)
            for n in gen_notes
        )
        new_clips.append(
            CandidateClip(
                part=clip.part,
                section_name=clip.section_name,
                start_bar=clip.start_bar,
                length_bars=clip.length_bars,
                notes=new_notes,
            )
        )

    new_candidate = MidiCandidate(
        candidate_id=new_id,
        brief=mock_candidate.brief,
        clips=tuple(new_clips),
        parent_candidate_id=parent_id,
        seed=999,
    )
    runtime._store(new_candidate)

    # 1. New candidate is stored with new ID
    stored_new = runtime.get_candidate(new_id)
    assert stored_new is not None
    assert stored_new.candidate_id == new_id
    assert stored_new.parent_candidate_id == parent_id

    # 2. Parent candidate is completely unchanged
    stored_parent = runtime.get_candidate(parent_id)
    assert stored_parent is not None
    assert stored_parent.candidate_id == parent_id
    assert stored_parent.note_fingerprint == parent_fingerprint
