"""Build reproducible drum, bass, harmony and effect notes from a production brief."""

import hashlib
import json
import uuid
from dataclasses import replace
from random import Random
from typing import Any

from kihachi_mcp.knowledge.genre_database import find as find_genre
from kihachi_mcp.knowledge.genre_profiles import (
    RIDE,
    bass_role,
    chord_articulation,
    drum_pattern,
    hat_positions,
    profile_for,
    swung,
)
from kihachi_mcp.knowledge.sound_recipes import recipe_for
from kihachi_mcp.models.midi_candidate import CandidateClip, MidiCandidate
from kihachi_mcp.models.production_brief import (
    ARRANGEMENT_PARTS,
    PART_ORDER,
    STUDIO_PARTS,
    ProductionBrief,
)
from kihachi_mcp.services.brief_parser import explicit_swing, read_explicit
from kihachi_mcp.services.drum_samples import (
    CONGA_HIGH_NOTE,
    CONGA_LOW_NOTE,
    IMPACT_NOTE,
    RISER_NOTE,
    RISER_SECONDS,
    SHAKER_NOTE,
)
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
HAT_PITCHES = frozenset({HAT_PITCH, OPEN_HAT_PITCH, RIDE})
#: Side stick, snare and clap: the backbeat voices, played on their own track.
SNARE_PITCHES = frozenset({37, 38, CLAP_PITCH})
#: Written by a generator of their own, after the original parts.
_GENERATED_EXTRAS = ("Perc", "Pad", "Arp", "Vocal", "FX", "Guitar", "Horn")
_BAND_FAMILIES = frozenset(
    {
        "R&B / Soul / Funk",
        "Disco",
        "Jazz",
        "Blues",
        "Brazilian",
        "Latin",
        "Rock",
        "Punk / Hardcore",
        "Metal",
        "Country / Americana",
        "Folk",
        "Reggae / Dub / Ska",
        "Hip-Hop / Rap",
    }
)
#: Parts only some families play, and the families besides the band ones.
#: Funk guitar and brass in a tech house track crowd the groove.
_FAMILY_PARTS = {
    "Guitar": frozenset(),
    "Horn": frozenset(),
    "Vocal": frozenset({"House", "UK Garage / Bass", "EDM / Future Bass"}),
}
#: Words that ask for a part the genre would leave out.
_PART_WORDS = {
    "Guitar": ("ギター", "guitar"),
    "Horn": ("ホーン", "ブラス", "horn", "brass"),
    "Vocal": ("ボイス", "ボーカルチョップ", "vocal chop"),
}
# Scale degrees for the Break, ending on the chord that leads back to i or I.
_BREAK_MINOR = (5, 6, 3, 4)
_BREAK_MAJOR = (3, 4, 5, 4)
#: The sub sits in this octave whatever register the bass plays in.
SUB_LOWEST_PITCH = 24
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
_MUTATION_PROGRESSIONS = ((0, 5, 6, 4), (0, 3, 5, 4), (0, 5, 3, 4))
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
_MUTATION_KICK_GROOVES = (
    ((0.0, 1.5, 2.75), (0.0, 0.75, 2.5, 3.5)),
    ((0.0, 1.75, 2.5), (0.0, 1.25, 2.75, 3.5)),
    ((0.0, 0.75, 2.25), (0.0, 1.5, 2.75, 3.25)),
)
_MUTATION_BASS_RHYTHMS = {
    "sparse": ((0, 6, 13), (0, 7, 14)),
    "normal": ((0, 3, 6, 10, 13), (0, 2, 7, 10, 14), (0, 3, 6, 9, 13)),
    "dense": ((0, 3, 6, 9, 10, 13, 15), (0, 2, 3, 7, 10, 13, 14)),
}
# Two empty comping bars per eight-bar Drop phrase leave room for the bass.
# Which answer is silent changes with the seed, while the phrase keeps its anchor.
_MUTATION_STAB_RESTS = ((2, 6), (3, 7), (1, 5))
# Mostly the chord root, sometimes its octave, fifth or seventh.
_BASS_MOTIF_POOL = (0, 0, 0, 0, 12, 7, 10)
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
    minor = is_minor(str(brief.key.value)) or str(brief.mood.value) in {
        "暗い",
        "ダーク",
    }
    progression = rng.choice(
        _MUTATION_PROGRESSIONS
        if str(brief.genre.value) == "mutation_funk" and minor
        else _MINOR_PROGRESSIONS
        if minor
        else _MAJOR_PROGRESSIONS
    )
    beats = brief.beats_per_bar
    bars = int(brief.bars.value)
    clips: list[CandidateClip] = []
    # Generation order fixes each part's random draws: new parts come last so
    # the original four keep the notes they always had.
    parts = (
        (*STUDIO_PARTS, "Lead") if str(brief.genre.value) == "mutation_funk" else STUDIO_PARTS
    )
    song: dict[str, list[MidiNote]] = {}
    for part in (*parts, *_GENERATED_EXTRAS):
        # Generated even when left out, so the parts after it keep their draws.
        notes = _notes_for_part(part, brief, bars, beats, rng, progression)
        if _genre_plays(part, brief):
            song[part] = notes
    song.update(_split_drums(song.pop("Hats")))
    song["Sub"] = _sub_notes(song["Bass"], brief, beats)
    for part in PART_ORDER:
        song_notes = song.get(part)
        if song_notes is None:
            continue
        sparse = part in ARRANGEMENT_PARTS
        for section in brief.sections:
            for start, length in _clip_windows(
                section.start_bar, section.length_bars, song_notes, beats
            ):
                notes = _clip_notes(song_notes, start, length, beats)
                if sparse and not notes:
                    continue  # every clip is Live operations; skip silent ones
                clips.append(
                    CandidateClip(
                        part=part,
                        section_name=section.name,
                        start_bar=start,
                        length_bars=length,
                        notes=notes,
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


def _genre_plays(part: str, brief: ProductionBrief) -> bool:
    """Whether the genre's family plays this part, or the brief asks for it."""
    if part not in _FAMILY_PARTS:
        return True
    genre = find_genre(str(brief.genre.value))
    family = genre.family if genre is not None else ""
    if family in _BAND_FAMILIES or family in _FAMILY_PARTS[part]:
        return True
    text = brief.original_text.lower()
    return any(word in text for word in _PART_WORDS[part])


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
    progression: tuple[int, ...],
) -> list[MidiNote]:
    # One generator per part, seeded from the shared one, so a change inside
    # one part does not reshuffle the random choices of the others.
    part_rng = Random(rng.random())
    plan = _SongPlan(brief, bars, beats, part_rng, progression)
    if part == "Kick":
        notes = _kick_notes(plan)
    elif part == "Hats":
        notes = _hat_notes(plan)
    elif part == "Bass":
        notes = _bass_notes(plan)
    elif part == "Stab":
        notes = _stab_notes(plan)
    elif part == "Perc":
        notes = _perc_notes(plan)
    elif part == "Pad":
        notes = _pad_notes(plan)
    elif part == "Arp":
        notes = _arp_notes(plan)
    elif part == "Vocal":
        notes = _vocal_notes(plan)
    elif part == "FX":
        notes = _fx_notes(plan)
    elif part == "Guitar":
        notes = _guitar_notes(plan)
    elif part == "Horn":
        notes = _horn_notes(plan)
    else:
        notes = _lead_notes(plan)
    return [_inside_bar(note, beats) for note in notes]


def _split_drums(hats: list[MidiNote]) -> dict[str, list[MidiNote]]:
    """Give the backbeat and the open hat their own tracks, notes unchanged."""
    return {
        "Hats": [
            note
            for note in hats
            if note.pitch not in SNARE_PITCHES and note.pitch != OPEN_HAT_PITCH
        ],
        "Snare": [note for note in hats if note.pitch in SNARE_PITCHES],
        "OpenHat": [note for note in hats if note.pitch == OPEN_HAT_PITCH],
    }


def _sub_notes(
    bass: list[MidiNote], brief: ProductionBrief, beats: float
) -> list[MidiNote]:
    """The bass rhythm an octave or two down, one pitch class per note.

    Ghost notes stay out, and the sub drops out of the breakdown and the Break
    so the Drop arrives with the low end.
    """
    quiet_bars = set(_breakdown_bars(brief))
    for section in brief.sections:
        if section.name == "Break":
            quiet_bars.update(range(section.start_bar, section.end_bar + 1))
    notes: list[MidiNote] = []
    for note in bass:
        if note.velocity < 60 or int(note.start_beats // beats) + 1 in quiet_bars:
            continue
        pitch = SUB_LOWEST_PITCH + (note.pitch - SUB_LOWEST_PITCH) % 12
        notes.append(
            MidiNote(pitch, note.start_beats, note.duration_beats, min(110, note.velocity + 6))
        )
    return notes


def _inside_bar(note: MidiNote, beats: float) -> MidiNote:
    """Shorten a note that would ring past its bar, where a clip may end.

    Swing moves late offbeats later: a blues shuffle puts the last sixteenth at
    3.83, and a 0.2-beat note there would cross into the next clip.
    """
    bar_end = (note.start_beats // beats + 1) * beats
    room = round(bar_end - note.start_beats - 0.01, 6)
    if note.duration_beats <= room:
        return note
    return MidiNote(note.pitch, note.start_beats, max(0.01, room), note.velocity)


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
        self.long_phrase_end = (
            self.in_section % (PHRASE_BARS * 2) == PHRASE_BARS * 2 - 1
        )
        self.breakdown = bar in plan.breakdown
        self.breakdown_left = plan.breakdown.stop - 1 - bar if self.breakdown else -1


class _SongPlan:
    """Choices shared by every bar of one part: patterns, progression, accents."""

    def __init__(
        self,
        brief: ProductionBrief,
        bars: int,
        beats: float,
        rng: Random,
        progression: tuple[int, ...],
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
        self.progression = progression
        self.break_progression = _BREAK_MINOR if self.minor else _BREAK_MAJOR
        self.breakdown = _breakdown_bars(brief)
        self._sections = tuple(brief.sections)
        self._first_drop = next(
            (section.start_bar for section in self._sections if section.name == "Drop"),
            0,
        )
        # What the genre's family plays. An unknown genre gets four on the
        # floor with offbeat stabs, which is what every genre used to get.
        self.profile = profile_for(str(brief.genre.value))
        self.pattern = drum_pattern(self.profile.drum_pattern)
        self.articulation = chord_articulation(self.profile.articulation)
        self.bass_role = bass_role(self.profile.bass_role)
        self.hat_density = (
            self.profile.hat_density if self.profile.hat_density is not None else 0.85
        )
        self.harmonic_rhythm = self.profile.harmonic_rhythm_bars or 2
        stated_swing = explicit_swing(brief.original_text)
        self.swing = (
            stated_swing if stated_swing is not None else (self.profile.swing or 0.5)
        )
        self.echo_requested = bool(read_explicit(brief.original_text)[0].get("echo"))
        # A recipe that puts a real Echo on the chords does the repeats; writing
        # them as notes as well doubles the delay.
        recipe = recipe_for(str(brief.genre.value))
        self.device_echo = recipe is not None and recipe.has_effect("Stab", "Echo")

    def section_of(self, bar: int) -> Any:
        for section in self._sections:
            if section.start_bar <= bar <= section.end_bar:
                return section
        return None

    def bar(self, number: int) -> _Bar:
        return _Bar(self, number)

    def second_drop(self, bar: _Bar) -> bool:
        """A Drop after the Break: the return, which should give a little more."""
        section = self.section_of(bar.number)
        return (
            bar.section == "Drop"
            and section is not None
            and section.start_bar > self._first_drop
        )

    def energy(self, bar: _Bar) -> float:
        """How much of each pattern plays: low in the Intro, full in the Drop."""
        section = self.section_of(bar.number)
        length = section.length_bars if section else self.bars
        progress = bar.in_section / max(1, length - 1)
        if bar.section == "Intro":
            return 0.3 + 0.3 * progress
        if bar.section == "Build":
            return 0.6 + 0.25 * progress
        if bar.section == "VerseA":
            return 0.55 + 0.1 * progress
        if bar.section == "VerseB":
            return 0.7 + 0.12 * progress
        if bar.section == "Break":
            return 0.2 + 0.5 * progress
        if bar.section == "ChorusA":
            return 0.92
        if bar.section == "ChorusB":
            return 1.0
        if bar.section == "Outro":
            return 0.75 - 0.55 * progress
        return 1.0

    def steps_for(self, bounds: tuple[int, int], bar: _Bar) -> int:
        low, high = bounds
        return low + round(self.energy(bar) * (high - low))

    def at(self, bar: _Bar, offset: float) -> float:
        """An absolute beat for an offset in the bar, swung if the genre swings."""
        return round(bar.start + swung(offset, self.swing), 6)

    def chord_degree(self, bar: _Bar) -> int:
        """Return the scale degree the harmony sits on in this bar."""
        if bar.section in {"Intro", "Outro"} and not bar.breakdown:
            return 0
        if self.profile.drum_pattern == "mutation_funk":
            if bar.section == "VerseA":
                return (0, 5)[(bar.in_section // 4) % 2]
            if bar.section == "VerseB":
                return (3, 4)[(bar.in_section // 4) % 2]
            if bar.section == "Break":
                return 4 if bar.left_in_section < 2 else 0
        if bar.section == "Break":
            # Away from the loop the Drops play, and back towards its tonic.
            lift = self.break_progression
            length = bar.in_section + bar.left_in_section + 1
            bars_per_chord = max(1, length // len(lift))
            return lift[len(lift) - 1 - (bar.left_in_section // bars_per_chord) % len(lift)]
        in_drop = bar.section in {"Drop", "ChorusA", "ChorusB"} and not bar.breakdown
        bars_per_chord = self.harmonic_rhythm * (1 if in_drop else 2)
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
    build = next(
        (section for section in brief.sections if section.name == "Build"), None
    )
    if build is None or build.length_bars < 4:
        return range(0)
    length = min(PHRASE_BARS, build.length_bars // 2)
    return range(build.end_bar - length + 1, build.end_bar + 1)


def _kick_notes(plan: _SongPlan) -> list[MidiNote]:
    notes: list[MidiNote] = []
    fill = plan.rng.choice(_KICK_FILLS)
    mutation_phrases: dict[tuple[str, int], tuple[tuple[float, ...], ...]] = {}
    for number in range(1, plan.bars + 1):
        bar = plan.bar(number)
        if bar.breakdown:
            continue
        if bar.section == "Break" and bar.in_section < max(
            1, plan.section_of(number).length_bars - 2
        ):
            continue
        if bar.section == "Outro" and bar.left_in_section == 0:
            continue  # leave the last bar open for whatever comes next
        half_time = (
            bar.section == "Intro" and plan.density == "sparse" and bar.phrase == 0
        )
        if plan.profile.drum_pattern == "mutation_funk":
            phrase_key = (bar.section, bar.phrase)
            if phrase_key not in mutation_phrases:
                mutation_phrases[phrase_key] = plan.rng.choice(_MUTATION_KICK_GROOVES)
            slots = mutation_phrases[phrase_key][bar.in_section % 2]
            if bar.section in {"Intro", "Outro"}:
                slots = slots[:2]
        else:
            slots = plan.pattern.kick_positions[
                : plan.steps_for(plan.pattern.kick_steps, bar)
            ]
        if half_time:
            slots = slots[::2]
        positions = [
            (slot, 118 if index == 0 else 110)
            for index, slot in enumerate(slots)
            if slot < plan.beats
        ]
        next_is_breakdown = number + 1 in plan.breakdown
        if bar.long_phrase_end and not next_is_breakdown and bar.left_in_section > 0:
            positions = [item for item in positions if item[0] < plan.beats - 1]
            positions.extend((plan.beats - 1 + offset, 96) for offset in fill)
        boost = 4 if bar.section in {"Drop", "ChorusA", "ChorusB"} else 0
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
        if bar.section == "Break":
            if bar.left_in_section < 2:
                notes.extend(_hat_roll(plan, bar))
            continue
        if bar.section == "Intro" and bar.phrase == 0:
            continue  # kick alone first, so the hats have something to arrive on
        if bar.section == "Outro" and bar.left_in_section < PHRASE_BARS // 2:
            density = "sparse"
        open_hats = plan.pattern.offbeat_open_hat and (
            (density != "sparse" and bar.section in {"Build", "Drop"})
            or (bar.section == "Outro" and bar.in_section < PHRASE_BARS)
        )
        key = (bar.section, bar.phrase)
        if key not in variations:
            variations[key] = plan.rng.choice(_HAT_VARIATIONS)
        # Every second bar answers the first with a small change.
        variation = variations[key] if bar.in_section % 2 == 1 else "plain"
        skipped = plan.beats - 1.25 if variation == "skip" else -1.0
        for position in _hat_grid(plan, density):
            step = round(position * 4)
            if abs(position - skipped) < 1e-6:
                continue
            if open_hats and step % 4 == 2:
                notes.append(
                    MidiNote(
                        OPEN_HAT_PITCH, plan.at(bar, position), 0.25, plan.velocity(84)
                    )
                )
                continue
            notes.append(
                MidiNote(
                    plan.pattern.hat_pitch,
                    plan.at(bar, position),
                    0.1,
                    plan.velocity(accents[step % len(accents)]),
                )
            )
        # A straight thirty-second roll would fight a swung ride.
        if bar.phrase_end and bar.left_in_section > 0 and plan.swing == 0.5:
            notes.extend(_hat_roll(plan, bar))
        elif (
            variation == "ghost_clap"
            and plan.pattern.backbeat_positions
            and _clap_plays(plan, bar)
        ):
            notes.append(
                MidiNote(
                    _backbeat_pitch(plan),
                    plan.at(bar, plan.beats - 0.25),
                    0.2,
                    plan.velocity(64),
                )
            )
        elif variation == "open_push" and open_hats:
            notes.append(
                MidiNote(
                    OPEN_HAT_PITCH,
                    round(bar.start + plan.beats - 0.25, 6),
                    0.25,
                    plan.velocity(70),
                )
            )
        if _clap_plays(plan, bar):
            for slot in plan.pattern.backbeat_positions:
                if slot < plan.beats:
                    notes.append(
                        MidiNote(
                            plan.pattern.backbeat_pitch,
                            plan.at(bar, slot),
                            0.25,
                            plan.velocity(100),
                        )
                    )
            if plan.profile.drum_pattern == "mutation_funk" and bar.section in {
                "Build",
                "Drop",
                "VerseB",
                "ChorusA",
                "ChorusB",
            }:
                ghost_slot = 0.75 if bar.in_section % 2 == 0 else 2.75
                notes.append(
                    MidiNote(37, plan.at(bar, ghost_slot), 0.1, plan.velocity(43, 3))
                )
    return notes


#: How the brief's word for hat density scales the family's own density.
_HAT_DENSITY_SCALE = {"sparse": 0.35, "normal": 1.0, "dense": 1.0}


def _hat_grid(plan: _SongPlan, density: str) -> tuple[float, ...]:
    """The family's hat grid, thinned for a sparse brief, split for a dense one."""
    pattern = plan.pattern
    if density == "dense" and pattern.hat_step > 0.25:
        pattern = replace(pattern, hat_step=0.25)
    level = plan.hat_density * _HAT_DENSITY_SCALE[density]
    return hat_positions(pattern, level, plan.beats)


def _backbeat_pitch(plan: _SongPlan) -> int:
    """The family's backbeat sound, or the clap when the groove has none."""
    return (
        plan.pattern.backbeat_pitch if plan.pattern.backbeat_positions else CLAP_PITCH
    )


def _hat_roll(plan: _SongPlan, bar: _Bar) -> list[MidiNote]:
    """Sixteenths across the last beat of a phrase, rising into the next one."""
    last = plan.beats - 1
    return [
        MidiNote(
            plan.pattern.hat_pitch,
            round(bar.start + last + index / 4 + 0.125, 6),
            0.08,
            56 + 12 * index,
        )
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
    # A groove with no backbeat (ambient, samba) builds with hats alone.
    if bar.breakdown_left <= 1 and plan.pattern.backbeat_positions:
        spacing = 0.5 if bar.breakdown_left == 1 else 0.25
        count = int(plan.beats / spacing)
        for index in range(count):
            notes.append(
                MidiNote(
                    _backbeat_pitch(plan),
                    round(bar.start + index * spacing, 6),
                    0.2,
                    max(1, min(127, int(60 + 60 * (index + 1) / count))),
                )
            )
    return notes


def _clap_plays(plan: _SongPlan, bar: _Bar) -> bool:
    if bar.section == "Intro":
        return bar.left_in_section < PHRASE_BARS
    if bar.section == "VerseA":
        return bar.in_section % 2 == 0
    if bar.section == "Outro":
        return bar.in_section < PHRASE_BARS
    return True


def _bass_notes(plan: _SongPlan) -> list[MidiNote]:
    octave = {"low": -1, "mid": 0, "high": 1}[str(plan.brief.bass_register.value)]
    root = root_pitch(str(plan.brief.key.value), octave_offset=octave)
    mutation = plan.profile.drum_pattern == "mutation_funk"
    choices = _BASS_RHYTHMS[plan.density]
    notes: list[MidiNote] = []
    rhythms: dict[tuple[str, int], tuple[int, ...]] = {}
    mutation_rhythms: dict[tuple[str, int], tuple[tuple[int, ...], ...]] = {}
    motifs: dict[tuple[str, int], tuple[int, ...]] = {}
    for number in range(1, plan.bars + 1):
        bar = plan.bar(number)
        chord_root = root + plan.nearest_degree_offset(plan.chord_degree(bar))
        if bar.breakdown:
            notes.append(MidiNote(chord_root, bar.start, plan.beats * 0.95, 92))
            continue
        if bar.section == "Break":
            if bar.in_section in {0, max(0, plan.section_of(number).length_bars - 2)}:
                notes.append(MidiNote(chord_root, bar.start, plan.beats * 0.9, 83))
            continue
        if bar.section == "Intro" and bar.in_section < _intro_bass_entry(bar, plan):
            continue
        if bar.section == "Outro" and bar.left_in_section < PHRASE_BARS:
            continue
        key = (bar.section, bar.phrase)
        if key not in motifs:
            if mutation:
                mutation_rhythms[key] = tuple(
                    plan.rng.sample(_MUTATION_BASS_RHYTHMS[plan.density], 2)
                )
            else:
                name = "offbeat" if bar.section == "Intro" else plan.rng.choice(choices)
                rhythms[key] = _BASS_PATTERNS[name]
            motifs[key] = tuple(plan.rng.choice(_BASS_MOTIF_POOL) for _ in range(4))
        pattern_steps = (
            mutation_rhythms[key][bar.in_section % 2] if mutation else rhythms[key]
        )
        steps = _role_steps(
            [step for step in pattern_steps if step < plan.steps],
            plan.bass_role.density_scale,
        )
        if mutation and bar.section == "Intro":
            steps = steps[:2]
        motif = motifs[key]
        length = 0.2 if len(steps) > 6 else 0.4
        base = plan.bass_role.velocity
        for index, step in enumerate(steps):
            interval = motif[index % len(motif)]
            if bar.phrase_end and index >= len(steps) - 2:
                interval = 12  # lift at the end of a phrase
            if interval == 10 and not plan.minor:
                interval = 7  # a major seventh in the bass sounds wrong here
            ghost = mutation and step % 4 == 3
            notes.append(
                MidiNote(
                    chord_root + interval,
                    plan.at(bar, step / 4),
                    0.12 if ghost else length,
                    plan.velocity(
                        48 if ghost else base + 2 if step % 4 == 2 else base - 6, 4
                    ),
                )
            )
    return notes


def _role_steps(steps: list[int], scale: float) -> list[int]:
    """A supporting bass keeps its offbeats and drops the in-between notes."""
    keep = max(1, round(len(steps) * scale))
    if keep >= len(steps):
        return steps
    ranked = sorted(steps, key=lambda step: (step % 4 != 2, step % 2 != 0, step))
    kept = set(ranked[:keep])
    return [step for step in steps if step in kept]


def _intro_bass_entry(bar: _Bar, plan: _SongPlan) -> int:
    """The bass waits for the second half of the Intro."""
    section = plan.section_of(bar.number)
    length = section.length_bars if section else plan.bars
    return length // 2


def _stab_notes(plan: _SongPlan) -> list[MidiNote]:
    key = str(plan.brief.key.value)
    dark = str(plan.brief.mood.value) in {"暗い", "ダーク"}
    tonic = root_pitch(key, octave_offset=1 if dark else 2)
    articulation = plan.articulation
    extra = 3.75 if plan.density == "dense" else None
    notes: list[MidiNote] = []
    rhythms: dict[tuple[str, int], tuple[float, ...]] = {}
    mutation_rests: dict[tuple[str, int], tuple[int, int]] = {}
    for number in range(1, plan.bars + 1):
        bar = plan.bar(number)
        chord = _voiced_chord(plan, tonic, plan.chord_degree(bar))
        if bar.breakdown:
            # Held chords, one per bar: a note may not cross a clip boundary.
            notes.extend(_chord_notes(chord, bar.start, plan.beats * 0.95, 70))
            continue
        if bar.section == "Break":
            if bar.in_section == 0 or bar.left_in_section == 1:
                notes.extend(_chord_notes(chord, bar.start, plan.beats * 0.7, 65))
            continue
        first = articulation.positions[0]
        if bar.section == "Intro":
            if bar.left_in_section < PHRASE_BARS and bar.in_section % 2 == 0:
                notes.extend(_stab(plan, bar, chord, first, articulation.duration, 66))
            continue
        if bar.section == "Outro":
            if bar.in_section < PHRASE_BARS:
                notes.extend(_stab(plan, bar, chord, first, articulation.duration, 70))
            continue
        rhythm_key = (bar.section, bar.phrase)
        if plan.profile.drum_pattern == "mutation_funk" and bar.section in {
            "Drop",
            "ChorusA",
            "ChorusB",
        }:
            if rhythm_key not in mutation_rests:
                mutation_rests[rhythm_key] = plan.rng.choice(_MUTATION_STAB_RESTS)
            if bar.in_section % PHRASE_BARS in mutation_rests[rhythm_key]:
                continue
        if rhythm_key not in rhythms:
            # The family's slots in groove order: the essential ones always,
            # and a different choice of the optional ones each phrase.
            low, high = articulation.steps
            count = plan.steps_for(articulation.steps, bar)
            optional = list(articulation.positions[low:high])
            chosen = plan.rng.sample(optional, min(len(optional), count - low))
            rhythms[rhythm_key] = tuple(articulation.positions[:low]) + tuple(chosen)
        hits = [hit for hit in rhythms[rhythm_key] if hit < plan.beats]
        if bar.section == "VerseA":
            hits = hits[:1]
        elif bar.section == "VerseB":
            hits = hits[:2]
        elif bar.section == "ChorusB" and plan.beats - 0.5 not in hits:
            hits.append(plan.beats - 0.5)
        if bar.section == "Build" and bar.phrase == 0:
            hits = hits[:1]
        short = articulation.duration < 0.5
        if (
            short
            and extra is not None
            and bar.section in {"Drop", "ChorusB"}
            and extra < plan.beats
        ):
            hits.append(extra)
        if short and bar.phrase_end and plan.beats - 0.75 not in hits:
            hits.append(plan.beats - 0.75)
        velocity = round(88 * articulation.velocity_scale)
        hits = sorted(set(hits))
        for hit in hits:
            notes.extend(_stab(plan, bar, chord, hit, articulation.duration, velocity))
        if _echoes(plan, bar):
            notes.extend(_echo(plan, bar, chord, hits, articulation.duration, velocity))
    return notes


def _lead_notes(plan: _SongPlan) -> list[MidiNote]:
    """A chord-tone hook with short scale passing notes in the upper register."""
    tonic = root_pitch(str(plan.brief.key.value), octave_offset=3)
    motifs = ((0, 2, 4, 2), (2, 4, 2, 0), (4, 2, 0, 2))
    phrase_motifs: dict[tuple[str, int], tuple[int, ...]] = {}
    notes: list[MidiNote] = []
    for number in range(1, plan.bars + 1):
        bar = plan.bar(number)
        if bar.breakdown or bar.section == "Intro":
            continue
        if bar.section == "Break":
            if bar.left_in_section >= 2:
                continue
            offsets = (2.0, 2.75, 3.5)
        elif bar.section == "VerseA":
            if bar.in_section < 4 or bar.in_section % 2:
                continue
            offsets = (1.5, 3.0)
        elif bar.section == "VerseB":
            offsets = (0.5, 2.0, 3.25) if bar.in_section % 2 == 0 else (1.5, 3.0)
        elif bar.section in {"ChorusA", "ChorusB"}:
            offsets = (0.5, 1.5, 2.5, 3.5)
        elif bar.section == "Outro":
            if bar.in_section % 2:
                continue
            offsets = (1.5, 3.0)
        else:
            offsets = (0.5, 2.0, 3.5)
        key = (bar.section, bar.phrase)
        if key not in phrase_motifs:
            phrase_motifs[key] = plan.rng.choice(motifs)
        motif = phrase_motifs[key]
        chord_degree = plan.chord_degree(bar)
        for index, offset in enumerate(offsets):
            if offset >= plan.beats:
                continue
            degree = chord_degree + motif[index % len(motif)]
            # The last pickup is a quiet diatonic neighbour, resolving on the
            # next bar's chord tone rather than a random out-of-key pitch.
            passing = index == len(offsets) - 1 and bar.in_section % 2 == 1
            if passing:
                degree += 1
            pitch = tonic + plan.degree_offset(degree)
            if pitch > 96:
                pitch -= 12
            notes.append(
                MidiNote(
                    pitch,
                    plan.at(bar, offset),
                    0.18 if passing else 0.32,
                    plan.velocity(69 if passing else 88, 4),
                )
            )
    return notes


_PEAK_SECTIONS = frozenset({"Drop", "ChorusA", "ChorusB"})
# Conga answers between the kicks, one figure per phrase.
_CONGA_FIGURES = (
    (0.75, 1.5, 2.25, 3.5),
    (0.5, 1.75, 2.5, 3.25),
    (0.75, 2.0, 2.75, 3.5),
)


def _perc_notes(plan: _SongPlan) -> list[MidiNote]:
    """A shaker that fills in with the energy, and congas at the peaks."""
    notes: list[MidiNote] = []
    figures: dict[tuple[str, int], tuple[float, ...]] = {}
    for number in range(1, plan.bars + 1):
        bar = plan.bar(number)
        if bar.breakdown or bar.section in {"Intro", "Break"}:
            continue
        if bar.section == "VerseA" and bar.in_section < 4:
            continue
        if bar.section == "Outro" and bar.in_section >= PHRASE_BARS:
            continue
        sixteenths = plan.energy(bar) >= 0.8
        for step in range(plan.steps):
            offbeat = step % 4 == 2
            if not sixteenths and step % 2:
                continue
            notes.append(
                MidiNote(
                    SHAKER_NOTE,
                    plan.at(bar, step / 4),
                    0.1,
                    plan.velocity(74 if offbeat else 46, 4),
                )
            )
        if bar.section in _PEAK_SECTIONS or (
            bar.section == "VerseB" and bar.in_section % 2 == 1
        ):
            key = (bar.section, bar.phrase)
            if key not in figures:
                figures[key] = plan.rng.choice(_CONGA_FIGURES)
            for index, offset in enumerate(figures[key]):
                if offset >= plan.beats:
                    continue
                notes.append(
                    MidiNote(
                        CONGA_HIGH_NOTE if index % 2 == 0 else CONGA_LOW_NOTE,
                        plan.at(bar, offset),
                        0.2,
                        plan.velocity(82 if index % 2 == 0 else 72, 5),
                    )
                )
    return notes


def _pad_notes(plan: _SongPlan) -> list[MidiNote]:
    """Held four-note chords under everything, loudest where the song thins out."""
    tonic = root_pitch(str(plan.brief.key.value), octave_offset=1)
    notes: list[MidiNote] = []
    for number in range(1, plan.bars + 1):
        bar = plan.bar(number)
        section = plan.section_of(number)
        length = section.length_bars if section else plan.bars
        if bar.section == "Intro" and bar.in_section < length // 2:
            continue
        if bar.section == "Outro" and bar.left_in_section < PHRASE_BARS // 2:
            continue
        if bar.breakdown:
            velocity = 58 + 2 * (len(plan.breakdown) - 1 - bar.breakdown_left)
        elif bar.section == "Break":
            velocity = 62
        elif bar.section in _PEAK_SECTIONS:
            velocity = 46
        else:
            velocity = 52
        chord = _pad_chord(plan, tonic, plan.chord_degree(bar))
        notes.extend(_chord_notes(chord, bar.start, plan.beats - 0.02, min(127, velocity)))
    return notes


def _pad_chord(plan: _SongPlan, tonic: int, degree: int) -> tuple[int, ...]:
    """The triad of _voiced_chord with its seventh, kept within an octave and a half."""
    triad = _voiced_chord(plan, tonic, degree)
    seventh = tonic + plan.degree_offset(degree + 6)
    while seventh <= triad[-1]:
        seventh += 12
    if seventh - triad[0] > 18:
        seventh -= 12
    return tuple(sorted({*triad, seventh}))


# Indexes into root, third, fifth, octave. One order per phrase.
_ARP_ORDERS = ((0, 1, 2, 3), (0, 2, 1, 3), (3, 2, 1, 0), (0, 1, 2, 3, 2, 1))


def _arp_notes(plan: _SongPlan) -> list[MidiNote]:
    """Chord tones in sixteenths at the peaks, eighths while the verse builds."""
    tonic = root_pitch(str(plan.brief.key.value), octave_offset=2)
    notes: list[MidiNote] = []
    orders: dict[tuple[str, int], tuple[int, ...]] = {}
    for number in range(1, plan.bars + 1):
        bar = plan.bar(number)
        rising = 0.0
        if bar.breakdown:
            position_in = len(plan.breakdown) - 1 - bar.breakdown_left
            if position_in < len(plan.breakdown) // 2:
                continue
            step, rising = 0.25, position_in / max(1, len(plan.breakdown) - 1)
        elif bar.section in _PEAK_SECTIONS or bar.section == "Build":
            step = 0.25
        elif bar.section == "VerseB" or (bar.section == "Break" and bar.left_in_section < 2):
            step = 0.5
        else:
            continue
        key = (bar.section, bar.phrase)
        if key not in orders:
            orders[key] = plan.rng.choice(_ARP_ORDERS)
        order = orders[key]
        degree = plan.chord_degree(bar)
        lift = 12 if plan.second_drop(bar) else 0
        tones = [tonic + lift + plan.degree_offset(degree + k) for k in (0, 2, 4, 7)]
        count = int(plan.beats / step)
        for index in range(count):
            base = 84 if index % 4 == 0 else 60
            if rising:
                base = round(base * (0.6 + 0.4 * rising))
            notes.append(
                MidiNote(
                    tones[order[index % len(order)]],
                    plan.at(bar, index * step),
                    round(step * 0.6, 6),
                    plan.velocity(base, 4),
                )
            )
    return notes


# Short chopped syllables, placed like a vocal hook around the backbeat.
_CHOP_RHYTHMS = (
    (0.0, 0.5, 0.75, 1.5, 2.5, 3.0),
    (0.0, 0.25, 1.0, 1.75, 2.5, 3.25),
    (0.5, 1.0, 1.5, 2.75, 3.5),
)
_CHOP_MOTIFS = ((0, 2, 4, 2, 0, 4), (4, 2, 0, 2, 4, 7), (0, 0, 2, 4, 2, 2))


def _vocal_notes(plan: _SongPlan) -> list[MidiNote]:
    """A chopped hook on chord tones, for a vocal sampler or a vocoder carrier."""
    tonic = root_pitch(str(plan.brief.key.value), octave_offset=2)
    notes: list[MidiNote] = []
    phrases: dict[tuple[str, int], tuple[tuple[float, ...], tuple[int, ...]]] = {}
    for number in range(1, plan.bars + 1):
        bar = plan.bar(number)
        if bar.breakdown:
            continue
        if bar.section in _PEAK_SECTIONS:
            limit = None
        elif bar.section == "VerseB" and bar.in_section % 2 == 1:
            limit = 3
        elif bar.section == "Break" and bar.left_in_section == 0:
            limit = 2
        else:
            continue
        key = (bar.section, bar.phrase)
        if key not in phrases:
            phrases[key] = (plan.rng.choice(_CHOP_RHYTHMS), plan.rng.choice(_CHOP_MOTIFS))
        rhythm, motif = phrases[key]
        degree = plan.chord_degree(bar)
        for index, offset in enumerate(rhythm[:limit]):
            if offset >= plan.beats:
                continue
            notes.append(
                MidiNote(
                    tonic + plan.degree_offset(degree + motif[index % len(motif)]),
                    plan.at(bar, offset),
                    0.2,
                    plan.velocity(90 if index == 0 else 76, 4),
                )
            )
    return notes


def _fx_notes(plan: _SongPlan) -> list[MidiNote]:
    """A riser that ends where each peak section starts, and an impact on it."""
    notes: list[MidiNote] = []
    riser_beats = RISER_SECONDS * int(plan.brief.tempo.value) / 60
    for section in plan.brief.sections:
        if section.name not in _PEAK_SECTIONS or section.start_bar == 1:
            continue
        target = (section.start_bar - 1) * plan.beats
        start = max(0.0, round((target - riser_beats) * 4) / 4)
        notes.append(MidiNote(RISER_NOTE, round(start, 6), round(target - start, 6), 100))
        notes.append(MidiNote(IMPACT_NOTE, round(target, 6), 1.0, plan.velocity(112, 3)))
    return notes


# Sixteenth steps of a funk cutting pattern: accented chops, the rest muted.
_GUITAR_CHOPS = ((2, 6, 10, 14), (2, 7, 10, 15), (3, 6, 11, 14))


def _guitar_notes(plan: _SongPlan) -> list[MidiNote]:
    """Funk cutting: muted sixteenth strums with open chord chops on the offbeats."""
    tonic = root_pitch(str(plan.brief.key.value), octave_offset=1)
    notes: list[MidiNote] = []
    chops: dict[tuple[str, int], tuple[int, ...]] = {}
    for number in range(1, plan.bars + 1):
        bar = plan.bar(number)
        if bar.breakdown or bar.section not in {*_PEAK_SECTIONS, "VerseB", "Outro"}:
            continue
        if bar.section == "Outro" and bar.in_section >= PHRASE_BARS:
            continue
        key = (bar.section, bar.phrase)
        if key not in chops:
            chops[key] = plan.rng.choice(_GUITAR_CHOPS)
        chord = _voiced_chord(plan, tonic + 12, plan.chord_degree(bar))
        muted = bar.section in _PEAK_SECTIONS
        for step in range(plan.steps):
            if step in chops[key]:
                notes.extend(
                    _chord_notes(chord, plan.at(bar, step / 4), 0.15, plan.velocity(86, 4))
                )
            elif muted and step % 4 == 1:
                # A muted strum reads as the top string, barely pitched.
                notes.append(
                    MidiNote(chord[-1], plan.at(bar, step / 4), 0.06, plan.velocity(42, 4))
                )
    return notes


# Two-hit brass figures, and the phrase-end fall into the next bar.
_HORN_FIGURES = ((1.5, 1.75), (0.5, 2.5), (2.75, 3.0), (1.0, 3.5))


def _horn_notes(plan: _SongPlan) -> list[MidiNote]:
    """Short brass stabs at the peaks, answering the chords rather than doubling them."""
    tonic = root_pitch(str(plan.brief.key.value), octave_offset=2)
    notes: list[MidiNote] = []
    figures: dict[tuple[str, int], tuple[float, ...]] = {}
    for number in range(1, plan.bars + 1):
        bar = plan.bar(number)
        if bar.breakdown:
            continue
        if bar.section in _PEAK_SECTIONS:
            if bar.in_section % 2 == 1 and not bar.phrase_end:
                continue  # every other bar, so the figure is a call, not a loop
        elif not (bar.section == "VerseB" and bar.phrase_end):
            continue
        key = (bar.section, bar.phrase)
        if key not in figures:
            figures[key] = plan.rng.choice(_HORN_FIGURES)
        chord = _voiced_chord(plan, tonic, plan.chord_degree(bar))
        hits = [hit for hit in figures[key] if hit < plan.beats]
        if bar.phrase_end:
            hits = [hit for hit in hits if hit < plan.beats - 1] + [plan.beats - 0.75]
        for index, hit in enumerate(hits):
            last = index == len(hits) - 1
            notes.extend(
                _chord_notes(
                    chord,
                    plan.at(bar, hit),
                    0.35 if last else 0.18,
                    plan.velocity(96 if last else 88, 4),
                )
            )
    return notes


def _stab(
    plan: _SongPlan,
    bar: _Bar,
    chord: tuple[int, ...],
    offset: float,
    length: float,
    velocity: int,
) -> list[MidiNote]:
    """One chord, held for the articulation's length but never past the bar."""
    start = swung(offset, plan.swing)
    held = min(length, plan.beats - start - 0.01)
    return _chord_notes(chord, bar.start + start, held, plan.velocity(velocity))


#: A dotted eighth, the classic dub delay time, and how much each repeat keeps.
_ECHO_BEATS = 0.75
_ECHO_LEVELS = (0.55, 0.3)


def _echoes(plan: _SongPlan, bar: _Bar) -> bool:
    """Dub chords always echo; a brief asking for delay gets it here and there."""
    if bar.section not in {"Build", "Drop"} or plan.device_echo:
        return False
    if plan.articulation.echo:
        return True
    # 「ところどころ」: the second half of every other eight-bar phrase.
    return (
        plan.echo_requested
        and bar.phrase % 2 == 1
        and bar.in_section % PHRASE_BARS >= 4
    )


def _echo(
    plan: _SongPlan,
    bar: _Bar,
    chord: tuple[int, ...],
    hits: list[float],
    length: float,
    velocity: int,
) -> list[MidiNote]:
    """Quieter repeats of each hit at a dotted eighth: a delay, written as MIDI."""
    notes: list[MidiNote] = []
    taken = set(hits)
    duration = min(length, _ECHO_BEATS - 0.05)
    for hit in hits:
        for index, level in enumerate(_ECHO_LEVELS, start=1):
            offset = round(hit + _ECHO_BEATS * index, 6)
            if offset + duration > plan.beats or offset in taken:
                break
            taken.add(offset)
            notes.extend(
                _chord_notes(
                    chord, bar.start + offset, duration, max(1, round(velocity * level))
                )
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
    clips = [*candidate.clips_for_part("Hats"), *candidate.clips_for_part("OpenHat")]
    for clip in clips:
        for note in clip.notes:
            if note.pitch not in HAT_PITCHES:
                continue  # a candidate saved before the split kept the clap here
            absolute_bar = clip.start_bar + note.start_beats / beats
            if absolute_bar <= midpoint:
                first += 1
            else:
                second += 1
    return first, second
