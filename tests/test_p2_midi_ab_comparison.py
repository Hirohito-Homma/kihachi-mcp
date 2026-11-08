"""Tests for P2 A/B MIDI comparison: swing-only and overall composer comparison."""

from __future__ import annotations

from kihachi_mcp.models.midi_composer import MusicalConstraints
from kihachi_mcp.services.midi_comparison import (
    compare_composer_generations,
    compare_swing_variants,
)


def _make_brief(role: str) -> MusicalConstraints:
    return MusicalConstraints(
        key="D#m",
        tempo=110,
        bars=16,
        role=role,
        grid="1/16",
        syncopation=0.7,
        ghost_notes=True,
        avoid_kick_collision=True,
        genre="Mutation Funk x DUB x Tech House",
        mood="Hypnotic / Funky",
        swing=0.54,
    )


def test_ab_comparison_variant_a_swing_only() -> None:
    """Task 2 (A): Swing-only comparison between 50% straight and 54% swing.

    Identical notes, identical velocities, downbeats strictly locked,
    only offbeats shifted.
    """
    for role in ("bass", "drums", "chords"):
        c = _make_brief(role)
        result = compare_swing_variants(c, seed=42)

        assert result["ok"] is True
        assert result["pitches_identical"] is True
        assert result["velocities_identical"] is True
        assert result["downbeat_lock_passed"] is True
        assert result["max_downbeat_drift"] < 1e-6
        assert result["offbeat_notes_count"] > 0
        assert result["avg_offbeat_shift"] > 0.0, f"{role} should have positive offbeat shift"


def test_ab_comparison_variant_b_composer_overall() -> None:
    """Task 2 (B): Overall Composer comparison between legacy and enhanced P2 composer.

    Tests note density, velocity dynamics standard deviation, pitch vocabulary, and ghost notes.
    """
    c_bass = _make_brief("bass")
    result_bass = compare_composer_generations(c_bass, seed=42)

    assert result_bass["ok"] is True
    assert result_bass["enhanced_note_count"] > result_bass["legacy_note_count"]
    # Enhanced slap bass has high dynamic variation (ghosts 35-55, accents 105-118)
    assert result_bass["dynamics_enhanced"] is True
    assert result_bass["enhanced_velocity_std"] > 15.0
    assert result_bass["ghost_note_count"] > 0
    # Pitch range should span slap intervals (39 to 51)
    min_p, max_p = result_bass["enhanced_pitch_range"]
    assert min_p == 39 and max_p == 51

    c_chords = _make_brief("chords")
    result_chords = compare_composer_generations(c_chords, seed=42)
    assert result_chords["ok"] is True
    assert result_chords["enhanced_note_count"] > 0
    assert result_chords["dynamics_enhanced"] is True
