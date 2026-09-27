"""Build reproducible Kick/Hats/Bass/Stab notes from a production brief."""

import hashlib
import json
import uuid
from random import Random

from kihachi_mcp.models.midi_candidate import CandidateClip, MidiCandidate
from kihachi_mcp.models.production_brief import STUDIO_PARTS, ProductionBrief
from kihachi_mcp.services.musical_time import beats_per_bar
from kihachi_mcp.services.session_pattern_builder import (
    MidiNote,
    is_minor,
    root_pitch,
)

KICK_PITCH = 36
HAT_PITCH = 42
MAX_CLIP_BARS = 16
# macOS loopback UDP send defaults to 9216 bytes. Stay under that so one
# replace_clip_notes datagram cannot raise EMSGSIZE.
SAFE_UDP_REQUEST_BYTES = 8000
_MINOR_TRIAD = (0, 3, 7)
_MAJOR_TRIAD = (0, 4, 7)
_DENSITY_STEPS = {"sparse": 2.0, "normal": 1.0, "dense": 0.5}


def build_candidate(
    brief: ProductionBrief,
    seed: int | None = None,
    parent_candidate_id: str = "",
    candidate_id: str | None = None,
) -> MidiCandidate:
    """Return one candidate whose preview notes are the apply notes."""
    resolved_seed = _resolve_seed(brief.original_text, seed)
    rng = Random(resolved_seed)
    beats = brief.beats_per_bar
    bars = int(brief.bars.value)
    clips: list[CandidateClip] = []
    for part in STUDIO_PARTS:
        song_notes = _notes_for_part(part, brief, bars, beats, rng)
        for section in brief.sections:
            for start, length in _clip_windows(
                section.start_bar, section.length_bars, song_notes, beats
            ):
                clips.append(
                    CandidateClip(
                        part=part,
                        section_name=section.name,
                        start_bar=start,
                        length_bars=length,
                        notes=_clip_notes(song_notes, start, length, beats),
                    )
                )
    return MidiCandidate(
        candidate_id=candidate_id or uuid.uuid4().hex,
        seed=resolved_seed,
        brief=brief,
        clips=tuple(clips),
        parent_candidate_id=parent_candidate_id,
    )


def seed_from_brief(text: str) -> int:
    """Return the default seed for a brief so identical input is reproducible."""
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return int(digest[:8], 16)


def _resolve_seed(text: str, seed: int | None) -> int:
    if seed is None:
        return seed_from_brief(text)
    if type(seed) is not int or seed < 0:
        raise ValueError("seed must be a non-negative integer")
    return seed


def _clip_windows(
    start_bar: int,
    length_bars: int,
    notes: list[MidiNote],
    beats: float,
) -> list[tuple[int, int]]:
    """Split a section so one UDP replace stays under the loopback size cap."""
    windows: list[tuple[int, int]] = []
    cursor = start_bar
    remaining = length_bars
    while remaining > 0:
        length = min(MAX_CLIP_BARS, remaining)
        while length > 1 and (
            estimate_replace_request_bytes(_clip_notes(notes, cursor, length, beats))
            > SAFE_UDP_REQUEST_BYTES
        ):
            length -= 1
        windows.append((cursor, length))
        cursor += length
        remaining -= length
    return windows


def estimate_replace_request_bytes(notes: tuple[MidiNote, ...] | list[MidiNote]) -> int:
    """Return the UDP size of one replace_clip_notes request for these notes."""
    payload_notes = [
        note.to_dict() if isinstance(note, MidiNote) else dict(note) for note in notes
    ]
    envelope = {
        "protocol": "kihachi.live",
        "version": 1,
        "request_id": "studio-" + ("0" * 32),
        "method": "apply_operation",
        "token": "t" * 43,
        "payload": {
            "operation": {
                "operation_id": "000-replace_clip_notes",
                "op": "replace_clip_notes",
                "target": {"track_index": 10, "scene_index": 10},
                "arguments": {
                    "notes": payload_notes,
                    "clip_name": "Section 000 xxxxxxxx [KIHACHI]",
                },
                "preconditions": [
                    {"kind": "not_recording", "arguments": {}},
                    {
                        "kind": "track_name_at_index",
                        "arguments": {
                            "track_index": 10,
                            "name": "KIHACHI Hats xxxxxxxx [KIHACHI]",
                        },
                    },
                ],
                "destructive": False,
                "expected_readback": {
                    "track_index": 10,
                    "scene_index": 10,
                    "note_count": len(payload_notes),
                },
            }
        },
    }
    return len(json.dumps(envelope, separators=(",", ":")).encode("utf-8"))


def _clip_notes(
    notes: list[MidiNote], start_bar: int, length_bars: int, beats: float
) -> tuple[MidiNote, ...]:
    start = (start_bar - 1) * beats
    end = start + length_bars * beats
    clipped: list[MidiNote] = []
    for note in notes:
        if start <= note.start_beats < end:
            clipped.append(
                MidiNote(
                    note.pitch,
                    round(note.start_beats - start, 6),
                    note.duration_beats,
                    note.velocity,
                )
            )
    return tuple(clipped)


def _notes_for_part(
    part: str,
    brief: ProductionBrief,
    bars: int,
    beats: float,
    rng: Random,
) -> list[MidiNote]:
    if part == "Kick":
        return _kick_notes(brief, bars, beats)
    if part == "Hats":
        return _hat_notes(brief, bars, beats)
    if part == "Bass":
        return _bass_notes(brief, bars, beats)
    return _stab_notes(brief, bars, beats, rng)


def _kick_notes(brief: ProductionBrief, bars: int, beats: float) -> list[MidiNote]:
    notes: list[MidiNote] = []
    total_beats = int(bars * beats)
    drop = int(brief.drop_start_bar.value)
    drop_beat = (drop - 1) * beats if drop else total_beats
    density = str(brief.note_density.value)
    for beat in range(total_beats):
        in_drop = beat >= drop_beat
        intro = beat < total_beats * 0.25
        if intro and density == "sparse" and beat % 2:
            continue
        velocity = 118 if beat % 4 == 0 else 104
        if in_drop:
            velocity = min(127, velocity + 6)
        notes.append(MidiNote(KICK_PITCH, float(beat), 0.25, velocity))
    return notes


def _hat_notes(brief: ProductionBrief, bars: int, beats: float) -> list[MidiNote]:
    notes: list[MidiNote] = []
    midpoint = bars / 2
    for bar in range(1, bars + 1):
        half = "first" if bar <= midpoint else "second"
        density = str(getattr(brief, f"hats_{half}_half").value)
        step = _DENSITY_STEPS[density]
        start = (bar - 1) * beats
        position = start + (0.5 if density != "dense" else 0.0)
        limit = start + beats
        while position < limit:
            velocity = 64 if density == "sparse" else 78 if density == "normal" else 90
            notes.append(MidiNote(HAT_PITCH, position, 0.125, velocity))
            if density == "dense":
                notes.append(MidiNote(HAT_PITCH, position + 0.25, 0.08, 62))
            position += step
    return notes


def _bass_notes(brief: ProductionBrief, bars: int, beats: float) -> list[MidiNote]:
    octave = {"low": -1, "mid": 0, "high": 1}[str(brief.bass_register.value)]
    root = root_pitch(str(brief.key.value), octave_offset=octave)
    fifth = 7 if is_minor(str(brief.key.value)) else 5
    notes: list[MidiNote] = []
    density = str(brief.note_density.value)
    for bar in range(1, bars + 1):
        section = _section_name(brief, bar)
        start = (bar - 1) * beats
        if section == "Intro":
            notes.append(MidiNote(root, start, beats * 0.9, 96))
            continue
        step = 2.0 if density == "sparse" or section == "Build" else 1.0
        if section == "Drop" and density != "sparse":
            step = 0.5
        index = 0
        position = start
        while position < start + beats:
            pitch = root if index % 4 != 3 else root + fifth
            notes.append(MidiNote(pitch, position, min(step * 0.85, 0.9), 108))
            position += step
            index += 1
    return notes


def _stab_notes(
    brief: ProductionBrief, bars: int, beats: float, rng: Random
) -> list[MidiNote]:
    dark = str(brief.mood.value) in {"暗い", "ダーク"}
    minor = is_minor(str(brief.key.value)) or dark
    root = root_pitch(str(brief.key.value), octave_offset=1 if dark else 2)
    triad = _MINOR_TRIAD if minor else _MAJOR_TRIAD
    notes: list[MidiNote] = []
    for bar in range(1, bars + 1):
        section = _section_name(brief, bar)
        start = (bar - 1) * beats
        if section == "Intro":
            if bar % 4 == 1:
                notes.extend(_chord(root, triad, start, beats * 0.4, 68))
            continue
        if section == "Outro":
            if bar % 2 == 1:
                notes.extend(_chord(root, triad, start, beats * 0.35, 64))
            continue
        hits = (0.0,) if section == "Build" else (0.0, 1.5)
        if str(brief.note_density.value) == "dense" and section == "Drop":
            hits = (0.0, 1.0, 2.5)
        accent = rng.choice((0, 0, 7)) if section == "Drop" else 0
        for offset in hits:
            notes.extend(_chord(root + accent, triad, start + offset, 0.35, 86))
    return notes


def _chord(
    root: int, triad: tuple[int, ...], start: float, duration: float, velocity: int
) -> list[MidiNote]:
    return [MidiNote(root + interval, start, duration, velocity) for interval in triad]


def _section_name(brief: ProductionBrief, bar: int) -> str:
    for section in brief.sections:
        if section.start_bar <= bar <= section.end_bar:
            return section.name
    return "Drop"


def hat_counts_by_half(candidate: MidiCandidate) -> tuple[int, int]:
    """Return first-half and second-half hat note counts for tests and the UI."""
    bars = int(candidate.brief.bars.value)
    midpoint = bars / 2
    first = 0
    second = 0
    beats = beats_per_bar(
        candidate.brief.meter_numerator, candidate.brief.meter_denominator
    )
    for clip in candidate.clips_for_part("Hats"):
        for note in clip.notes:
            absolute_bar = clip.start_bar + note.start_beats / beats
            if absolute_bar <= midpoint:
                first += 1
            else:
                second += 1
    return first, second
