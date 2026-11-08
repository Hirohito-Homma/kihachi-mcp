"""Deterministic and provider-backed MIDI composers behind one contract."""

from __future__ import annotations

import json
from dataclasses import replace
from random import Random
from typing import Any, Protocol

from kihachi_mcp.models.midi_composer import (
    COMPOSITION_ENGINE,
    PART_PITCH,
    ROLE_ALL,
    ComposedMidiNote,
    MidiCompositionRequest,
    MusicalConstraints,
)
from kihachi_mcp.models.production_brief import PART_ORDER
from kihachi_mcp.services.ai_provider import AIProvider
from kihachi_mcp.services.session_pattern_builder import is_minor, root_pitch


class MIDIComposer(Protocol):
    def compose(self, request: MidiCompositionRequest) -> tuple[ComposedMidiNote, ...]: ...


class DeterministicMIDIComposer:
    """Small reproducible adapter used offline and as the model fallback baseline."""

    def __init__(self, seed: int = 0) -> None:
        self._seed = seed

    def compose(self, request: MidiCompositionRequest) -> tuple[ComposedMidiNote, ...]:
        if request.operation == "generate":
            notes = self._generate(request.constraints)
        else:
            notes = self._revise(request)
        return tuple(sorted(notes, key=lambda note: (note.start, note.pitch)))

    def _generate(self, c: MusicalConstraints) -> list[ComposedMidiNote]:
        if c.role == ROLE_ALL:
            notes: list[ComposedMidiNote] = []
            for part in PART_ORDER:
                notes.extend(self._generate(replace(c, role=part)))
            return notes
        seed_dict = {k: v for k, v in c.to_dict().items() if k != "swing"}
        rng = Random(f"{self._seed}:{json.dumps(seed_dict, sort_keys=True)}")
        if c.role in PART_PITCH:
            return _part_drum_notes(c, rng, PART_PITCH[c.role])
        engine = COMPOSITION_ENGINE.get(c.role, c.role)
        if engine in {"drums", "percussion"}:
            # Structural choices (ghost notes, extra 16th hats) draw from a
            # dedicated stream seeded without tempo / key / bars, so the
            # groove pattern itself stays invariant under those changes
            # (DOC-003 determinism). Velocity humanization keeps the
            # shared stream above and may still reshuffle.
            structure_rng = Random(
                f"{self._seed}:structure:{c.role}:{c.ghost_notes}:{c.grid}"
                f":{c.mpc_groove}:{c.triplet_rolls}"
            )
            return _rhythm_notes(
                c, rng, percussion=engine == "percussion", structure_rng=structure_rng
            )
        root = root_pitch(c.key, octave_offset={"bass": 0, "chords": 2, "melody": 2, "arp": 2}[engine])
        if engine == "bass":
            return _bass_notes(c, root, rng)
        if engine == "chords":
            return _chord_notes(c, root)
        return _melodic_notes(c, root, rng, arp=engine == "arp")

    def _revise(self, request: MidiCompositionRequest) -> list[ComposedMidiNote]:
        c = request.constraints
        notes: list[ComposedMidiNote] = []
        kick = {round(value, 6) for value in c.kick_onsets}
        grid = _grid_beats(c.grid)
        for index, note in enumerate(request.source_notes):
            start = note.start
            velocity = note.velocity
            engine = COMPOSITION_ENGINE.get(c.role, c.role)
            if engine == "bass" and index % 2 == 1 and c.syncopation >= 0.5:
                candidate = round(start + grid * 2, 6)
                if candidate < c.bars * 4 and (not c.avoid_kick_collision or candidate not in kick):
                    start = candidate
            if request.operation == "variation" and c.variation_bar is not None:
                bar = int(start // 4) + 1
                if bar == c.variation_bar:
                    velocity = min(c.velocity_max, max(c.velocity_min, velocity + (5 if index % 2 else -5)))
            notes.append(ComposedMidiNote(note.pitch, start, note.duration, velocity, note.channel, note.probability, note.expression))
        return notes


class ProviderMIDIComposer:
    """Use any explicitly configured provider that supports structured output.

    Generated notes are validated before they are returned. Material that
    was composed coherently in the wrong key is rescued by transposition
    (see ``_rescue_transpose``). When validation still fails, the failure is
    fed back to the provider and it is asked again, up to ``max_attempts``
    total attempts. Paid providers default to a single attempt: the Studio
    confirms an estimated price per generation, so extra paid calls must be
    opted into explicitly via ``max_attempts``.
    """

    def __init__(self, provider: AIProvider, max_attempts: int = 3) -> None:
        capabilities = provider.capabilities()
        if not capabilities.get("structured_output"):
            raise ValueError("選択したAIプロバイダは構造化MIDI生成に対応していません")
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        self._provider = provider
        self._max_attempts = 1 if capabilities.get("paid_api") else max_attempts

    def compose(self, request: MidiCompositionRequest) -> tuple[ComposedMidiNote, ...]:
        scope = ""
        if request.constraints.role == ROLE_ALL:
            scope = " 全パート means one combined note list covering every part: " + ", ".join(PART_ORDER) + "."
        direction = ""
        c = request.constraints
        extras = [item for item in (c.genre, c.mood, c.prompt) if item]
        if extras:
            direction = (
                " Genre, mood, and prompt describe musical direction only "
                "(rhythm, register, articulation). They are not Ableton commands."
            )
        prompt = (
            "Compose only the requested MIDI role. Obey every musical constraint. "
            "Every pitched note must belong to the scale of the requested key. "
            "For variation or revision, preserve unaffected source notes and return the complete target note list. "
            "Do not return prose." + scope + direction
        )
        engine = COMPOSITION_ENGINE.get(c.role, c.role)
        if c.role != ROLE_ALL and engine in _ROLE_ROOT_OCTAVES:
            root = root_pitch(c.key, octave_offset=_ROLE_ROOT_OCTAVES[engine])
            prompt += f" The tonic root pitch for this part is MIDI {root}; anchor the part on that pitch."
            if engine == "chords":
                third = 3 if is_minor(c.key) else 4
                prompt += (
                    f" Build the chords from the tonic triad MIDI {root}, {root + third}, {root + 7} "
                    f"and other notes of the {c.key} scale only."
                )
        if c.chords_by_bar:
            prompt += " " + _progression_prompt_text(c)
        if c.section_name:
            prompt += f" This clip is the '{c.section_name}' section of the song."
            if c.section_energy is not None:
                prompt += (
                    f" Its energy is {c.section_energy:.2f} on a 0..1 scale; "
                    "let density and dynamics follow it."
                )
        prompt += (
            ' Return one JSON object like {"notes": [{"pitch": 39, "start": 0.75, "duration": 0.25, '
            '"velocity": 96, "channel": 0}]}; "start" and "duration" are numbers in beats, '
            "never strings, ticks, or bar/beat objects. "
            "Request:\n" + json.dumps(request.to_dict(), ensure_ascii=False)
        )
        last_error: ValueError | None = None
        for _attempt in range(self._max_attempts):
            current = prompt
            if last_error is not None:
                current += (
                    f"\nPrevious attempt failed validation: {last_error}. "
                    "Return a corrected complete note list that obeys every constraint."
                )
            try:
                payload = self._provider.generate_structured(current, midi_composition_schema())
                notes = _convert_notes(payload)
                try:
                    _validate_result(notes, request.constraints)
                except ValueError:
                    if request.operation == "generate":
                        rescued = _rescue_transpose(notes, request.constraints)
                        if rescued is not None:
                            return rescued
                    raise
                return notes
            except ValueError as exc:
                # Validation failures (including unparseable or malformed
                # output) are worth another attempt; transport errors and
                # other exceptions propagate immediately.
                last_error = exc
        assert last_error is not None
        if self._max_attempts > 1:
            raise ValueError(f"{last_error}（{self._max_attempts}回の生成すべて検証不合格）")
        raise last_error


_REQUIRED_NOTE_FIELDS = frozenset({"pitch", "start", "duration", "velocity"})


def _convert_notes(payload: dict[str, Any]) -> tuple[ComposedMidiNote, ...]:
    """Convert raw provider notes, naming the offending note on failure.

    The retry loop feeds this message back to the provider, so it must say
    which note was malformed and why, not just that conversion failed.
    Notes missing a timing or pitch field are rejected outright: silently
    defaulting a missing start to 0 would collapse a whole line onto one
    beat and still pass validation. (Channel stays optional; from_dict
    defaults it to 0 and some providers omit it.)
    """
    notes: list[ComposedMidiNote] = []
    for item in payload.get("notes") or []:
        if not isinstance(item, dict) or not _REQUIRED_NOTE_FIELDS <= item.keys():
            raise ValueError(f"MIDI Composerのノート形式が不正です: {item} (必須フィールド不足)")
        try:
            notes.append(ComposedMidiNote.from_dict(item))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"MIDI Composerのノート形式が不正です: {item} ({exc})") from exc
    return tuple(notes)


def midi_composition_schema() -> dict[str, Any]:
    note = {
        "type": "object",
        "additionalProperties": False,
        "required": ["pitch", "start", "duration", "velocity", "channel"],
        "properties": {
            "pitch": {"type": "integer", "minimum": 0, "maximum": 127},
            "start": {"type": "number", "minimum": 0},
            "duration": {"type": "number", "exclusiveMinimum": 0},
            "velocity": {"type": "integer", "minimum": 1, "maximum": 127},
            "channel": {"type": "integer", "minimum": 0, "maximum": 15},
        },
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["notes"],
        "properties": {"notes": {"type": "array", "maxItems": 4096, "items": note}},
    }


# Engines whose notes are pitched and must stay inside the requested key's
# scale. Drum engines use fixed drum pitches, so they are exempt. The
# combined 全パート role is also exempt: its single note list mixes drum
# and pitched parts without part labels, so it cannot be segmented here.
_SCALE_CHECKED_ENGINES = frozenset({"bass", "chords", "melody", "arp"})
_MINOR_SCALE = (0, 2, 3, 5, 7, 8, 10)
_MAJOR_SCALE = (0, 2, 4, 5, 7, 9, 11)
# Register each pitched engine is anchored to, matching the deterministic
# generators in this module.
_ROLE_ROOT_OCTAVES = {"bass": 0, "chords": 2, "melody": 2, "arp": 2}


def _engine_for(role: str) -> str:
    return COMPOSITION_ENGINE.get(role, role)


def _scale_pitch_classes(key: str) -> frozenset[int]:
    """Pitch classes of the key's natural minor / major scale.

    Uses the same tonic parsing and scale shapes as the deterministic
    generators below, so provider output is judged by the house rules.
    """
    tonic = (root_pitch(key) - 36) % 12
    intervals = _MINOR_SCALE if is_minor(key) else _MAJOR_SCALE
    return frozenset((tonic + interval) % 12 for interval in intervals)


_CHORD_ROOT_PC = {"c": 0, "d": 2, "e": 4, "f": 5, "g": 7, "a": 9, "b": 11}
_CHORD_QUALITIES: dict[str, tuple[int, ...]] = {
    "": (0, 4, 7),
    "maj": (0, 4, 7),
    "m": (0, 3, 7),
    "min": (0, 3, 7),
    "-": (0, 3, 7),
    "7": (0, 4, 7, 10),
    "m7": (0, 3, 7, 10),
    "min7": (0, 3, 7, 10),
    "maj7": (0, 4, 7, 11),
    "mmaj7": (0, 3, 7, 11),
    "dim": (0, 3, 6),
    "dim7": (0, 3, 6, 9),
    "aug": (0, 4, 8),
    "sus4": (0, 5, 7),
    "sus2": (0, 2, 7),
    "5": (0, 7),
    "6": (0, 4, 7, 9),
    "m6": (0, 3, 7, 9),
    "9": (0, 4, 7, 10, 14),
    "m9": (0, 3, 7, 10, 14),
    "add9": (0, 4, 7, 14),
}


def _parse_chord(symbol: str) -> tuple[int, tuple[int, ...]] | None:
    """Parse an absolute chord symbol (``D#m``, ``B``, ``F#m7``).

    Returns ``(root_pitch_class, intervals)`` or None when the symbol is
    empty or its quality is not a supported spelling, so an unknown chord
    opts out of chord-aware behavior instead of being judged against a
    guessed voicing.
    """
    text = (symbol or "").strip()
    if not text or text[0].lower() not in _CHORD_ROOT_PC:
        return None
    root_pc = _CHORD_ROOT_PC[text[0].lower()]
    rest = text[1:]
    if rest.startswith("#"):
        root_pc += 1
        rest = rest[1:]
    elif rest.startswith("b"):
        root_pc -= 1
        rest = rest[1:]
    intervals = _CHORD_QUALITIES.get(rest.strip().lower())
    if intervals is None:
        return None
    return root_pc % 12, intervals


def _chord_pitch_classes(symbol: str) -> frozenset[int] | None:
    parsed = _parse_chord(symbol)
    if parsed is None:
        return None
    root_pc, intervals = parsed
    return frozenset((root_pc + interval) % 12 for interval in intervals)


def _bar_chord_tones(c: MusicalConstraints, bar: int) -> frozenset[int] | None:
    """Chord tones in force at a 0-based bar of the clip, if specified."""
    if not c.chords_by_bar or not 0 <= bar < len(c.chords_by_bar):
        return None
    return _chord_pitch_classes(c.chords_by_bar[bar])


def _chord_root_pitch(c: MusicalConstraints, bar: int, base_pitch: int) -> int:
    """Chord root for a bar, voiced in the register around ``base_pitch``.

    Falls back to ``base_pitch`` (the key's tonic anchor) when the bar has
    no parseable chord, so progression-free generation is unchanged.
    """
    if not c.chords_by_bar or not 0 <= bar < len(c.chords_by_bar):
        return base_pitch
    parsed = _parse_chord(c.chords_by_bar[bar])
    if parsed is None:
        return base_pitch
    pitch = base_pitch - (base_pitch % 12) + parsed[0]
    while pitch - base_pitch > 6:
        pitch -= 12
    while base_pitch - pitch > 6:
        pitch += 12
    return pitch


def _snap_to_chord_tone(pitch: int, tones: frozenset[int]) -> int:
    """Nearest pitch whose pitch class is a chord tone (ties prefer up)."""
    for delta in range(1, 7):
        if pitch + delta <= 127 and (pitch + delta) % 12 in tones:
            return pitch + delta
        if pitch - delta >= 0 and (pitch - delta) % 12 in tones:
            return pitch - delta
    return pitch


#: A melodic line may use scale passing tones freely; it fails the chord
#: check only when most of its judged notes ignore the progression.
_CHORD_FIT_LIMIT = 0.5


def chord_fit_ratio(
    notes: tuple[ComposedMidiNote, ...], c: MusicalConstraints
) -> float | None:
    """Share of pitched notes that fall outside their bar's chord tones.

    Only bars whose chord symbol could be parsed are judged. Drum parts,
    the combined all-parts role, and clips without a usable progression
    return None. 0.0 means every judged note is a chord tone.
    """
    if c.role == ROLE_ALL or _engine_for(c.role) not in _SCALE_CHECKED_ENGINES:
        return None
    if not c.chords_by_bar:
        return None
    judged = 0
    off_chord = 0
    for note in notes:
        tones = _bar_chord_tones(c, int(note.start // 4))
        if tones is None:
            continue
        judged += 1
        if note.pitch % 12 not in tones:
            off_chord += 1
    if judged == 0:
        return None
    return off_chord / judged


def _progression_prompt_text(c: MusicalConstraints) -> str:
    """Run-length summary of the per-bar progression for provider prompts."""
    runs: list[str] = []
    start = 0
    for index in range(1, len(c.chords_by_bar) + 1):
        if index == len(c.chords_by_bar) or c.chords_by_bar[index] != c.chords_by_bar[start]:
            symbol = c.chords_by_bar[start] or "(no chord specified)"
            if index - start == 1:
                runs.append(f"bar {start + 1}: {symbol}")
            else:
                runs.append(f"bars {start + 1}-{index}: {symbol}")
            start = index
    return (
        "Chord progression by bar: " + "; ".join(runs) + ". Follow it: the bass anchors "
        "on each bar's chord root, chord parts play that bar's chord tones, and melody "
        "and arp lines favor that bar's chord tones on strong beats (scale passing "
        "tones are fine elsewhere)."
    )


def _rescue_transpose(
    notes: tuple[ComposedMidiNote, ...], c: MusicalConstraints
) -> tuple[ComposedMidiNote, ...] | None:
    """Rescue material composed coherently in the wrong key by transposing it.

    Models sometimes anchor on the wrong tonic yet write a line that is
    internally consistent in that other key. A rescue is attempted only when
    the notes establish an implied key: at least four notes, at least three
    distinct pitch classes, and a most-common pitch acting as implied tonic.
    A shift qualifies when it maps the implied tonic onto the requested
    tonic, puts every note inside the requested scale, and keeps every pitch
    in MIDI range. Among qualifying shifts, the one landing the implied
    tonic closest to the engine's house root pitch wins, so the rescued
    part stays in its expected register. Anything else returns None and the
    caller falls back to regeneration.
    """
    engine = _engine_for(c.role)
    if c.role == ROLE_ALL or engine not in _SCALE_CHECKED_ENGINES or len(notes) < 4:
        return None
    if len({note.pitch % 12 for note in notes}) < 3:
        return None
    allowed = _scale_pitch_classes(c.key)
    tonic_pc = (root_pitch(c.key) - 36) % 12
    counts: dict[int, int] = {}
    for note in notes:
        counts[note.pitch] = counts.get(note.pitch, 0) + 1
    implied_tonic = min(counts, key=lambda pitch: (-counts[pitch], pitch))
    expected_root = root_pitch(c.key, octave_offset=_ROLE_ROOT_OCTAVES[engine])
    best: tuple[int, int] | None = None
    for shift in range(-11, 12):
        if shift == 0 or (implied_tonic + shift) % 12 != tonic_pc:
            continue
        if any(not 0 <= note.pitch + shift <= 127 for note in notes):
            continue
        if any((note.pitch + shift) % 12 not in allowed for note in notes):
            continue
        candidate = (abs(implied_tonic + shift - expected_root), shift)
        if best is None or candidate < best:
            best = candidate
    if best is None:
        return None
    shift = best[1]
    rescued = tuple(
        ComposedMidiNote(
            note.pitch + shift, note.start, note.duration, note.velocity,
            note.channel, note.probability, note.expression,
        )
        for note in notes
    )
    try:
        _validate_result(rescued, c)
    except ValueError:
        return None
    return rescued


def _validate_result(notes: tuple[ComposedMidiNote, ...], c: MusicalConstraints) -> None:
    if not notes:
        raise ValueError("MIDI Composerがノートを返しませんでした")
    end = c.bars * 4
    if any(note.start + note.duration > end + 1e-6 for note in notes):
        raise ValueError("MIDI Composerのノートが指定小節を超えています")
    if any(not c.velocity_min <= note.velocity <= c.velocity_max for note in notes):
        raise ValueError("MIDI Composerのvelocityが制約外です")
    if c.role != ROLE_ALL and _engine_for(c.role) in _SCALE_CHECKED_ENGINES:
        allowed = _scale_pitch_classes(c.key)
        offending = sorted({note.pitch for note in notes if note.pitch % 12 not in allowed})
        if offending:
            raise ValueError(
                f"MIDI Composerのノートにキー {c.key} のスケール外の音が含まれています: {offending}"
            )
    ratio = chord_fit_ratio(notes, c)
    if ratio is not None and ratio > _CHORD_FIT_LIMIT:
        raise ValueError(
            f"MIDI Composerのノートがコード進行に合っていません"
            f"（コード外の音 {ratio:.0%}、上限 {_CHORD_FIT_LIMIT:.0%}）"
        )


def _grid_beats(grid: str) -> float:
    return {"1/4": 1.0, "1/8": 0.5, "1/16": 0.25, "1/32": 0.125}[grid]


def _apply_swing(position: float, swing: float, grid: str = "1/16") -> float:
    """Apply swing warp to position in beats.

    0.5 is straight. Values like 0.54 shift offbeat eighths (0.5 -> 0.54)
    and sixteenths (0.25 -> 0.27, 0.75 -> 0.77) with consistent 54% groove.
    Quarter-note downbeats (0.0, 1.0, ...) stay locked on-grid.
    """
    if abs(swing - 0.5) < 1e-6:
        return round(position, 6)
    beat = int(position)
    offset = position - beat
    if offset < 1e-6:
        return float(beat)

    if abs(offset - 0.5) < 1e-6:
        return round(beat + swing, 6)
    if abs(offset - 0.25) < 1e-6:
        return round(beat + swing * 0.5, 6)
    if abs(offset - 0.75) < 1e-6:
        return round(beat + swing + (1.0 - swing) * 0.5, 6)

    # Continuous interpolation
    if offset <= 0.5:
        warped = (offset / 0.5) * swing
    else:
        warped = swing + ((offset - 0.5) / 0.5) * (1.0 - swing)
    return round(beat + warped, 6)


def _apply_mpc_groove(position: float, swing: float, grid: str = "1/16") -> float:
    """Apply MPC60/3000 style micro-groove offsets and laid-back humanize.

    MPC micro-timing offsets:
    - Downbeat (0.0): Tight on grid with slight laid-back feel (+0.003 beats)
    - 16th 'e' (0.25): Slightly pushed/laid back (+0.012 beats)
    - 8th offbeat '&' (0.5): Swung position with subtle extra lag (+0.010 beats)
    - 16th 'a' (0.75): Antici-pated groove pocket (+0.014 beats)
    """
    base_swung = _apply_swing(position, swing, grid)
    beat = int(position)
    offset = position - beat

    # Determine 16th position
    step = round(offset * 4) % 4
    mpc_delays = (0.002, 0.012, 0.009, 0.014)
    delay = mpc_delays[step]
    return round(base_swung + delay, 6)


def _generate_triplet_roll(
    start_beat: float,
    duration_beats: float,
    base_velocity: int,
    pitch: int = 42,
    divisions: int = 3,
) -> list[ComposedMidiNote]:
    """Generate a triplet roll (e.g. 1/8T or 1/16T) with velocity crescendo."""
    roll_notes: list[ComposedMidiNote] = []
    step_duration = duration_beats / divisions
    for i in range(divisions):
        note_start = round(start_beat + i * step_duration, 6)
        # Crescendo: ramping velocity up towards the accent
        vel_boost = int((i / max(1, divisions - 1)) * 20)
        vel = min(127, base_velocity - 15 + vel_boost)
        roll_notes.append(
            ComposedMidiNote(
                pitch=pitch,
                start=note_start,
                duration=round(step_duration * 0.8, 6),
                velocity=max(40, vel),
                channel=9,
            )
        )
    return roll_notes


def _velocity(
    c: MusicalConstraints,
    rng: Random,
    ghost: bool = False,
    accent: bool = False,
) -> int:
    if ghost and c.ghost_notes:
        # Ghost notes are strictly quieter than standard notes (35-55)
        return rng.randint(35, min(55, max(36, c.velocity_min - 5)))
    if accent:
        return rng.randint(max(c.velocity_min, min(c.velocity_max, 105)), c.velocity_max)
    return rng.randint(max(c.velocity_min, c.velocity_max - 24), min(c.velocity_max, c.velocity_max - 5))


def _rhythm_notes(
    c: MusicalConstraints,
    rng: Random,
    percussion: bool | None = None,
    structure_rng: Random | None = None,
) -> list[ComposedMidiNote]:
    # Structural gates draw from structure_rng (seeded without tempo / key /
    # bars); fall back to the shared stream for direct callers.
    if structure_rng is None:
        structure_rng = rng
    notes: list[ComposedMidiNote] = []
    drum_part = c.role == "drums" if percussion is None else not percussion
    swing = getattr(c, "swing", 0.5)
    mpc = getattr(c, "mpc_groove", False)
    rolls = getattr(c, "triplet_rolls", False)
    warp_fn = _apply_mpc_groove if mpc else _apply_swing
    max_beats = c.bars * 4.0

    for bar in range(c.bars):
        base = bar * 4.0
        if drum_part:
            # 1. Kick: Solid 4-on-the-floor, locked on beats (no swing), high velocity
            for beat in range(4):
                kick_time = base + beat
                if kick_time + 0.125 <= max_beats:
                    notes.append(ComposedMidiNote(36, kick_time, 0.125, _velocity(c, rng, accent=True), 9))

            # 2. Snare / Clap on 2 and 4 (beats 1.0 and 3.0)
            snare_layback = 0.008 if mpc else 0.0
            for offset in (1.0, 3.0):
                snare_time = base + offset + snare_layback
                if snare_time + 0.125 <= max_beats:
                    notes.append(ComposedMidiNote(38, snare_time, 0.125, _velocity(c, rng, accent=True), 9))

            # Ghost snare hits in funk/tech groove
            if c.ghost_notes and (bar % 2 == 1 or structure_rng.random() < 0.6):
                for ghost_offset in (1.75, 3.25):
                    if structure_rng.random() < 0.7:
                        g_time = warp_fn(base + ghost_offset, swing, c.grid)
                        if g_time + 0.08 <= max_beats:
                            notes.append(ComposedMidiNote(38, g_time, 0.08, _velocity(c, rng, ghost=True), 9))

            # 3. Hi-Hats: 8ths or 16ths with dynamics & swing / Triplet rolls
            roll_this_bar = rolls and (bar % 2 == 1)
            for step in range(8):
                # If roll is active on the last beat (step 6, 7 = beat 3.0..4.0), let roll handle it
                if roll_this_bar and step >= 6:
                    continue
                raw_offset = step * 0.5
                is_offbeat = step % 2 == 1
                hat_time = warp_fn(base + raw_offset, swing, c.grid) if is_offbeat else base + raw_offset
                accent = is_offbeat
                pitch = 46 if (is_offbeat and bar % 4 == 3 and step == 7) else 42
                if hat_time + 0.1 <= max_beats:
                    notes.append(ComposedMidiNote(pitch, hat_time, 0.1, _velocity(c, rng, accent=accent), 9))

                if c.grid == "1/16" and (bar % 4 in (1, 3)) and structure_rng.random() < 0.5:
                    semi_offset = raw_offset + 0.25
                    semi_time = warp_fn(base + semi_offset, swing, c.grid)
                    if semi_time + 0.06 <= max_beats:
                        notes.append(ComposedMidiNote(42, semi_time, 0.06, _velocity(c, rng, ghost=True), 9))

            if roll_this_bar and base + 3.0 + 1.0 <= max_beats:
                notes.extend(_generate_triplet_roll(base + 3.0, 1.0, _velocity(c, rng, accent=True), pitch=42, divisions=6))
        else:
            # Percussion: syncopated congas/shakers with swing
            for offset in (0.75, 1.5, 2.75, 3.5):
                p_time = warp_fn(base + offset, swing, c.grid)
                pitch = 70 if int(offset * 2) % 2 else 71
                if p_time + 0.12 <= max_beats:
                    notes.append(ComposedMidiNote(pitch, p_time, 0.12, _velocity(c, rng), 9))
    return notes


def _part_drum_notes(c: MusicalConstraints, rng: Random, pitch: int) -> list[ComposedMidiNote]:
    """One drum part only. Kick is every beat; the others sit on the backbeat or offbeats."""
    notes: list[ComposedMidiNote] = []
    swing = getattr(c, "swing", 0.5)
    mpc = getattr(c, "mpc_groove", False)
    rolls = getattr(c, "triplet_rolls", False)
    warp_fn = _apply_mpc_groove if mpc else _apply_swing
    max_beats = c.bars * 4.0
    for bar in range(c.bars):
        base = bar * 4.0
        if pitch == 36:
            # Kick: strict 4-on-the-floor, unswung
            for b in (0.0, 1.0, 2.0, 3.0):
                t = base + b
                if t + 0.125 <= max_beats:
                    notes.append(ComposedMidiNote(36, t, 0.125, _velocity(c, rng, accent=True), 9))
        elif pitch == 42:
            # Hats: swung offbeats & dynamics / Triplet rolls
            roll_this_bar = rolls and (bar % 2 == 1)
            for step in range(8):
                if roll_this_bar and step >= 6:
                    continue
                raw = step * 0.5
                is_off = step % 2 == 1
                t = warp_fn(base + raw, swing, c.grid) if is_off else base + raw
                if t + 0.1 <= max_beats:
                    notes.append(ComposedMidiNote(42, t, 0.1, _velocity(c, rng, accent=is_off), 9))
            if roll_this_bar and base + 3.0 + 1.0 <= max_beats:
                notes.extend(_generate_triplet_roll(base + 3.0, 1.0, _velocity(c, rng, accent=True), pitch=42, divisions=6))
        elif pitch in (38, 39):
            # Snare/Clap: backbeat + optional ghost
            snare_layback = 0.008 if mpc else 0.0
            for b in (1.0, 3.0):
                t = base + b + snare_layback
                if t + 0.125 <= max_beats:
                    notes.append(ComposedMidiNote(pitch, t, 0.125, _velocity(c, rng, accent=True), 9))
            if c.ghost_notes and (bar % 2 == 1):
                g_t = warp_fn(base + 1.75, swing, c.grid)
                if g_t + 0.08 <= max_beats:
                    notes.append(ComposedMidiNote(pitch, g_t, 0.08, _velocity(c, rng, ghost=True), 9))
        else:
            for offset in (1.0, 3.0):
                t = base + offset
                if t + 0.125 <= max_beats:
                    notes.append(ComposedMidiNote(pitch, t, 0.125, _velocity(c, rng), 9))
    return notes


def _bass_notes(c: MusicalConstraints, root: int, rng: Random) -> list[ComposedMidiNote]:
    """Funky Slap Bass: Thumb on downbeats/syncopations, Pop on offbeats, ghost notes, 16-bar variations."""
    notes: list[ComposedMidiNote] = []
    kick = {round(value, 6) for value in c.kick_onsets}
    swing = getattr(c, "swing", 0.5)
    max_beats = c.bars * 4.0

    # Scale intervals for minor funk slap:
    # root, minor 3rd (+3), 4th (+5), 5th (+7), minor 7th (+10), octave (+12)
    m3, p4, p5, m7, oct_int = 3, 5, 7, 10, 12

    song_root = root
    for bar in range(c.bars):
        base = bar * 4.0
        # With a progression, each bar anchors on its own chord root (the
        # pattern shapes stay the house shapes, transposed to the chord).
        root = _chord_root_pitch(c, bar, song_root)
        is_turnaround = (bar % 8 == 7)
        is_fill_bar = (bar % 4 == 3)

        if is_turnaround:
            # 8-bar turnaround: dense funk pentatonic turnaround run
            pattern = [
                (0.0, root, "thumb", 0.25),
                (0.5, root + oct_int, "pop", 0.2),
                (0.75, root + oct_int, "pop", 0.15),
                (1.5, root + m7, "pop", 0.2),
                (2.0, root + p5, "thumb", 0.2),
                (2.5, root + p4, "thumb", 0.2),
                (3.0, root + m3, "thumb", 0.2),
                (3.5, root, "thumb", 0.2),
                (3.75, root, "ghost", 0.1),
            ]
        elif is_fill_bar:
            # 4-bar fill: Octave pop repetitions & ghost flurry
            pattern = [
                (0.0, root, "thumb", 0.3),
                (0.5, root + oct_int, "pop", 0.2),
                (1.0, root, "ghost", 0.1),
                (1.25, root + oct_int, "pop", 0.15),
                (1.75, root + m7, "pop", 0.2),
                (2.5, root + oct_int, "pop", 0.2),
                (3.0, root, "thumb", 0.2),
                (3.5, root + oct_int, "pop", 0.2),
            ]
        else:
            # Core funky slap groove: Thumb on beat, Pop on offbeats, ghost notes in 16th pockets
            pattern = [
                (0.0, root, "thumb", 0.35),
                (0.75 if c.syncopation >= 0.5 else 1.0, root + oct_int, "pop", 0.25),
                (1.5, root + m7 if (bar % 2 == 1) else root, "pop" if (bar % 2 == 1) else "thumb", 0.25),
                (2.0, root, "ghost" if c.ghost_notes else "thumb", 0.15 if c.ghost_notes else 0.3),
                (2.5, root + oct_int, "pop", 0.25),
                (3.25 if c.syncopation >= 0.5 else 3.0, root + oct_int, "pop", 0.2),
            ]
            if c.ghost_notes:
                pattern.append((1.25, root, "ghost", 0.08))
                pattern.append((2.75, root, "ghost", 0.08))

        # Render notes with swing, kick collision handling, and velocity shaping
        for offset, pitch, style, length in pattern:
            start = _apply_swing(base + offset, swing, c.grid) if (offset % 0.5 != 0 or (c.grid == "1/8" and offset % 1.0 == 0.5)) else base + offset

            if c.avoid_kick_collision and round(start, 6) in kick and offset != 0.0:
                start = _apply_swing(base + offset + 0.25, swing, c.grid)

            if start >= max_beats:
                continue

            duration = min(length, c.note_length_max)
            if start + duration > max_beats:
                duration = max_beats - start
            if duration <= 1e-6:
                continue

            if style == "ghost":
                vel = _velocity(c, rng, ghost=True)
            elif style == "pop":
                vel = min(c.velocity_max, _velocity(c, rng, accent=True) + 4)
            else:
                vel = _velocity(c, rng, accent=(offset == 0.0))

            notes.append(ComposedMidiNote(pitch, start, duration, vel, 0))

    return _enforce_monophonic(notes)


def _enforce_monophonic(notes: list[ComposedMidiNote]) -> list[ComposedMidiNote]:
    """Keep a single-line part single: no two notes may sound at once.

    The kick-avoidance shift (and swing warping) can move a note onto the
    onset of another, or into its tail. At a shared onset the louder note
    wins (a ghost never doubles a main hit); otherwise the earlier note
    is truncated to end where the next one starts.
    """
    ordered = sorted(notes, key=lambda note: (note.start, -note.velocity))
    kept: list[ComposedMidiNote] = []
    for note in ordered:
        if kept:
            previous = kept[-1]
            previous_end = previous.start + previous.duration
            if note.start < previous_end - 1e-6:
                if abs(note.start - previous.start) <= 1e-6:
                    # Shared onset: the louder note keeps the onset. The
                    # quieter hit survives as a pickup ending exactly where
                    # the main note starts, when that pocket is free.
                    if note.velocity > previous.velocity:
                        kept[-1] = note
                    else:
                        pickup_start = previous.start - note.duration
                        before = kept[-2] if len(kept) >= 2 else None
                        if pickup_start >= 0 and (
                            before is None
                            or pickup_start >= before.start + before.duration - 1e-6
                        ):
                            kept.insert(
                                len(kept) - 1,
                                ComposedMidiNote(
                                    note.pitch,
                                    pickup_start,
                                    note.duration,
                                    note.velocity,
                                    note.channel,
                                    note.probability,
                                    note.expression,
                                ),
                            )
                    continue
                truncated = note.start - previous.start
                if truncated <= 0.02:
                    kept.pop()
                else:
                    kept[-1] = ComposedMidiNote(
                        previous.pitch,
                        previous.start,
                        truncated,
                        previous.velocity,
                        previous.channel,
                        previous.probability,
                        previous.expression,
                    )
        kept.append(note)
    return kept


def _chord_notes(c: MusicalConstraints, root: int) -> list[ComposedMidiNote]:
    """Dub Chord Stabs: offbeat stabs with delay echo repeats, minor 7th voicing, 16-bar density."""
    notes: list[ComposedMidiNote] = []
    swing = getattr(c, "swing", 0.5)
    max_beats = c.bars * 4.0
    chord_intervals = (0, 3, 7, 10) if c.key.endswith("m") else (0, 4, 7, 11)

    for bar in range(c.bars):
        base = bar * 4.0
        # With a progression, stab the bar's own chord in the house register;
        # without one, keep the tonic voicing this generator has always used.
        bar_root = root
        bar_intervals = chord_intervals
        if c.chords_by_bar and bar < len(c.chords_by_bar):
            parsed = _parse_chord(c.chords_by_bar[bar])
            if parsed is not None:
                bar_root = _chord_root_pitch(c, bar, root)
                bar_intervals = parsed[1]
        stabs = [(1.5, 108)]
        if bar % 2 == 1:
            stabs.append((3.5, 102))

        echo_repeats = []
        for stab_pos, main_vel in stabs:
            echo_repeats.append((stab_pos, main_vel, False))
            # 1st echo reflection (dotted 8th delay -> 16th offbeat)
            echo_repeats.append((stab_pos + 0.75, max(c.velocity_min, main_vel - 32), True))
            # 2nd echo reflection (1 beat later -> 8th offbeat)
            if bar % 4 in (2, 3):
                echo_repeats.append((stab_pos + 1.0, max(c.velocity_min, main_vel - 55), True))

        for offset, vel, is_echo in echo_repeats:
            # Echoes naturally spill across bar boundaries, but must stay inside the clip
            if base + offset >= max_beats:
                continue
            start = _apply_swing(base + offset, swing, c.grid)
            if start >= max_beats:
                continue
            stab_length = 0.2 if not is_echo else 0.15
            duration = min(stab_length, c.note_length_max)
            if start + duration > max_beats:
                duration = max_beats - start
            if duration <= 1e-6:
                continue

            for interval in bar_intervals:
                notes.append(
                    ComposedMidiNote(
                        bar_root + interval,
                        start,
                        duration,
                        vel,
                        0,
                    )
                )

    return notes


def _melodic_notes(c: MusicalConstraints, root: int, rng: Random, arp: bool) -> list[ComposedMidiNote]:
    scale = (0, 2, 3, 5, 7, 8, 10) if c.key.endswith("m") else (0, 2, 4, 5, 7, 9, 11)
    step = _grid_beats(c.grid) if arp else max(0.5, _grid_beats(c.grid) * 2)
    swing = getattr(c, "swing", 0.5)
    max_beats = c.bars * 4.0
    notes = []
    count = int(c.bars * 4 / step)
    for index in range(count):
        raw_start = index * step
        start = _apply_swing(raw_start, swing, c.grid) if (index % 2 == 1) else raw_start
        if start >= max_beats:
            continue
        duration = min(step * 0.8, c.note_length_max)
        if start + duration > max_beats:
            duration = max_beats - start
        if duration <= 1e-6:
            continue
        pitch = root + scale[(index * 2 if arp else rng.randrange(len(scale))) % len(scale)]
        if c.chords_by_bar and raw_start % 1.0 == 0:
            # Strong beats land on the bar's chord tones when a progression
            # is in force; passing tones between beats stay free.
            tones = _bar_chord_tones(c, int(raw_start // 4))
            if tones is not None and pitch % 12 not in tones:
                pitch = _snap_to_chord_tone(pitch, tones)
        notes.append(ComposedMidiNote(pitch, round(start, 6), duration, _velocity(c, rng), 0))
    return notes
