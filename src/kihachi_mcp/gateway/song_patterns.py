"""Deterministic patterns for the Mutation Funk × DUB × Tech House preset.

Every time value written into a note is a beat inside the song clip.
``seed`` only changes ghost-note velocity. The same seed always replays.
"""

from __future__ import annotations

from kihachi_mcp.gateway.song_time import BEATS_PER_BAR, swing_beat

ROLES: tuple[str, ...] = (
    "KICK",
    "DRUMS",
    "PERCUSSION",
    "SLAP BASS",
    "SUB BASS",
    "DUB CHORDS",
    "SYNTH STABS",
    "ARP",
    "VOCODER CARRIER",
    "VOCAL",
    "FX",
)

# Pitch classes of D# natural minor: D# E#(F) F# G# A# B C#.
_SCALE = frozenset({1, 3, 5, 6, 8, 10, 11})

_CHORDS = (
    (63, 66, 70),  # D#m
    (68, 71, 75),  # G#m
    (71, 75, 78),  # B
    (70, 73, 77),  # A#m
)

# (beat inside a 2-bar cell, pitch, duration, velocity or None for a ghost)
_SLAP_CELL = (
    (0.00, 39, 0.45, 114),
    (0.75, 42, 0.12, None),
    (1.50, 39, 0.28, 104),
    (2.25, 46, 0.18, 98),
    (3.00, 39, 0.12, None),
    (3.75, 51, 0.32, 110),
    (4.50, 39, 0.36, 116),
    (5.25, 37, 0.16, 90),
    (6.00, 42, 0.22, 102),
    (7.25, 34, 0.28, 96),
)

_SUB_CELL = (
    (0.0, 27, 1.4, 102),
    (2.0, 27, 0.8, 94),
    (4.0, 27, 1.2, 104),
    (6.5, 34, 0.7, 90),
)

_VOCAL_CELL = (
    (0.0, 63, 0.45, 98),
    (1.5, 66, 0.30, 90),
    (2.5, 70, 0.40, 104),
    (4.0, 68, 0.35, 92),
    (5.5, 66, 0.25, 86),
    (6.5, 63, 0.50, 100),
)

_CYCLE = (
    ("intro", 1, 8),
    ("groove", 9, 16),
    ("main", 17, 24),
    ("outro", 25, 32),
)


def harmonic_pitch(pitch: int) -> bool:
    """True when the MIDI note is inside D# natural minor."""
    return pitch % 12 in _SCALE


def generate_role_notes(role: str, bars: int, swing: float, seed: int) -> list[dict[str, float | int]]:
    """Notes for one role across the whole song, in clip-relative beats."""
    if role not in ROLES:
        raise ValueError(f"未知のロールです: {role}")
    ghost = 58 + (seed % 7)
    notes: list[dict[str, float | int]] = []
    for bar, section, section_start, section_end in _bars(bars):
        if role == "KICK":
            _kick(notes, bar, section, section_start, section_end)
        elif role == "DRUMS":
            _drums(notes, bar, section, swing)
        elif role == "PERCUSSION":
            _percussion(notes, bar, section, swing)
        elif role == "SLAP BASS":
            _cell(notes, _SLAP_CELL, bar, section, swing, ghost, swung=True)
        elif role == "SUB BASS":
            _cell(notes, _SUB_CELL, bar, section, swing, ghost, swung=False)
        elif role == "DUB CHORDS":
            _dub(notes, bar, section)
        elif role == "SYNTH STABS":
            _stabs(notes, bar, section)
        elif role == "ARP":
            _arp(notes, bar, section, swing)
        elif role == "VOCODER CARRIER":
            _carrier(notes, bar, section)
        elif role == "VOCAL":
            _cell(notes, _VOCAL_CELL, bar, section, swing, ghost, swung=False)
        elif role == "FX":
            _fx(notes, bar, section, swing)
    length = bars * BEATS_PER_BAR
    for note in notes:
        start = float(note["start"])
        if start < 0 or start >= length or float(note["duration"]) <= 0:
            raise RuntimeError(f"{role} のノートがクリップの外です")
    return notes


def section_plan(bars: int) -> list[dict[str, object]]:
    """32-bar form, repeated when the song is 64 or 128 bars."""
    sections: list[dict[str, object]] = []
    for cycle in range(bars // 32):
        offset = cycle * 32
        suffix = "" if cycle == 0 else f" {cycle + 1}"
        sections.extend(
            (
                {
                    "name": f"INTRO{suffix}",
                    "start_bar": 1 + offset,
                    "end_bar": 8 + offset,
                    "roles": ["KICK", "PERCUSSION", "DUB CHORDS", "FX"],
                },
                {
                    "name": f"GROOVE{suffix}",
                    "start_bar": 9 + offset,
                    "end_bar": 16 + offset,
                    "roles": ["KICK", "DRUMS", "PERCUSSION", "SLAP BASS", "SUB BASS", "DUB CHORDS"],
                },
                {
                    "name": f"MAIN{suffix}",
                    "start_bar": 17 + offset,
                    "end_bar": 24 + offset,
                    "roles": list(ROLES),
                },
                {
                    "name": f"OUTRO{suffix}",
                    "start_bar": 25 + offset,
                    "end_bar": 32 + offset,
                    "roles": ["KICK", "PERCUSSION", "DUB CHORDS", "FX"],
                },
            )
        )
    return sections


def _bars(bars: int):
    for bar in range(1, bars + 1):
        local = (bar - 1) % 32 + 1
        base = ((bar - 1) // 32) * 32
        for name, start, end in _CYCLE:
            if start <= local <= end:
                yield bar, name, start + base, end + base
                break


def _plays(role: str, section: str) -> bool:
    if role == "KICK":
        return section in {"intro", "groove", "main", "outro"}
    if role == "DRUMS":
        return section in {"groove", "main"}
    if role == "PERCUSSION":
        return section in {"intro", "groove", "main", "outro"}
    if role in {"SLAP BASS", "SUB BASS"}:
        return section in {"groove", "main"}
    if role == "DUB CHORDS":
        return section in {"intro", "groove", "main", "outro"}
    if role in {"SYNTH STABS", "ARP", "VOCODER CARRIER", "VOCAL"}:
        return section == "main"
    if role == "FX":
        return section in {"intro", "outro"}
    return False


def _kick(notes: list[dict[str, float | int]], bar: int, section: str, start: int, end: int) -> None:
    if section == "outro" and bar >= start + (end - start + 1) // 2:
        return
    if not _plays("KICK", section):
        return
    for beat in (0.0, 1.0, 2.0, 3.0):
        _add(notes, bar, beat, 36, 0.2, 118, 0.5, swung=False)


def _drums(notes: list[dict[str, float | int]], bar: int, section: str, swing: float) -> None:
    if not _plays("DRUMS", section):
        return
    for beat in (1.0, 3.0):
        _add(notes, bar, beat, 38, 0.15, 112, swing, swung=False)
        _add(notes, bar, beat, 39, 0.1, 96, swing, swung=False)
    for beat in (0.5, 1.5, 2.5, 3.5):
        _add(notes, bar, beat, 42, 0.08, 78, swing, swung=False)
    for beat in (0.25, 0.75, 1.25, 1.75, 2.25, 2.75, 3.25, 3.75):
        _add(notes, bar, beat, 42, 0.05, 64, swing, swung=True)


def _percussion(notes: list[dict[str, float | int]], bar: int, section: str, swing: float) -> None:
    if not _plays("PERCUSSION", section):
        return
    if bar % 2 == 1:
        hits = (0.75, 2.25)
        if section != "outro":
            hits = (0.75, 2.25, 3.5)
    else:
        hits = (1.75,)
        if section != "outro":
            hits = (0.25, 1.75, 3.25)
    for index, beat in enumerate(hits):
        _add(notes, bar, beat, 64 if index % 2 == 0 else 65, 0.12, 86, swing, swung=True)


def _dub(notes: list[dict[str, float | int]], bar: int, section: str) -> None:
    if not _plays("DUB CHORDS", section):
        return
    if section == "intro" and bar % 4 != 1:
        return
    if section == "outro" and bar % 2 == 0:
        return
    for beat in (0.0, 2.5):
        for pitch in _CHORDS[(bar - 1) % 4]:
            _add(notes, bar, beat, pitch, 0.28, 88, 0.5, swung=False)


def _stabs(notes: list[dict[str, float | int]], bar: int, section: str) -> None:
    if not _plays("SYNTH STABS", section):
        return
    chord = _CHORDS[(bar - 1) % 4]
    _add(notes, bar, 0.5, chord[-1], 0.18, 92, 0.5, swung=False)
    _add(notes, bar, 2.0, chord[0] + 12, 0.15, 84, 0.5, swung=False)


def _arp(notes: list[dict[str, float | int]], bar: int, section: str, swing: float) -> None:
    if not _plays("ARP", section):
        return
    chord = _CHORDS[(bar - 1) % 4]
    for step in range(16):
        if step % 4 == 3:
            continue
        velocity = 104 if step % 4 == 0 else 72
        _add(notes, bar, step * 0.25, chord[step % 3], 0.12, velocity, swing, swung=True)


def _carrier(notes: list[dict[str, float | int]], bar: int, section: str) -> None:
    if not _plays("VOCODER CARRIER", section):
        return
    for beat in (0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5):
        _add(notes, bar, beat, 58, 0.32, 80, 0.5, swung=False)


def _fx(notes: list[dict[str, float | int]], bar: int, section: str, swing: float) -> None:
    if not _plays("FX", section):
        return
    _add(notes, bar, 0.0, 77, 0.5, 70, swing, swung=False)
    if section == "intro":
        _add(notes, bar, 3.25, 82, 0.1, 60, swing, swung=True)


def _cell(
    notes: list[dict[str, float | int]],
    cell: tuple[tuple[float, int, float, int | None], ...],
    bar: int,
    section: str,
    swing: float,
    ghost: int,
    *,
    swung: bool,
) -> None:
    role = "SLAP BASS" if cell is _SLAP_CELL else "SUB BASS"
    if cell is _VOCAL_CELL:
        role = "VOCAL"
    if not _plays(role, section) or (bar - 1) % 2 != 0:
        return
    for beat, pitch, duration, velocity in cell:
        _add(notes, bar, beat, pitch, duration, ghost if velocity is None else velocity, swing, swung=swung)


def _add(
    notes: list[dict[str, float | int]],
    bar: int,
    beat: float,
    pitch: int,
    duration: float,
    velocity: int,
    swing: float,
    *,
    swung: bool,
) -> None:
    start = (bar - 1) * BEATS_PER_BAR + beat
    if swung:
        start = swing_beat(start, swing)
    notes.append(
        {
            "pitch": pitch,
            "start": round(start, 6),
            "duration": duration,
            "velocity": velocity,
            "probability": 1.0,
        }
    )
