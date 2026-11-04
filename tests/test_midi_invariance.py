"""Invariance of deterministic MIDI generation.

These tests pin the design requirement that changing one musical parameter
must not disturb the others (DOC-003 determinism items, checklist MC-12/13/14).

What holds today, and is locked in here:

- same conditions + same seed reproduce the same notes;
- rhythm (start, duration) and note counts survive tempo, key and bar-count
  changes for every role;
- a key change transposes bass / chords / arp by the exact interval and
  leaves drum pitches untouched;
- growing the bar count keeps pitch and rhythm of the original bars.

What does NOT hold yet is marked ``xfail(strict=True)`` so a fix cannot slip
in unnoticed: the composer seeds its single random generator from the seed
plus the full constraints JSON (``midi_composer.py``), so any parameter
change reshuffles every random draw. Velocity humanization therefore changes
under any edit, and the melody is re-chosen rather than transposed when the
key changes. Separating the random streams per concern (pattern / velocity /
melodic choice) is the known fix; it changes existing outputs and needs a
deliberate decision, so it is not done silently here.
"""

import pytest

from kihachi_mcp.models.midi_composer import (
    ComposedMidiNote,
    MidiCompositionRequest,
    MusicalConstraints,
)
from kihachi_mcp.services.midi_composer import DeterministicMIDIComposer

ROLES = ["drums", "bass", "chords", "melody", "arp", "percussion"]
TRANSPOSED_ROLES = ["bass", "chords", "arp"]
DRUM_ROLES = ["drums", "percussion"]


def _compose(role: str, **overrides) -> tuple[ComposedMidiNote, ...]:
    params = {"key": "Dm", "tempo": 110, "bars": 4, "role": role}
    params.update(overrides)
    constraints = MusicalConstraints(**params)
    return DeterministicMIDIComposer(seed=11).compose(
        MidiCompositionRequest(constraints)
    )


def _rhythm(notes: tuple[ComposedMidiNote, ...]) -> list[tuple]:
    return [(n.start, n.duration) for n in notes]


def _pitches(notes: tuple[ComposedMidiNote, ...]) -> list[int]:
    return [n.pitch for n in notes]


def test_same_seed_reproduces_the_same_notes() -> None:
    for role in ROLES:
        assert _compose(role) == _compose(role)


def test_tempo_change_keeps_rhythm_counts_and_most_pitches() -> None:
    for role in ROLES:
        slower = _compose(role, tempo=90)
        faster = _compose(role, tempo=140)
        assert _rhythm(slower) == _rhythm(faster), role
        assert len(slower) == len(faster), role
        if role != "melody":
            assert _pitches(slower) == _pitches(faster), role


def test_key_change_keeps_rhythm_for_every_role() -> None:
    for role in ROLES:
        base = _compose(role, key="Dm")
        moved = _compose(role, key="Em")
        assert _rhythm(base) == _rhythm(moved), role
        assert len(base) == len(moved), role


def test_key_change_transposes_bass_chords_and_arp_exactly() -> None:
    for role in TRANSPOSED_ROLES:
        base = _compose(role, key="Dm")
        moved = _compose(role, key="Em")
        shifts = {n.pitch - b.pitch for b, n in zip(base, moved)}
        assert shifts == {2}, (role, shifts)


def test_key_change_leaves_drum_pitches_untouched() -> None:
    for role in DRUM_ROLES:
        assert _pitches(_compose(role, key="Dm")) == _pitches(
            _compose(role, key="Em")
        ), role


def test_more_bars_keep_the_original_bars_pitch_and_rhythm() -> None:
    for role in ROLES:
        short = _compose(role, bars=2)
        grown = [n for n in _compose(role, bars=4) if n.start < 8]
        assert _rhythm(short) == _rhythm(grown), role
        if role != "melody":
            assert _pitches(short) == _pitches(grown), role


@pytest.mark.xfail(
    strict=True,
    reason="velocity is drawn from an RNG seeded by the full constraints, "
    "so a tempo change reshuffles it (known determinism deviation)",
)
def test_tempo_change_keeps_velocity_too() -> None:
    assert _compose("drums", tempo=90) == _compose("drums", tempo=140)


@pytest.mark.xfail(
    strict=True,
    reason="the melody is re-chosen from the reseeded RNG instead of being "
    "transposed when the key changes (known determinism deviation)",
)
def test_key_change_transposes_the_melody_too() -> None:
    base = _compose("melody", key="Dm")
    moved = _compose("melody", key="Em")
    assert {n.pitch - b.pitch for b, n in zip(base, moved)} == {2}


@pytest.mark.xfail(
    strict=True,
    reason="velocity in the original bars changes when the bar count grows, "
    "because the RNG is reseeded by the full constraints (known deviation)",
)
def test_more_bars_keep_the_original_bars_notes_exactly() -> None:
    short = _compose("drums", bars=2)
    grown = [n for n in _compose("drums", bars=4) if n.start < 8]
    assert list(short) == grown


@pytest.mark.xfail(
    strict=True,
    reason="the melody of the original bars is re-chosen when the bar count "
    "grows, because the RNG is reseeded by the full constraints (known "
    "determinism deviation)",
)
def test_more_bars_keep_the_melodys_pitches_too() -> None:
    short = _compose("melody", bars=2)
    grown = [n for n in _compose("melody", bars=4) if n.start < 8]
    assert _pitches(short) == _pitches(grown)
