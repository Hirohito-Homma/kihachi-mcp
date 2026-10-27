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
from kihachi_mcp.services.session_pattern_builder import root_pitch


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
            return _rhythm_notes(c, rng, percussion=engine == "percussion")
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
    """Use any explicitly configured provider that supports structured output."""

    def __init__(self, provider: AIProvider) -> None:
        if not provider.capabilities().get("structured_output"):
            raise ValueError("選択したAIプロバイダは構造化MIDI生成に対応していません")
        self._provider = provider

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
            "For variation or revision, preserve unaffected source notes and return the complete target note list. "
            "Do not return prose." + scope + direction + " Request:\n" + json.dumps(request.to_dict(), ensure_ascii=False)
        )
        payload = self._provider.generate_structured(prompt, midi_composition_schema())
        notes = tuple(ComposedMidiNote.from_dict(item) for item in payload.get("notes") or [])
        _validate_result(notes, request.constraints)
        return notes


MAX_COMPOSED_MIDI_NOTES = 1024


def midi_composition_schema(max_notes: int = MAX_COMPOSED_MIDI_NOTES) -> dict[str, Any]:
    if max_notes < 1 or max_notes > MAX_COMPOSED_MIDI_NOTES:
        raise ValueError(f"MIDI構造化出力の上限ノート数は1〜{MAX_COMPOSED_MIDI_NOTES}件の範囲で指定してください")
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
        "properties": {"notes": {"type": "array", "maxItems": max_notes, "items": note}},
    }


def _validate_result(notes: tuple[ComposedMidiNote, ...], c: MusicalConstraints) -> None:
    if not notes:
        raise ValueError("MIDI Composerがノートを返しませんでした")
    if len(notes) > MAX_COMPOSED_MIDI_NOTES:
        raise ValueError(f"MIDI Composerのノート数が上限（{MAX_COMPOSED_MIDI_NOTES}件）を超えています")
    end = c.bars * 4
    if any(note.start + note.duration > end + 1e-6 for note in notes):
        raise ValueError("MIDI Composerのノートが指定小節を超えています")
    for note in notes:
        if c.velocity_min <= note.velocity <= c.velocity_max:
            continue
        if c.ghost_notes and 35 <= note.velocity <= min(55, c.velocity_max):
            continue
        raise ValueError("MIDI Composerのvelocityが制約外です")


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
    c: MusicalConstraints, rng: Random, percussion: bool | None = None
) -> list[ComposedMidiNote]:
    notes: list[ComposedMidiNote] = []
    drum_part = c.role == "drums" if percussion is None else not percussion
    swing = getattr(c, "swing", 0.5)
    max_beats = c.bars * 4.0

    for bar in range(c.bars):
        base = bar * 4.0
        if drum_part:
            # 1. Follow the abstract kick profile measured from the reference.
            if c.kick_style == "broken_beat":
                kick_offsets = (0.0, 1.5, 2.5, 3.5)
            else:
                kick_offsets = (0.0, 1.0, 2.0, 3.0)
            if c.kick_density_per_bar is not None:
                count = max(1, min(len(kick_offsets), round(c.kick_density_per_bar)))
                kick_offsets = tuple(kick_offsets[:count])
            for beat in kick_offsets:
                kick_time = base + beat
                if kick_time + 0.125 <= max_beats:
                    notes.append(ComposedMidiNote(36, kick_time, 0.125, _velocity(c, rng, accent=True), 9))

            # 2. Snare / Clap on 2 and 4 (beats 1.0 and 3.0)
            for offset in (1.0, 3.0):
                snare_time = base + offset
                if snare_time + 0.125 <= max_beats:
                    notes.append(ComposedMidiNote(38, snare_time, 0.125, _velocity(c, rng, accent=True), 9))

            # Ghost snare hits in funk/tech groove
            if c.ghost_notes and (bar % 2 == 1 or rng.random() < 0.6):
                for ghost_offset in (1.75, 3.25):
                    if rng.random() < 0.7:
                        g_time = _apply_swing(base + ghost_offset, swing, c.grid)
                        if g_time + 0.08 <= max_beats:
                            notes.append(ComposedMidiNote(38, g_time, 0.08, _velocity(c, rng, ghost=True), 9))

            # 3. Hi-Hats: 8ths or 16ths with dynamics & swing
            for step in range(8):
                raw_offset = step * 0.5
                is_offbeat = step % 2 == 1
                hat_time = _apply_swing(base + raw_offset, swing, c.grid) if is_offbeat else base + raw_offset
                accent = is_offbeat
                pitch = 46 if (is_offbeat and bar % 4 == 3 and step == 7) else 42
                if hat_time + 0.1 <= max_beats:
                    notes.append(ComposedMidiNote(pitch, hat_time, 0.1, _velocity(c, rng, accent=accent), 9))

                if c.grid == "1/16" and (bar % 4 in (1, 3)) and rng.random() < 0.5:
                    semi_offset = raw_offset + 0.25
                    semi_time = _apply_swing(base + semi_offset, swing, c.grid)
                    if semi_time + 0.06 <= max_beats:
                        notes.append(ComposedMidiNote(42, semi_time, 0.06, _velocity(c, rng, ghost=True), 9))
        else:
            # Percussion: syncopated congas/shakers with swing
            for offset in (0.75, 1.5, 2.75, 3.5):
                p_time = _apply_swing(base + offset, swing, c.grid)
                pitch = 70 if int(offset * 2) % 2 else 71
                if p_time + 0.12 <= max_beats:
                    notes.append(ComposedMidiNote(pitch, p_time, 0.12, _velocity(c, rng), 9))
    return notes


def _part_drum_notes(c: MusicalConstraints, rng: Random, pitch: int) -> list[ComposedMidiNote]:
    """One drum part only. Kick is every beat; the others sit on the backbeat or offbeats."""
    notes: list[ComposedMidiNote] = []
    swing = getattr(c, "swing", 0.5)
    max_beats = c.bars * 4.0
    for bar in range(c.bars):
        base = bar * 4.0
        if pitch == 36:
            # Kick: follow the abstract reference rhythm profile.
            offsets = (0.0, 1.5, 2.5, 3.5) if c.kick_style == "broken_beat" else (0.0, 1.0, 2.0, 3.0)
            if c.kick_density_per_bar is not None:
                offsets = offsets[:max(1, min(len(offsets), round(c.kick_density_per_bar)))]
            for b in offsets:
                t = base + b
                if t + 0.125 <= max_beats:
                    notes.append(ComposedMidiNote(36, t, 0.125, _velocity(c, rng, accent=True), 9))
        elif pitch == 42:
            # Hats: swung offbeats & dynamics
            for step in range(8):
                raw = step * 0.5
                is_off = step % 2 == 1
                t = _apply_swing(base + raw, swing, c.grid) if is_off else base + raw
                if t + 0.1 <= max_beats:
                    notes.append(ComposedMidiNote(42, t, 0.1, _velocity(c, rng, accent=is_off), 9))
        elif pitch in (38, 39):
            # Snare/Clap: backbeat + optional ghost
            for b in (1.0, 3.0):
                t = base + b
                if t + 0.125 <= max_beats:
                    notes.append(ComposedMidiNote(pitch, t, 0.125, _velocity(c, rng, accent=True), 9))
            if c.ghost_notes and (bar % 2 == 1):
                g_t = _apply_swing(base + 1.75, swing, c.grid)
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

    for bar in range(c.bars):
        base = bar * 4.0
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
                (0.75, root, "ghost", 0.1),
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

        if c.bass_density_per_bar is not None and pattern:
            wanted = max(1, min(len(pattern), round(c.bass_density_per_bar)))
            if wanted < len(pattern):
                def density_priority(item: tuple[float, int, str, float]) -> tuple[int, float]:
                    offset = item[0]
                    is_offbeat = abs(offset % 1.0) > 1e-6
                    prefers_offbeat = c.syncopation >= 0.5
                    kick_overlap = any(abs(base + offset - onset) <= 0.125 for onset in kick)
                    return (int(not kick_overlap) + int(is_offbeat == prefers_offbeat), -offset)
                chosen = sorted(pattern, key=density_priority, reverse=True)[:wanted]
                # Keep a tonic anchor in each bar, then restore chronological order.
                anchor = next((item for item in pattern if item[0] == 0.0), None)
                if anchor is not None and anchor not in chosen:
                    chosen[-1] = anchor
                pattern = sorted(chosen, key=lambda item: item[0])

        # Render notes with swing, kick collision handling, and velocity shaping
        for offset, pitch, style, length in pattern:
            start = _apply_swing(base + offset, swing, c.grid) if (offset % 0.5 != 0 or (c.grid == "1/8" and offset % 1.0 == 0.5)) else base + offset

            if c.avoid_kick_collision and round(start, 6) in kick and offset != 0.0:
                shifted = _apply_swing(base + offset + 0.25, swing, c.grid)
                occupied = {
                    _apply_swing(base + o, swing, c.grid) if (o % 0.5 != 0 or (c.grid == "1/8" and o % 1.0 == 0.5)) else base + o
                    for o, _, _, _ in pattern
                    if o != offset
                }
                if any(abs(shifted - occ) < 1e-4 for occ in occupied):
                    continue
                start = shifted

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

    # Ensure strict monophony (max_polyphony=1) for bass
    cleaned: list[ComposedMidiNote] = []
    for note in sorted(notes, key=lambda n: n.start):
        if cleaned:
            prev = cleaned[-1]
            if abs(note.start - prev.start) < 1e-4:
                continue
            if prev.start + prev.duration > note.start - 1e-6:
                trimmed_dur = max(0.05, round(note.start - prev.start, 4))
                cleaned[-1] = ComposedMidiNote(
                    prev.pitch,
                    prev.start,
                    trimmed_dur,
                    prev.velocity,
                    prev.channel,
                    prev.probability,
                    prev.expression,
                )
        cleaned.append(note)

    return cleaned


def _chord_notes(c: MusicalConstraints, root: int) -> list[ComposedMidiNote]:
    """Dub Chord Stabs: offbeat stabs with delay echo repeats, minor 7th voicing, 16-bar density."""
    notes: list[ComposedMidiNote] = []
    swing = getattr(c, "swing", 0.5)
    max_beats = c.bars * 4.0
    chord_intervals = (0, 3, 7, 10) if c.key.endswith("m") else (0, 4, 7, 11)

    for bar in range(c.bars):
        base = bar * 4.0
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

            for interval in chord_intervals:
                notes.append(
                    ComposedMidiNote(
                        root + interval,
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
        notes.append(ComposedMidiNote(pitch, round(start, 6), duration, _velocity(c, rng), 0))
    return notes
