"""Deterministic MIDI note patterns for Session View clips.

The same ProjectPlan always produces the same notes, so a plan can be reviewed,
approved, and later verified by comparing note counts read back from Live.

Positions are expressed in Live beat units relative to the clip start, where one
beat is a quarter note. Bars are converted using the Set's meter, never assumed
to be four beats.
"""

from dataclasses import dataclass
from typing import Any

ROLE_DRUMS = "drums"
ROLE_PERCUSSION = "percussion"
ROLE_BASS = "bass"
ROLE_CHORDS = "chords"
ROLE_LEAD = "lead"
ROLE_TEXTURE = "texture"

_PITCH_CLASSES = {
    "c": 0,
    "c#": 1,
    "db": 1,
    "d": 2,
    "d#": 3,
    "eb": 3,
    "e": 4,
    "f": 5,
    "f#": 6,
    "gb": 6,
    "g": 7,
    "g#": 8,
    "ab": 8,
    "a": 9,
    "a#": 10,
    "bb": 10,
    "b": 11,
}

_MINOR_TRIAD = (0, 3, 7)
_MAJOR_TRIAD = (0, 4, 7)

_ROLE_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("kick", ROLE_DRUMS),
    ("drum", ROLE_DRUMS),
    ("snare", ROLE_DRUMS),
    ("clap", ROLE_PERCUSSION),
    ("hat", ROLE_PERCUSSION),
    ("perc", ROLE_PERCUSSION),
    ("ride", ROLE_PERCUSSION),
    ("sub", ROLE_BASS),
    ("bass", ROLE_BASS),
    ("chord", ROLE_CHORDS),
    ("pad", ROLE_CHORDS),
    ("key", ROLE_CHORDS),
    ("stab", ROLE_CHORDS),
    ("lead", ROLE_LEAD),
    ("arp", ROLE_LEAD),
    ("pluck", ROLE_LEAD),
    ("melody", ROLE_LEAD),
)

DRUM_KICK_PITCH = 36
DRUM_CLAP_PITCH = 39
DRUM_HAT_PITCH = 42


@dataclass(frozen=True)
class MidiNote:
    """One note to place in a Live clip."""

    pitch: int
    start_beats: float
    duration_beats: float
    velocity: int

    def to_dict(self) -> dict[str, Any]:
        """Serialize the note in the Live device's note format."""
        return {
            "pitch": self.pitch,
            "start": round(self.start_beats, 6),
            "duration": round(self.duration_beats, 6),
            "velocity": self.velocity,
        }


def role_for_track(track_name: str) -> str:
    """Return the pattern role KIHACHI infers from a track name."""
    lowered = track_name.lower()
    for keyword, role in _ROLE_KEYWORDS:
        if keyword in lowered:
            return role
    return ROLE_TEXTURE


def root_pitch(key: str, octave_offset: int = 0) -> int:
    """Return a MIDI pitch for the tonic of a key such as ``D#m``.

    Unparsable keys fall back to C, which keeps planning deterministic instead
    of raising in the middle of building a plan.
    """
    text = key.strip()
    if not text:
        return 36 + octave_offset * 12
    body = text[:-1] if text.lower().endswith("m") else text
    pitch_class = _PITCH_CLASSES.get(body.lower())
    if pitch_class is None:
        pitch_class = _PITCH_CLASSES.get(body[:1].lower(), 0)
    return 36 + pitch_class + octave_offset * 12


def is_minor(key: str) -> bool:
    """Report whether a key string denotes a minor key."""
    return key.strip().lower().endswith("m")


def build_notes(
    track_name: str,
    key: str,
    length_bars: int,
    beats_per_bar: float,
    density: float = 1.0,
) -> list[MidiNote]:
    """Return the deterministic note pattern for one Session View clip.

    ``density`` scales how busy a section is. It is applied by dropping whole
    events rather than by randomizing, so the result stays reproducible.
    """
    role = role_for_track(track_name)
    bars = max(1, length_bars)
    if role == ROLE_DRUMS:
        return _drum_pattern(bars, beats_per_bar, density)
    if role == ROLE_PERCUSSION:
        return _percussion_pattern(bars, beats_per_bar, density)
    if role == ROLE_BASS:
        return _bass_pattern(key, bars, beats_per_bar, density)
    if role == ROLE_CHORDS:
        return _chord_pattern(key, bars, beats_per_bar)
    if role == ROLE_LEAD:
        return _lead_pattern(key, bars, beats_per_bar, density)
    return _texture_pattern(key, bars, beats_per_bar)


def _drum_pattern(
    bars: int, beats_per_bar: float, density: float
) -> list[MidiNote]:
    notes: list[MidiNote] = []
    beats = int(bars * beats_per_bar)
    for beat in range(beats):
        notes.append(
            MidiNote(DRUM_KICK_PITCH, float(beat), 0.25, 112 if beat % 4 == 0 else 96)
        )
    if density >= 0.75:
        for beat in range(2, beats, 4):
            notes.append(MidiNote(DRUM_CLAP_PITCH, float(beat), 0.25, 88))
    return notes


def _percussion_pattern(
    bars: int, beats_per_bar: float, density: float
) -> list[MidiNote]:
    beats = int(bars * beats_per_bar)
    step = 0.5 if density >= 0.75 else 1.0
    notes: list[MidiNote] = []
    position = step
    while position < beats:
        notes.append(MidiNote(DRUM_HAT_PITCH, position, 0.125, 72))
        position += step * 2
    return notes


def _bass_pattern(
    key: str, bars: int, beats_per_bar: float, density: float
) -> list[MidiNote]:
    root = root_pitch(key, octave_offset=0)
    beats = int(bars * beats_per_bar)
    step = 0.5 if density >= 0.75 else 1.0
    notes: list[MidiNote] = []
    position = 0.0
    index = 0
    while position < beats:
        pitch = root if index % 4 != 3 else root + (7 if is_minor(key) else 5)
        notes.append(MidiNote(pitch, position, step * 0.9, 104))
        position += step
        index += 1
    return notes


def _chord_pattern(key: str, bars: int, beats_per_bar: float) -> list[MidiNote]:
    root = root_pitch(key, octave_offset=2)
    triad = _MINOR_TRIAD if is_minor(key) else _MAJOR_TRIAD
    notes: list[MidiNote] = []
    for bar in range(bars):
        start = bar * beats_per_bar
        for interval in triad:
            notes.append(MidiNote(root + interval, start, beats_per_bar * 0.95, 80))
    return notes


def _lead_pattern(
    key: str, bars: int, beats_per_bar: float, density: float
) -> list[MidiNote]:
    root = root_pitch(key, octave_offset=3)
    triad = _MINOR_TRIAD if is_minor(key) else _MAJOR_TRIAD
    beats = int(bars * beats_per_bar)
    step = 0.5 if density >= 0.75 else 1.0
    notes: list[MidiNote] = []
    position = 0.0
    index = 0
    while position < beats:
        notes.append(
            MidiNote(root + triad[index % len(triad)], position, step * 0.9, 92)
        )
        position += step
        index += 1
    return notes


def _texture_pattern(key: str, bars: int, beats_per_bar: float) -> list[MidiNote]:
    root = root_pitch(key, octave_offset=2)
    return [MidiNote(root, 0.0, bars * beats_per_bar * 0.98, 64)]
