"""Provider-neutral requests and notes for role-scoped MIDI composition."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Self

from kihachi_mcp.services.session_pattern_builder import MidiNote

MIDI_ROLES = frozenset({"drums", "bass", "chords", "melody", "arp", "percussion"})
COMPOSITION_OPERATIONS = frozenset({"generate", "variation", "revision"})
GRIDS = frozenset({"1/4", "1/8", "1/16", "1/32"})


@dataclass(frozen=True)
class MusicalConstraints:
    key: str
    tempo: int
    bars: int
    role: str
    grid: str = "1/16"
    syncopation: float = 0.5
    octave_jump: float = 0.0
    ghost_notes: bool = False
    velocity_min: int = 65
    velocity_max: int = 118
    note_length_min: float = 0.25
    note_length_max: float = 0.75
    avoid_kick_collision: bool = False
    variation_bar: int | None = None
    kick_onsets: tuple[float, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if self.role not in MIDI_ROLES:
            raise ValueError(f"unsupported MIDI role '{self.role}'")
        if not 40 <= self.tempo <= 240:
            raise ValueError("tempo must be between 40 and 240")
        if not 1 <= self.bars <= 256:
            raise ValueError("bars must be between 1 and 256")
        if self.grid not in GRIDS:
            raise ValueError("grid must be 1/4, 1/8, 1/16 or 1/32")
        if not 0 <= self.syncopation <= 1 or not 0 <= self.octave_jump <= 1:
            raise ValueError("syncopation and octave_jump must be between 0 and 1")
        if not 1 <= self.velocity_min <= self.velocity_max <= 127:
            raise ValueError("velocity range is invalid")
        if not 0 < self.note_length_min <= self.note_length_max:
            raise ValueError("note length range is invalid")
        if self.variation_bar is not None and not 1 <= self.variation_bar <= self.bars:
            raise ValueError("variation_bar is outside the clip")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        velocity = data.get("velocity_range") or ()
        note_length = data.get("note_length_range") or ()
        return cls(
            key=str(data.get("key") or "Cm"),
            tempo=int(data.get("tempo") or 120),
            bars=int(data.get("bars") or 1),
            role=str(data.get("role") or ""),
            grid=str(data.get("grid") or "1/16"),
            syncopation=float(data.get("syncopation") or 0),
            octave_jump=float(data.get("octave_jump") or 0),
            ghost_notes=bool(data.get("ghost_notes")),
            velocity_min=int(data.get("velocity_min") or (velocity[0] if len(velocity) == 2 else 65)),
            velocity_max=int(data.get("velocity_max") or (velocity[1] if len(velocity) == 2 else 118)),
            note_length_min=float(data.get("note_length_min") or (note_length[0] if len(note_length) == 2 else 0.25)),
            note_length_max=float(data.get("note_length_max") or (note_length[1] if len(note_length) == 2 else 0.75)),
            avoid_kick_collision=bool(data.get("avoid_kick_collision")),
            variation_bar=int(data["variation_bar"]) if data.get("variation_bar") is not None else None,
            kick_onsets=tuple(float(value) for value in data.get("kick_onsets") or ()),
        )


@dataclass(frozen=True)
class ComposedMidiNote:
    pitch: int
    start: float
    duration: float
    velocity: int
    channel: int = 0
    probability: float | None = None
    expression: dict[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not 0 <= self.pitch <= 127 or not 1 <= self.velocity <= 127:
            raise ValueError("MIDI pitch or velocity is invalid")
        if self.start < 0 or self.duration <= 0 or not 0 <= self.channel <= 15:
            raise ValueError("MIDI timing or channel is invalid")
        if self.probability is not None and not 0 <= self.probability <= 1:
            raise ValueError("probability must be between 0 and 1")

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        return {key: value for key, value in data.items() if value not in (None, {})}

    def to_live_note(self) -> MidiNote:
        """Drop optional expression fields at the current Live contract boundary."""
        return MidiNote(self.pitch, self.start, self.duration, self.velocity)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        return cls(
            pitch=int(data.get("pitch") or 0),
            start=float(data.get("start") or 0),
            duration=float(data.get("duration") or 0),
            velocity=int(data.get("velocity") or 0),
            channel=int(data.get("channel") or 0),
            probability=float(data["probability"]) if data.get("probability") is not None else None,
            expression={str(key): float(value) for key, value in (data.get("expression") or {}).items()},
        )


@dataclass(frozen=True)
class MidiCompositionRequest:
    constraints: MusicalConstraints
    operation: str = "generate"
    source_notes: tuple[ComposedMidiNote, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if self.operation not in COMPOSITION_OPERATIONS:
            raise ValueError(f"unsupported composition operation '{self.operation}'")
        if self.operation != "generate" and not self.source_notes:
            raise ValueError("variation and revision require source_notes")

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation": self.operation,
            "constraints": self.constraints.to_dict(),
            "source_notes": [note.to_dict() for note in self.source_notes],
        }
