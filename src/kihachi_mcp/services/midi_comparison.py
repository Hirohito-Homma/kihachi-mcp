"""A/B comparison utilities for MIDI composition quality and groove verification."""

from __future__ import annotations

import math
from dataclasses import replace
from typing import Any

from kihachi_mcp.models.midi_composer import (
    ComposedMidiNote,
    MidiCompositionRequest,
    MusicalConstraints,
)
from kihachi_mcp.services.midi_composer import DeterministicMIDIComposer


def compare_swing_variants(
    constraints: MusicalConstraints, seed: int = 0
) -> dict[str, Any]:
    """A. Swing-only comparison: Identical notes & velocity, strictly timing differences.

    Variant A: straight 50% swing
    Variant B: 54% swing
    """
    cA = replace(constraints, swing=0.50)
    cB = replace(constraints, swing=0.54)

    compA = DeterministicMIDIComposer(seed=seed)
    compB = DeterministicMIDIComposer(seed=seed)

    notesA = compA.compose(MidiCompositionRequest(constraints=cA, operation="generate"))
    notesB = compB.compose(MidiCompositionRequest(constraints=cB, operation="generate"))

    if len(notesA) != len(notesB):
        raise ValueError(
            f"Swing comparison note count mismatch: A={len(notesA)}, B={len(notesB)}"
        )

    timing_deltas: list[float] = []
    downbeat_deltas: list[float] = []
    offbeat_deltas: list[float] = []

    for nA, nB in zip(notesA, notesB):
        if nA.pitch != nB.pitch:
            raise ValueError(f"Pitch mismatch between swing variants: {nA.pitch} != {nB.pitch}")
        if nA.velocity != nB.velocity:
            raise ValueError(f"Velocity mismatch between swing variants: {nA.velocity} != {nB.velocity}")

        delta = round(nB.start - nA.start, 6)
        timing_deltas.append(delta)

        # Downbeat vs Offbeat
        beat_offset = nA.start % 1.0
        if abs(beat_offset) < 1e-6:
            downbeat_deltas.append(delta)
        else:
            offbeat_deltas.append(delta)

    # Downbeats must stay locked (delta == 0)
    max_downbeat_drift = max((abs(d) for d in downbeat_deltas), default=0.0)
    # Offbeats should have positive shift for 54% swing
    avg_offbeat_shift = (
        sum(offbeat_deltas) / len(offbeat_deltas) if offbeat_deltas else 0.0
    )

    return {
        "ok": True,
        "note_count": len(notesA),
        "pitches_identical": True,
        "velocities_identical": True,
        "max_downbeat_drift": max_downbeat_drift,
        "downbeat_lock_passed": max_downbeat_drift < 1e-6,
        "avg_offbeat_shift": round(avg_offbeat_shift, 6),
        "offbeat_notes_count": len(offbeat_deltas),
        "notes_variant_a": [n.to_dict() for n in notesA],
        "notes_variant_b": [n.to_dict() for n in notesB],
    }


def _legacy_generate_notes(c: MusicalConstraints, root: int) -> list[ComposedMidiNote]:
    """Simple straight mechanical pattern used as legacy baseline."""
    notes: list[ComposedMidiNote] = []
    for bar in range(c.bars):
        base = bar * 4.0
        if c.role == "bass":
            # Simple monotone quarter/eighth root bass without slap/ghosts
            for beat in range(4):
                notes.append(ComposedMidiNote(root, base + beat, 0.4, 90, 0))
        elif c.role == "chords":
            # Simple triads on downbeats
            for beat in (0.0, 2.0):
                for interval in (0, 3, 7):
                    notes.append(ComposedMidiNote(root + interval, base + beat, 0.5, 85, 0))
        elif c.role in {"drums", "Kick"}:
            for beat in range(4):
                notes.append(ComposedMidiNote(36, base + beat, 0.2, 100, 9))
    return notes


def compare_composer_generations(
    constraints: MusicalConstraints, seed: int = 0
) -> dict[str, Any]:
    """B. Overall Composer comparison: Legacy baseline vs Enhanced P2 generator.

    Compares: note count, velocity dynamics, pitch range, and phrase variations.
    """
    enhanced_composer = DeterministicMIDIComposer(seed=seed)
    enhanced_notes = enhanced_composer.compose(
        MidiCompositionRequest(constraints=constraints, operation="generate")
    )

    from kihachi_mcp.services.session_pattern_builder import root_pitch

    engine_offset = {"bass": 0, "chords": 2, "drums": 0}.get(constraints.role, 0)
    root = root_pitch(constraints.key, octave_offset=engine_offset)
    legacy_notes = _legacy_generate_notes(constraints, root)

    # Calculate statistics
    enhanced_vels = [n.velocity for n in enhanced_notes]
    legacy_vels = [n.velocity for n in legacy_notes]

    def _std(vals: list[int]) -> float:
        if not vals:
            return 0.0
        mean = sum(vals) / len(vals)
        return math.sqrt(sum((v - mean) ** 2 for v in vals) / len(vals))

    enhanced_pitches = [n.pitch for n in enhanced_notes]
    legacy_pitches = [n.pitch for n in legacy_notes]

    enhanced_ghosts = [n for n in enhanced_notes if n.velocity <= 55]

    return {
        "ok": True,
        "legacy_note_count": len(legacy_notes),
        "enhanced_note_count": len(enhanced_notes),
        "legacy_velocity_std": round(_std(legacy_vels), 2),
        "enhanced_velocity_std": round(_std(enhanced_vels), 2),
        "legacy_pitch_range": (min(legacy_pitches, default=0), max(legacy_pitches, default=0)),
        "enhanced_pitch_range": (min(enhanced_pitches, default=0), max(enhanced_pitches, default=0)),
        "ghost_note_count": len(enhanced_ghosts),
        "dynamics_enhanced": _std(enhanced_vels) > _std(legacy_vels),
    }
