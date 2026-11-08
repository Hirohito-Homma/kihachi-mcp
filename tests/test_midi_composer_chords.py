"""Phase A2: chord progression and section context wired into composition.

Covers the MusicalConstraints additions (chords_by_bar / section_name /
section_energy), the SongSpec span helper that feeds them, the provider
prompt and chord-fit validation, and the deterministic generators reading
the same constraints. Everything here is progression-aware only when a
progression is supplied; the last test pins that nothing changes without
one beyond ordinary determinism.
"""

from __future__ import annotations

import pytest

from kihachi_mcp.models.midi_composer import (
    ComposedMidiNote,
    MidiCompositionRequest,
    MusicalConstraints,
)
from kihachi_mcp.models.songspec import SectionSpec, SongSpec
from kihachi_mcp.services.midi_composer import (
    DeterministicMIDIComposer,
    ProviderMIDIComposer,
    _parse_chord,
    chord_fit_ratio,
)

PROGRESSION = ("D#m", "D#m", "B", "B")


def _constraints(**overrides) -> MusicalConstraints:
    base = {"key": "D#m", "tempo": 110, "bars": 4, "role": "bass"}
    base.update(overrides)
    return MusicalConstraints(**base)


def test_chord_symbol_parsing():
    assert _parse_chord("D#m") == (3, (0, 3, 7))
    assert _parse_chord("B") == (11, (0, 4, 7))
    assert _parse_chord("F#m7") == (6, (0, 3, 7, 10))
    assert _parse_chord("Eb") == (3, (0, 4, 7))
    assert _parse_chord("Csus4") == (0, (0, 5, 7))
    assert _parse_chord("") is None
    assert _parse_chord("H") is None
    assert _parse_chord("D#xyz") is None


def test_constraints_round_trip_with_chords():
    c = _constraints(chords_by_bar=PROGRESSION, section_name="Groove", section_energy=0.7)
    data = c.to_dict()
    assert tuple(data["chords_by_bar"]) == PROGRESSION
    assert data["section_name"] == "Groove"
    assert data["section_energy"] == 0.7
    assert MusicalConstraints.from_dict(data) == c


def test_constraints_defaults_omit_chord_fields():
    data = _constraints().to_dict()
    assert "chords_by_bar" not in data
    assert "section_name" not in data
    assert "section_energy" not in data


def test_constraints_chord_length_must_match_bars():
    with pytest.raises(ValueError):
        _constraints(chords_by_bar=("D#m", "B"))


def test_songspec_chords_for_span():
    spec = SongSpec(
        genre="Dub Techno",
        tempo=110,
        key="D#m",
        length_minutes=2.0,
        bars=16,
        tracks=(),
        sections=(
            SectionSpec("Groove", 8, 0.7, ("D#m", "B")),
            SectionSpec("Drop", 8, 0.9, ("F#", "C#")),
        ),
    )
    assert spec.chords_for_span(0, 4) == ("D#m", "D#m", "D#m", "D#m")
    assert spec.chords_for_span(6, 4) == ("B", "B", "F#", "F#")
    with pytest.raises(ValueError):
        spec.chords_for_span(14, 4)


def test_chord_fit_ratio_cases():
    c = _constraints(chords_by_bar=PROGRESSION)
    on_chord = (
        ComposedMidiNote(39, 0.0, 0.25, 100),  # D# in a D#m bar
        ComposedMidiNote(35, 8.0, 0.25, 100),  # B in a B bar
    )
    assert chord_fit_ratio(on_chord, c) == 0.0
    off_chord = (ComposedMidiNote(41, 0.0, 0.25, 100),)  # F: in scale, off chord
    assert chord_fit_ratio(off_chord, c) == 1.0
    assert chord_fit_ratio(on_chord, _constraints()) is None
    drums = _constraints(role="drums", chords_by_bar=PROGRESSION)
    assert chord_fit_ratio(on_chord, drums) is None


class _StubProvider:
    """Paid-style provider: one attempt, captures the prompt it was given."""

    def __init__(self, notes: list[dict]) -> None:
        self._notes = notes
        self.prompt = ""

    def capabilities(self) -> dict:
        return {"structured_output": True, "paid_api": True}

    def generate_structured(self, prompt: str, schema: dict) -> dict:
        self.prompt = prompt
        return {"notes": self._notes}


def _note_dicts(*specs: tuple[int, float]) -> list[dict]:
    return [
        {"pitch": pitch, "start": start, "duration": 0.25, "velocity": 100, "channel": 0}
        for pitch, start in specs
    ]


def test_provider_rejects_line_ignoring_progression():
    # F natural (pc 5) is inside the D# minor scale but is a chord tone of
    # neither D#m nor B, so only the chord check can reject this line.
    provider = _StubProvider(_note_dicts(*[(41, float(i)) for i in range(8)]))
    composer = ProviderMIDIComposer(provider)
    request = MidiCompositionRequest(constraints=_constraints(chords_by_bar=PROGRESSION))
    with pytest.raises(ValueError, match="コード進行"):
        composer.compose(request)


def test_provider_prompt_carries_progression_and_section():
    provider = _StubProvider(_note_dicts((39, 0.0), (39, 4.0), (35, 8.0), (35, 12.0)))
    composer = ProviderMIDIComposer(provider)
    c = _constraints(chords_by_bar=PROGRESSION, section_name="Drop", section_energy=0.9)
    notes = composer.compose(MidiCompositionRequest(constraints=c))
    assert len(notes) == 4
    assert "Chord progression by bar" in provider.prompt
    assert "bars 1-2: D#m" in provider.prompt
    assert "bars 3-4: B" in provider.prompt
    assert "'Drop' section" in provider.prompt


def test_provider_prompt_has_no_progression_block_without_chords():
    provider = _StubProvider(_note_dicts((39, 0.0), (42, 1.0)))
    composer = ProviderMIDIComposer(provider)
    composer.compose(MidiCompositionRequest(constraints=_constraints()))
    assert "Chord progression by bar" not in provider.prompt


def test_deterministic_bass_follows_progression():
    composer = DeterministicMIDIComposer(seed=7)
    c = _constraints(chords_by_bar=PROGRESSION)
    notes = composer.compose(MidiCompositionRequest(constraints=c))
    downbeats = {note.start: note.pitch % 12 for note in notes if note.start % 4 == 0}
    assert downbeats[0.0] == 3  # D#m bars anchor on D#
    assert downbeats[8.0] == 11  # B bars anchor on B
    assert downbeats[12.0] == 11

    plain = composer.compose(MidiCompositionRequest(constraints=_constraints()))
    plain_downbeats = {note.start: note.pitch % 12 for note in plain if note.start % 4 == 0}
    assert set(plain_downbeats.values()) == {3}


def test_deterministic_chords_follow_progression():
    composer = DeterministicMIDIComposer(seed=7)
    c = _constraints(role="chords", chords_by_bar=PROGRESSION)
    notes = composer.compose(MidiCompositionRequest(constraints=c))
    # Judge the struck stabs (duration 0.2). The dub echoes (0.15) ring the
    # struck bar's chord across the barline by design, so they are not
    # evidence about the bar they land in.
    stabs = [note for note in notes if note.duration == 0.2]
    assert stabs
    for note in stabs:
        bar = int(note.start // 4)
        expected = {3, 6, 10} if PROGRESSION[bar] == "D#m" else {11, 3, 6}
        assert note.pitch % 12 in expected


def test_deterministic_arp_strong_beats_land_on_chord_tones():
    composer = DeterministicMIDIComposer(seed=7)
    c = _constraints(role="arp", chords_by_bar=PROGRESSION, grid="1/16")
    notes = composer.compose(MidiCompositionRequest(constraints=c))
    tones = {"D#m": {3, 6, 10}, "B": {11, 3, 6}}
    strong = [note for note in notes if note.start % 1.0 == 0]
    assert strong
    for note in strong:
        bar = int(note.start // 4)
        assert note.pitch % 12 in tones[PROGRESSION[bar]]


def test_deterministic_output_is_reproducible_without_progression():
    composer = DeterministicMIDIComposer(seed=7)
    request = MidiCompositionRequest(constraints=_constraints())
    assert composer.compose(request) == composer.compose(request)
