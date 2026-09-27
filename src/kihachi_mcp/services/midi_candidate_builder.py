"""Build reproducible Kick/Hats/Bass/Stab notes from a production brief."""

import hashlib
import json
import uuid
from random import Random
from typing import Any

from kihachi_mcp.models.midi_candidate import CandidateClip, MidiCandidate
from kihachi_mcp.models.production_brief import STUDIO_PARTS, ProductionBrief
from kihachi_mcp.services.musical_time import beats_per_bar
from kihachi_mcp.services.session_pattern_builder import (
    MidiNote,
    is_minor,
    root_pitch,
)

KICK_PITCH = 36
CLAP_PITCH = 39
HAT_PITCH = 42
OPEN_HAT_PITCH = 46
HAT_PITCHES = frozenset({HAT_PITCH, OPEN_HAT_PITCH})
MAX_CLIP_BARS = 16
PHRASE_BARS = 8
# macOS loopback UDP send defaults to 9216 bytes. Stay under that so one
# replace_clip_notes datagram cannot raise EMSGSIZE.
SAFE_UDP_REQUEST_BYTES = 8000
_MINOR_SCALE = (0, 2, 3, 5, 7, 8, 10)
_MAJOR_SCALE = (0, 2, 4, 5, 7, 9, 11)
# Scale degrees, 0-based: in minor 0=i 3=iv 4=v 5=VI 6=VII.
_MINOR_PROGRESSIONS = ((0, 5, 6, 5), (0, 3, 5, 4), (0, 0, 5, 6), (0, 6, 5, 6))
_MAJOR_PROGRESSIONS = ((0, 5, 3, 4), (0, 3, 4, 3), (0, 4, 5, 3))
# Sixteenth steps in a 4/4 bar. None of them land on the kick's quarter notes.
_BASS_PATTERNS = {
    "offbeat": (2, 6, 10, 14),
    "gallop": (2, 3, 6, 7, 10, 11, 14, 15),
    "rolling": (1, 2, 3, 5, 6, 7, 9, 10, 11, 13, 14, 15),
    "syncopated": (2, 3, 6, 10, 11, 14),
}
_BASS_RHYTHMS = {
    "sparse": ("offbeat",),
    "normal": ("offbeat", "gallop", "syncopated"),
    "dense": ("rolling", "gallop", "syncopated"),
}
# Mostly the chord root, sometimes its octave, fifth or seventh.
_BASS_MOTIF_POOL = (0, 0, 0, 0, 12, 7, 10)
_STAB_RHYTHMS = (
    (0.5, 2.5),
    (0.75, 2.25),
    (1.5, 3.5),
    (0.5, 1.75, 3.0),
    (0.0, 1.5, 2.75),
)
# Velocities per sixteenth for closed hats; the offbeat is the loudest.
_HAT_ACCENTS = (
    (70, 48, 92, 52),
    (64, 56, 88, 60),
    (74, 44, 96, 58),
)
# What every second bar of a phrase changes. Chosen once per phrase.
_HAT_VARIATIONS = ("ghost_clap", "open_push", "skip")
# Where the last beat of a 16-bar phrase puts its kicks instead of beat 4.
_KICK_FILLS = ((0.5, 0.75), (0.25, 0.75), (0.75,))


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
    # One generator per part, seeded from the shared one, so a change inside
    # one part does not reshuffle the random choices of the others.
    part_rng = Random(rng.random())
    plan = _SongPlan(brief, bars, beats, part_rng)
    if part == "Kick":
        return _kick_notes(plan)
    if part == "Hats":
        return _hat_notes(plan)
    if part == "Bass":
        return _bass_notes(plan)
    return _stab_notes(plan)


class _Bar:
    """Where one bar sits: its section, phrase, and the breakdown before a drop."""

    def __init__(self, plan: "_SongPlan", bar: int) -> None:
        section = plan.section_of(bar)
        self.number = bar
        self.start = (bar - 1) * plan.beats
        self.section = section.name if section else "Drop"
        section_start = section.start_bar if section else 1
        section_end = section.end_bar if section else plan.bars
        self.in_section = bar - section_start
        self.left_in_section = section_end - bar
        self.phrase = self.in_section // PHRASE_BARS
        self.phrase_end = self.in_section % PHRASE_BARS == PHRASE_BARS - 1
        self.long_phrase_end = self.in_section % (PHRASE_BARS * 2) == PHRASE_BARS * 2 - 1
        self.breakdown = bar in plan.breakdown
        self.breakdown_left = (
            plan.breakdown.stop - 1 - bar if self.breakdown else -1
        )


class _SongPlan:
    """Choices shared by every bar of one part: patterns, progression, accents."""

    def __init__(
        self, brief: ProductionBrief, bars: int, beats: float, rng: Random
    ) -> None:
        self.brief = brief
        self.bars = bars
        self.beats = beats
        self.steps = max(1, round(beats * 4))
        self.rng = rng
        self.density = str(brief.note_density.value)
        self.minor = is_minor(str(brief.key.value)) or str(brief.mood.value) in {
            "暗い",
            "ダーク",
        }
        self.scale = _MINOR_SCALE if self.minor else _MAJOR_SCALE
        self.progression = rng.choice(
            _MINOR_PROGRESSIONS if self.minor else _MAJOR_PROGRESSIONS
        )
        self.breakdown = _breakdown_bars(brief)
        self._sections = tuple(brief.sections)

    def section_of(self, bar: int) -> Any:
        for section in self._sections:
            if section.start_bar <= bar <= section.end_bar:
                return section
        return None

    def bar(self, number: int) -> _Bar:
        return _Bar(self, number)

    def chord_degree(self, bar: _Bar) -> int:
        """Return the scale degree the harmony sits on in this bar."""
        if bar.section in {"Intro", "Outro"} and not bar.breakdown:
            return 0
        bars_per_chord = 1 if bar.section == "Drop" and not bar.breakdown else 2
        index = (bar.in_section // bars_per_chord) % len(self.progression)
        return self.progression[index]

    def degree_offset(self, degree: int) -> int:
        """Semitones from the tonic to a scale degree, wrapping upward."""
        octave, step = divmod(degree, len(self.scale))
        return self.scale[step] + 12 * octave

    def nearest_degree_offset(self, degree: int) -> int:
        """The same degree folded to within a fifth of the tonic, for the bass."""
        offset = self.degree_offset(degree) % 12
        return offset - 12 if offset > 6 else offset

    def velocity(self, base: int, spread: int = 5) -> int:
        return max(1, min(127, base + self.rng.randint(-spread, spread)))


def _breakdown_bars(brief: ProductionBrief) -> range:
    """The last bars of the Build: the kick drops out so the Drop lands."""
    build = next((section for section in brief.sections if section.name == "Build"), None)
    if build is None or build.length_bars < 4:
        return range(0)
    length = min(PHRASE_BARS, build.length_bars // 2)
    return range(build.end_bar - length + 1, build.end_bar + 1)


def _kick_notes(plan: _SongPlan) -> list[MidiNote]:
    notes: list[MidiNote] = []
    fill = plan.rng.choice(_KICK_FILLS)
    whole_beats = int(plan.beats)
    for number in range(1, plan.bars + 1):
        bar = plan.bar(number)
        if bar.breakdown:
            continue
        if bar.section == "Outro" and bar.left_in_section == 0:
            continue  # leave the last bar open for whatever comes next
        half_time = (
            bar.section == "Intro" and plan.density == "sparse" and bar.phrase == 0
        )
        positions: list[tuple[float, int]] = []
        for beat in range(whole_beats):
            if half_time and beat % 2:
                continue
            positions.append((float(beat), 118 if beat == 0 else 110))
        next_is_breakdown = number + 1 in plan.breakdown
        if bar.long_phrase_end and not next_is_breakdown and bar.left_in_section > 0:
            positions = [item for item in positions if item[0] < plan.beats - 1]
            positions.extend((plan.beats - 1 + offset, 96) for offset in fill)
        boost = 4 if bar.section == "Drop" else 0
        for offset, base in positions:
            notes.append(
                MidiNote(
                    KICK_PITCH,
                    round(bar.start + offset, 6),
                    0.25,
                    plan.velocity(base + boost, 3),
                )
            )
    return notes


def _hat_notes(plan: _SongPlan) -> list[MidiNote]:
    notes: list[MidiNote] = []
    accents = plan.rng.choice(_HAT_ACCENTS)
    variations: dict[tuple[str, int], str] = {}
    midpoint = plan.bars / 2
    for number in range(1, plan.bars + 1):
        bar = plan.bar(number)
        half = "first" if number <= midpoint else "second"
        density = str(getattr(plan.brief, f"hats_{half}_half").value)
        if bar.breakdown:
            notes.extend(_breakdown_hats(plan, bar))
            continue
        if bar.section == "Intro" and bar.phrase == 0:
            continue  # kick alone first, so the hats have something to arrive on
        if bar.section == "Outro" and bar.left_in_section < PHRASE_BARS // 2:
            density = "sparse"
        open_hats = (density != "sparse" and bar.section in {"Build", "Drop"}) or (
            bar.section == "Outro" and bar.in_section < PHRASE_BARS
        )
        key = (bar.section, bar.phrase)
        if key not in variations:
            variations[key] = plan.rng.choice(_HAT_VARIATIONS)
        # Every second bar answers the first with a small change.
        variation = variations[key] if bar.in_section % 2 == 1 else "plain"
        skipped = plan.steps - 5 if variation == "skip" else -1
        for step in range(plan.steps):
            position = step / 4
            offbeat = step % 4 == 2
            if step == skipped:
                continue
            if open_hats and offbeat:
                notes.append(
                    MidiNote(
                        OPEN_HAT_PITCH,
                        round(bar.start + position, 6),
                        0.25,
                        plan.velocity(84),
                    )
                )
                continue
            if not _hat_step_plays(density, step):
                continue
            notes.append(
                MidiNote(
                    HAT_PITCH,
                    round(bar.start + position, 6),
                    0.1,
                    plan.velocity(accents[step % len(accents)]),
                )
            )
        if bar.phrase_end and bar.left_in_section > 0:
            notes.extend(_hat_roll(plan, bar))
        elif variation == "ghost_clap" and _clap_plays(plan, bar):
            notes.append(
                MidiNote(CLAP_PITCH, round(bar.start + plan.beats - 0.25, 6), 0.2, plan.velocity(64))
            )
        elif variation == "open_push" and open_hats:
            notes.append(
                MidiNote(OPEN_HAT_PITCH, round(bar.start + plan.beats - 0.25, 6), 0.25, plan.velocity(70))
            )
        if _clap_plays(plan, bar):
            for beat in range(1, int(plan.beats), 2):
                notes.append(
                    MidiNote(
                        CLAP_PITCH,
                        round(bar.start + beat, 6),
                        0.25,
                        plan.velocity(100),
                    )
                )
    return notes


def _hat_step_plays(density: str, step: int) -> bool:
    if density == "sparse":
        return step % 4 == 2
    if density == "normal":
        return step % 2 == 0
    return True


def _hat_roll(plan: _SongPlan, bar: _Bar) -> list[MidiNote]:
    """Sixteenths across the last beat of a phrase, rising into the next one."""
    last = plan.beats - 1
    return [
        MidiNote(HAT_PITCH, round(bar.start + last + index / 4 + 0.125, 6), 0.08, 56 + 12 * index)
        for index in range(4)
    ]


def _breakdown_hats(plan: _SongPlan, bar: _Bar) -> list[MidiNote]:
    """Silence first, then a closed-hat and clap roll that climbs to the Drop."""
    length = len(plan.breakdown)
    position_in = length - 1 - bar.breakdown_left
    if position_in < length // 2:
        return []
    notes: list[MidiNote] = []
    rising = (position_in - length // 2 + 1) / max(1, length - length // 2)
    for step in range(plan.steps):
        progress = rising * (step + 1) / plan.steps
        notes.append(
            MidiNote(
                HAT_PITCH,
                round(bar.start + step / 4, 6),
                0.08,
                max(1, min(127, int(40 + 70 * progress))),
            )
        )
    if bar.breakdown_left <= 1:
        spacing = 0.5 if bar.breakdown_left == 1 else 0.25
        count = int(plan.beats / spacing)
        for index in range(count):
            notes.append(
                MidiNote(
                    CLAP_PITCH,
                    round(bar.start + index * spacing, 6),
                    0.2,
                    max(1, min(127, int(60 + 60 * (index + 1) / count))),
                )
            )
    return notes


def _clap_plays(plan: _SongPlan, bar: _Bar) -> bool:
    if bar.section == "Intro":
        return bar.left_in_section < PHRASE_BARS
    if bar.section == "Outro":
        return bar.in_section < PHRASE_BARS
    return True


def _bass_notes(plan: _SongPlan) -> list[MidiNote]:
    octave = {"low": -1, "mid": 0, "high": 1}[str(plan.brief.bass_register.value)]
    root = root_pitch(str(plan.brief.key.value), octave_offset=octave)
    choices = _BASS_RHYTHMS[plan.density]
    notes: list[MidiNote] = []
    rhythms: dict[tuple[str, int], tuple[int, ...]] = {}
    motifs: dict[tuple[str, int], tuple[int, ...]] = {}
    for number in range(1, plan.bars + 1):
        bar = plan.bar(number)
        chord_root = root + plan.nearest_degree_offset(plan.chord_degree(bar))
        if bar.breakdown:
            notes.append(MidiNote(chord_root, bar.start, plan.beats * 0.95, 92))
            continue
        if bar.section == "Intro" and bar.in_section < _intro_bass_entry(bar, plan):
            continue
        if bar.section == "Outro" and bar.left_in_section < PHRASE_BARS:
            continue
        key = (bar.section, bar.phrase)
        if key not in rhythms:
            name = "offbeat" if bar.section == "Intro" else plan.rng.choice(choices)
            rhythms[key] = _BASS_PATTERNS[name]
            motifs[key] = tuple(plan.rng.choice(_BASS_MOTIF_POOL) for _ in range(4))
        steps = [step for step in rhythms[key] if step < plan.steps]
        motif = motifs[key]
        length = 0.2 if len(steps) > 6 else 0.4
        for index, step in enumerate(steps):
            interval = motif[index % len(motif)]
            if bar.phrase_end and index >= len(steps) - 2:
                interval = 12  # lift at the end of a phrase
            if interval == 10 and not plan.minor:
                interval = 7  # a major seventh in the bass sounds wrong here
            notes.append(
                MidiNote(
                    chord_root + interval,
                    round(bar.start + step / 4, 6),
                    length,
                    plan.velocity(104 if step % 4 == 2 else 96, 4),
                )
            )
    return notes


def _intro_bass_entry(bar: _Bar, plan: _SongPlan) -> int:
    """The bass waits for the second half of the Intro."""
    section = plan.section_of(bar.number)
    length = section.length_bars if section else plan.bars
    return length // 2


def _stab_notes(plan: _SongPlan) -> list[MidiNote]:
    key = str(plan.brief.key.value)
    dark = str(plan.brief.mood.value) in {"暗い", "ダーク"}
    tonic = root_pitch(key, octave_offset=1 if dark else 2)
    extra = 3.75 if plan.density == "dense" else None
    notes: list[MidiNote] = []
    rhythms: dict[tuple[str, int], tuple[float, ...]] = {}
    for number in range(1, plan.bars + 1):
        bar = plan.bar(number)
        chord = _voiced_chord(plan, tonic, plan.chord_degree(bar))
        if bar.breakdown:
            # Held chords, one per bar: a note may not cross a clip boundary.
            notes.extend(_chord_notes(chord, bar.start, plan.beats * 0.95, 70))
            continue
        if bar.section == "Intro":
            if bar.left_in_section < PHRASE_BARS and bar.in_section % 2 == 0:
                notes.extend(_chord_notes(chord, bar.start + 0.5, 0.3, plan.velocity(66)))
            continue
        if bar.section == "Outro":
            if bar.in_section < PHRASE_BARS:
                notes.extend(_chord_notes(chord, bar.start + 0.5, 0.3, plan.velocity(70)))
            continue
        rhythm_key = (bar.section, bar.phrase)
        if rhythm_key not in rhythms:
            rhythms[rhythm_key] = plan.rng.choice(_STAB_RHYTHMS)
        hits = [hit for hit in rhythms[rhythm_key] if hit < plan.beats]
        if bar.section == "Build" and bar.phrase == 0:
            hits = hits[:1]
        if extra is not None and bar.section == "Drop" and extra < plan.beats:
            hits.append(extra)
        if bar.phrase_end and plan.beats - 0.75 not in hits:
            hits.append(plan.beats - 0.75)
        for hit in sorted(set(hits)):
            notes.extend(
                _chord_notes(chord, bar.start + hit, 0.3, plan.velocity(88))
            )
    return notes


def _voiced_chord(plan: _SongPlan, tonic: int, degree: int) -> tuple[int, ...]:
    """Stack scale thirds on a degree and keep every tone near the tonic."""
    tones = [tonic + plan.degree_offset(degree + step) for step in (0, 2, 4)]
    low = tonic - 2
    return tuple(sorted(low + (tone - low) % 12 for tone in tones))


def _chord_notes(
    chord: tuple[int, ...], start: float, duration: float, velocity: int
) -> list[MidiNote]:
    return [MidiNote(pitch, round(start, 6), duration, velocity) for pitch in chord]


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
            if note.pitch not in HAT_PITCHES:
                continue  # the clap shares the Hats track but is not a hat
            absolute_bar = clip.start_bar + note.start_beats / beats
            if absolute_bar <= midpoint:
                first += 1
            else:
                second += 1
    return first, second
