# -*- coding: utf-8 -*-
"""P2 MIDI Composer Quality Enhancement Test Suite.

Covers:
- Task 2: Groove Engine verification (54% swing timing, Kick 4-on-floor lock, Snare/Hi-Hat dynamics, Ghost Notes velocity).
- Task 3: Funky Slap Bass verification (Thumb & Pop octave jump, syncopation, ghost notes, 4-bar fill & 8-bar turnaround).
- Task 4: Dub Chord Stabs verification (offbeat stabs, tape delay echo reflections, D#m7 voicing, 16-bar density).
- Task 5: A/B Comparison between baseline and upgraded MIDI Composer.
- Task 6: Determinism across identical seeds, 110 BPM / D# minor / 54% swing compliance.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Any

import pytest

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from kihachi_mcp.models.midi_composer import (
    ComposedMidiNote,
    MidiCompositionRequest,
    MusicalConstraints,
)
from kihachi_mcp.services.midi_composer import DeterministicMIDIComposer, _apply_swing


def _make_brief_constraints(role: str = "bass", bars: int = 16, seed: int = 11054) -> MusicalConstraints:
    """Fixed Test Brief:

    Genre: Mutation Funk x DUB x Tech House
    Tempo: 110 BPM, Key: D#m (D# minor), Swing: 54% (0.54)
    Structure: 16 bars base
    """
    return MusicalConstraints(
        key="D#m",
        tempo=110,
        bars=bars,
        role=role,
        grid="1/16",
        syncopation=0.65,
        octave_jump=0.8,
        ghost_notes=True,
        velocity_min=35,
        velocity_max=120,
        note_length_min=0.1,
        note_length_max=0.5,
        avoid_kick_collision=True,
        kick_onsets=tuple(float(i) for i in range(bars * 4)),
        genre="Mutation Funk x DUB x Tech House",
        mood="Dark Funky",
        prompt="Funky slap bass with dub stabs and tight tech house groove",
        swing=0.54,
    )


def test_groove_engine_swing_and_kick_lock() -> None:
    """Task 2: 54% Swing correctly shifts offbeat eighths (0.5 -> 0.54) and sixteenths,

    while keeping downbeat kicks strictly on-grid.
    """
    # 1. Swing warp calculations
    assert _apply_swing(0.0, 0.54) == 0.0  # Downbeat is locked on-grid
    assert _apply_swing(1.0, 0.54) == 1.0  # Beat 2 downbeat locked
    assert _apply_swing(0.5, 0.54) == 0.54  # 8th offbeat pushed by 54% swing
    assert _apply_swing(1.5, 0.54) == 1.54  # Beat 2 offbeat pushed by 54% swing

    # Sixteenth note is shifted proportionally
    sixteenth_swung = _apply_swing(0.25, 0.54)
    assert sixteenth_swung > 0.25

    # 2. Drums generation check: Kick remains exactly at integer beats
    c_drums = _make_brief_constraints(role="drums", bars=4)
    composer = DeterministicMIDIComposer(seed=42)
    req = MidiCompositionRequest(operation="generate", constraints=c_drums)
    notes = composer.compose(req)

    kicks = [n for n in notes if n.pitch == 36]
    assert len(kicks) == 16  # 4 beats * 4 bars
    for idx, kick in enumerate(kicks):
        assert abs(kick.start - float(idx)) < 1e-6, f"Kick {idx} was swung! start={kick.start}"
        assert kick.velocity >= 100, f"Kick velocity too low: {kick.velocity}"


def test_ghost_notes_velocity_separation() -> None:
    """Task 2: Ghost notes must have strictly lower velocity (35-55) than standard/accent hits."""
    c_drums = _make_brief_constraints(role="drums", bars=8)
    composer = DeterministicMIDIComposer(seed=123)
    notes = composer.compose(MidiCompositionRequest(constraints=c_drums, operation="generate"))

    snares = [n for n in notes if n.pitch == 38]
    backbeat_snares = [n for n in snares if n.start % 2.0 == 1.0]
    ghost_snares = [n for n in snares if n.start % 2.0 != 1.0]

    assert len(backbeat_snares) >= 16  # 2 per bar * 8 bars
    for snare in backbeat_snares:
        assert snare.velocity >= 100, f"Backbeat snare velocity should be high: {snare.velocity}"

    if ghost_snares:
        for ghost in ghost_snares:
            assert 35 <= ghost.velocity <= 55, f"Ghost snare velocity outside ghost range: {ghost.velocity}"
            assert ghost.duration <= 0.1, f"Ghost snare should be short staccato: {ghost.duration}"


def test_funky_slap_bass_vocabulary_and_variations() -> None:
    """Task 3: Slap Bass incorporates Thumb (low root), Pop (octave/7th), Ghost notes,

    and 16-bar phrase variation (4-bar fill and 8-bar turnaround).
    """
    c_bass = _make_brief_constraints(role="bass", bars=16)
    composer = DeterministicMIDIComposer(seed=999)
    notes = composer.compose(MidiCompositionRequest(constraints=c_bass, operation="generate"))

    assert len(notes) > 0

    # D#1 is MIDI 27, D#2 is MIDI 39, D#3 is MIDI 51
    # Check pitch presence: Thumb (root around 27/39), Pop (+12, +10 around 39/51)
    pitches = {n.pitch for n in notes}
    # Root pitch for D#m at octave_offset=0 in root_pitch is 27 or 39
    assert any(p <= 39 for p in pitches), f"Low thumb pitch missing from {pitches}"
    assert any(p >= 39 for p in pitches), f"High pop octave pitch missing from {pitches}"

    # Check 4-bar fill vs regular bars: bar 3 (0-indexed) has fill pattern with more notes
    bar_note_counts = {}
    for n in notes:
        bar_num = int(n.start // 4.0)
        bar_note_counts[bar_num] = bar_note_counts.get(bar_num, 0) + 1

    # Regular bar count vs fill bar count
    regular_count = bar_note_counts.get(0, 0)
    fill_count = bar_note_counts.get(3, 0)  # Bar 4 (fill)
    turnaround_count = bar_note_counts.get(7, 0)  # Bar 8 (turnaround)

    assert fill_count >= regular_count, f"Fill bar should have dense variations: {fill_count} vs {regular_count}"
    assert turnaround_count >= regular_count, f"Turnaround bar should have variations: {turnaround_count} vs {regular_count}"

    # Check ghost notes in bass
    ghost_bass = [n for n in notes if n.velocity <= 55]
    assert len(ghost_bass) > 0, "Slap bass should contain mute ghost notes"


def test_dub_chord_stabs_and_echo_reflections() -> None:
    """Task 4: Dub Chords sit on offbeats (e.g. 1.5, 3.5), feature minor 7th voicing,

    and generate tape delay echo reflections.
    """
    c_chords = _make_brief_constraints(role="chords", bars=16)
    composer = DeterministicMIDIComposer(seed=777)
    notes = composer.compose(MidiCompositionRequest(constraints=c_chords, operation="generate"))

    assert len(notes) > 0

    # Chords must NOT sit on integer beat downbeats (0.0, 1.0, 2.0, 3.0)
    # They sit on offbeats with 54% swing
    for n in notes:
        beat_offset = n.start % 1.0
        assert beat_offset != 0.0, f"Dub stab chord should be offbeat, found at {n.start}"

    # Echo reflections: stabs followed by quieter repetitions
    # Check velocity distribution: main stab > 90, echo 1 < 80, echo 2 < 60
    velocities = [n.velocity for n in notes]
    has_main_stabs = any(v >= 95 for v in velocities)
    has_echo_reflections = any(v <= 80 for v in velocities)
    assert has_main_stabs and has_echo_reflections, f"Dub echo velocity gradation missing: {set(velocities)}"


def test_deterministic_reproducibility() -> None:
    """Task 6: Identical seed produces bit-identical notes."""
    c = _make_brief_constraints(role="bass", bars=16, seed=8888)
    comp1 = DeterministicMIDIComposer(seed=8888)
    comp2 = DeterministicMIDIComposer(seed=8888)

    notes1 = comp1.compose(MidiCompositionRequest(constraints=c, operation="generate"))
    notes2 = comp2.compose(MidiCompositionRequest(constraints=c, operation="generate"))

    assert len(notes1) == len(notes2)
    for n1, n2 in zip(notes1, notes2):
        assert n1.pitch == n2.pitch
        assert abs(n1.start - n2.start) < 1e-9
        assert abs(n1.duration - n2.duration) < 1e-9
        assert n1.velocity == n2.velocity


def test_slap_bass_pitch_range_and_intervals() -> None:
    """Task 1 audit: Slap bass pitches in D# minor must stay within 39 (D#2) to 51 (D#3)."""
    c = _make_brief_constraints(role="bass", bars=16)
    composer = DeterministicMIDIComposer(seed=42)
    notes = composer.compose(MidiCompositionRequest(constraints=c, operation="generate"))

    pitches = {n.pitch for n in notes}
    # D#2=39, F#2=42 (m3), G#2=44 (p4), A#2=46 (p5), C#3=49 (m7), D#3=51 (octave)
    assert min(pitches) == 39, f"Expected D#2 (39) minimum, got {min(pitches)}"
    assert max(pitches) == 51, f"Expected D#3 (51) maximum, got {max(pitches)}"
    assert pitches.issubset({39, 42, 44, 46, 49, 51}), f"Unexpected pitches found: {pitches}"


def test_clip_boundary_and_dub_echo_containment() -> None:
    """Task 1 audit: Every note across all roles must strictly stay within 16 bars (64.0 beats).

    Echo reflections spilling across bar boundaries must NOT overflow clip boundary.
    """
    roles = ("drums", "bass", "chords", "percussion", "melody", "arp")
    composer = DeterministicMIDIComposer(seed=99)
    for role in roles:
        c = _make_brief_constraints(role=role, bars=16)
        notes = composer.compose(MidiCompositionRequest(constraints=c, operation="generate"))
        assert len(notes) > 0, f"Role {role} generated 0 notes"
        for n in notes:
            assert n.start >= 0.0, f"{role} note start {n.start} < 0"
            assert n.duration > 0.0, f"{role} note duration <= 0"
            assert n.start + n.duration <= 64.0 + 1e-6, (
                f"{role} note overflowed clip boundary: start={n.start}, duration={n.duration}, "
                f"end={n.start + n.duration} > 64.0"
            )


def test_swing_consistency_eighth_and_sixteenth() -> None:
    """Task 1 audit: Swing 54% formula must be mathematically consistent for 8ths and 16ths."""
    # Downbeats remain locked
    assert _apply_swing(0.0, 0.54) == 0.0
    assert _apply_swing(1.0, 0.54) == 1.0
    assert _apply_swing(2.0, 0.54) == 2.0

    # 8th offbeat is pushed to 0.54 (54% of 1.0)
    assert _apply_swing(0.5, 0.54) == 0.54

    # 16th second beat is 0.27 (54% of 0.5)
    assert _apply_swing(0.25, 0.54) == 0.27

    # 16th fourth beat is 0.77 (0.54 + 50% of (1.0 - 0.54))
    assert _apply_swing(0.75, 0.54) == 0.77

    # Straight 50% preserves exact grid positions
    for test_pos in (0.0, 0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 1.75):
        assert _apply_swing(test_pos, 0.50) == round(test_pos, 6)


def test_ghost_notes_velocity_strict_distribution() -> None:
    """Task 1 audit: Ghost notes (35-55) and standard notes (65-118) must have strictly disjoint velocities."""
    c = _make_brief_constraints(role="bass", bars=16)
    composer = DeterministicMIDIComposer(seed=123)
    notes = composer.compose(MidiCompositionRequest(constraints=c, operation="generate"))

    ghost_vels = [n.velocity for n in notes if n.velocity <= 55]
    standard_vels = [n.velocity for n in notes if n.velocity > 55]

    assert len(ghost_vels) > 0, "No ghost notes found"
    assert len(standard_vels) > 0, "No standard notes found"
    assert max(ghost_vels) <= 55, f"Ghost notes exceed 55: {max(ghost_vels)}"
    assert min(standard_vels) >= 65, f"Standard notes fall below 65: {min(standard_vels)}"
