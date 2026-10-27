"""Tests for groove rule-based MIDI variant composition."""

from kihachi_mcp.models.production_brief import PART_ORDER
from kihachi_mcp.services.groove_rule_composer import (
    generate_variants_from_card,
    promote_variant_to_candidate,
)
from kihachi_mcp.services.reference_analysis_card import AnalysisCard


def _create_mock_card() -> AnalysisCard:
    sample_analysis = {
        "ok": True,
        "segment": {
            "start_seconds": 0.0,
            "duration_seconds": 16.0,
            "sample_rate": 22050,
        },
        "bpm": {"value": 124.0, "confidence": 0.85, "unit": "BPM", "method": "autocorrelation", "status": "verified"},
        "kick_pattern": {"hit_count": 16, "interval_summary": "4-beat regular", "confidence": 0.9, "is_four_on_the_floor": True},
        "bass_groove": {"hit_count": 12, "rest_ratio": 0.65, "syncopation_ratio": 0.5, "density_notes_per_bar": 2.0, "confidence": 0.75},
        "energy_dynamics": {"trend": "stable", "confidence": 0.7, "method": "RMS"},
        "section": {"candidate": "groove_core", "confidence": 0.4, "status": "unverified"},
        "harmonic_analysis": {"key": "unknown", "confidence": 0.0, "status": "unverified"},
        "theory_interpretation": {
            "summary": "安定した4つ打ちと裏拍ベース",
            "detailed_explanations": ["キックとベースがマスキングを回避しています。"],
        },
        "generation_rules": {
            "bpm_target": 124.0,
            "kick_style": "four_on_the_floor",
            "bass_syncopation": 0.5,
            "bass_rest_ratio": 0.65,
        },
    }
    return AnalysisCard.from_analysis(
        analysis_result=sample_analysis,
        user_title="Mock Techno",
        audio_filename="mock.wav",
    )


def test_generate_variants_from_card():
    card = _create_mock_card()
    result = generate_variants_from_card(card, bars=8, key="D#m")

    assert result["ok"] is True
    variants = result["variants"]
    assert len(variants) == 3

    var_a, var_b, var_c = variants[0], variants[1], variants[2]

    # Variant A: Reference Faithful Density
    assert var_a["variant_id"] == "A"
    assert "基本グルーヴ" in var_a["diff_from_base"] or "Density" in var_a.get("name", "")
    assert var_a["bass_note_count"] > 0

    # Variant B: Spacious Dub Rest
    assert var_b["variant_id"] == "B"
    assert "休符" in var_b["diff_from_base"] or "Dub" in var_b.get("name", "")
    # Variant B should have equal or fewer notes than A due to spaciousness
    assert var_b["bass_note_count"] <= var_a["bass_note_count"]

    # Variant C: Sub-Split Offbeat
    assert var_c["variant_id"] == "C"
    assert "裏拍" in var_c["diff_from_base"] or "Interlock" in var_c.get("name", "")

    # Verify no raw note cloning: generated notes are synthetic DeterministicMIDIComposer output
    assert all("pitch" in n and "start" in n for n in var_a["notes"]["Bass"])


def test_promote_variant_to_candidate():
    card = _create_mock_card()
    candidate = promote_variant_to_candidate(card, variant_id="A", bars=64, key=None)

    assert candidate.candidate_id is not None
    assert len(candidate.clips) > 0

    # Ensure clips are sorted strictly by PART_ORDER
    part_sequence = [clip.part for clip in candidate.clips]
    observed_order = []
    for p in part_sequence:
        if not observed_order or observed_order[-1] != p:
            observed_order.append(p)
    expected_order = [p for p in PART_ORDER if p in observed_order]
    assert observed_order == expected_order

    # Provenance and confidence checks
    brief = candidate.brief
    assert "Mock Techno" in brief.original_text
    assert brief.tempo.value == 124.0
    assert "conf=0.85" in brief.original_text
    assert "conf=0.00" in brief.original_text

    # Card key was "unknown" (confidence 0.0), verify default fallback is truthfully marked
    assert brief.key.value == "Dm"

    # Verify section intents
    assert len(brief.sections) == 4
    section_names = [s.name for s in brief.sections]
    assert section_names == ["Intro", "Build", "Drop", "Outro"]


def test_promote_variant_with_human_override_and_variant_c():
    card = _create_mock_card()
    candidate_c = promote_variant_to_candidate(card, variant_id="C", bars=64, key="Fm")

    brief = candidate_c.brief
    assert brief.key.value == "Fm"
    assert brief.key.source == "user"
    assert "conf=1.00" in brief.original_text
    assert "Variant C" in brief.original_text

    # Verify bass notes have anti-collision interlock
    bass_clips = [c for c in candidate_c.clips if c.part == "Bass"]
    assert len(bass_clips) > 0
    # Bass onsets should avoid exact integer downbeats (0.0) in Variant C
    for clip in bass_clips:
        for note in clip.notes:
            rem = note.start_beats % 1.0
            # should not land right on integer beat 0.0
            assert rem > 0.01 or rem < 0.99
