"""Deterministic and provider-backed MIDI composers behind one contract."""

from __future__ import annotations

import json
from random import Random
from typing import Any, Protocol

from kihachi_mcp.models.midi_composer import (
    ComposedMidiNote,
    MidiCompositionRequest,
    MusicalConstraints,
)
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
        rng = Random(f"{self._seed}:{json.dumps(c.to_dict(), sort_keys=True)}")
        if c.role in {"drums", "percussion"}:
            return _rhythm_notes(c, rng)
        root = root_pitch(c.key, octave_offset={"bass": 0, "chords": 2, "melody": 2, "arp": 2}[c.role])
        if c.role == "bass":
            return _bass_notes(c, root, rng)
        if c.role == "chords":
            return _chord_notes(c, root)
        return _melodic_notes(c, root, rng, arp=c.role == "arp")

    def _revise(self, request: MidiCompositionRequest) -> list[ComposedMidiNote]:
        c = request.constraints
        notes: list[ComposedMidiNote] = []
        kick = {round(value, 6) for value in c.kick_onsets}
        grid = _grid_beats(c.grid)
        for index, note in enumerate(request.source_notes):
            start = note.start
            velocity = note.velocity
            if c.role == "bass" and index % 2 == 1 and c.syncopation >= 0.5:
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
        prompt = (
            "Compose only the requested MIDI role. Obey every musical constraint. "
            "For variation or revision, preserve unaffected source notes and return the complete target note list. "
            "Do not return prose. Request:\n" + json.dumps(request.to_dict(), ensure_ascii=False)
        )
        payload = self._provider.generate_structured(prompt, midi_composition_schema())
        notes = tuple(ComposedMidiNote.from_dict(item) for item in payload.get("notes") or [])
        _validate_result(notes, request.constraints)
        return notes


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


def _validate_result(notes: tuple[ComposedMidiNote, ...], c: MusicalConstraints) -> None:
    if not notes:
        raise ValueError("MIDI Composerがノートを返しませんでした")
    end = c.bars * 4
    if any(note.start + note.duration > end + 1e-6 for note in notes):
        raise ValueError("MIDI Composerのノートが指定小節を超えています")
    if any(not c.velocity_min <= note.velocity <= c.velocity_max for note in notes):
        raise ValueError("MIDI Composerのvelocityが制約外です")


def _grid_beats(grid: str) -> float:
    return {"1/4": 1.0, "1/8": 0.5, "1/16": 0.25, "1/32": 0.125}[grid]


def _velocity(c: MusicalConstraints, rng: Random, ghost: bool = False) -> int:
    if ghost and c.ghost_notes:
        return max(c.velocity_min, min(c.velocity_max, c.velocity_min + 4))
    return rng.randint(max(c.velocity_min, c.velocity_max - 18), c.velocity_max)


def _rhythm_notes(c: MusicalConstraints, rng: Random) -> list[ComposedMidiNote]:
    notes: list[ComposedMidiNote] = []
    for bar in range(c.bars):
        base = bar * 4.0
        if c.role == "drums":
            for beat in range(4):
                notes.append(ComposedMidiNote(36, base + beat, 0.125, _velocity(c, rng), 9))
            for offset in (1.0, 3.0):
                notes.append(ComposedMidiNote(39, base + offset, 0.125, _velocity(c, rng), 9))
            for step in range(8):
                notes.append(ComposedMidiNote(42, base + step * 0.5, 0.1, _velocity(c, rng, step % 2 == 0), 9))
        else:
            for offset in (0.75, 1.5, 2.75, 3.5):
                notes.append(ComposedMidiNote(70 if int(offset * 2) % 2 else 71, base + offset, 0.12, _velocity(c, rng), 9))
    return notes


def _bass_notes(c: MusicalConstraints, root: int, rng: Random) -> list[ComposedMidiNote]:
    notes: list[ComposedMidiNote] = []
    kick = {round(value, 6) for value in c.kick_onsets}
    offsets = (0.5, 1.5, 2.5, 3.5) if c.syncopation >= 0.5 else (0.0, 1.0, 2.0, 3.0)
    for bar in range(c.bars):
        for index, offset in enumerate(offsets):
            start = bar * 4 + offset
            if c.avoid_kick_collision and round(start, 6) in kick:
                continue
            jump = 12 if c.octave_jump > 0 and rng.random() < c.octave_jump else 0
            length = min(c.note_length_max, max(c.note_length_min, 0.4))
            notes.append(ComposedMidiNote(root + jump, start, length, _velocity(c, rng, index % 4 == 3), 0))
    return notes


def _chord_notes(c: MusicalConstraints, root: int) -> list[ComposedMidiNote]:
    intervals = (0, 3, 7) if c.key.endswith("m") else (0, 4, 7)
    notes = []
    for bar in range(c.bars):
        start = bar * 4.0
        for interval in intervals:
            notes.append(ComposedMidiNote(root + interval, start, min(3.8, c.note_length_max), c.velocity_max, 0))
    return notes


def _melodic_notes(c: MusicalConstraints, root: int, rng: Random, arp: bool) -> list[ComposedMidiNote]:
    scale = (0, 2, 3, 5, 7, 8, 10) if c.key.endswith("m") else (0, 2, 4, 5, 7, 9, 11)
    step = _grid_beats(c.grid) if arp else max(0.5, _grid_beats(c.grid) * 2)
    notes = []
    count = int(c.bars * 4 / step)
    for index in range(count):
        pitch = root + scale[(index * 2 if arp else rng.randrange(len(scale))) % len(scale)]
        notes.append(ComposedMidiNote(pitch, round(index * step, 6), min(step * 0.8, c.note_length_max), _velocity(c, rng), 0))
    return notes
