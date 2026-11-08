"""Provider-neutral requests and notes for role-scoped MIDI composition."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Self

from kihachi_mcp.models.production_brief import PART_ORDER
from kihachi_mcp.services.session_pattern_builder import MidiNote

ROLE_ALL = "全パート"
MIDI_ROLES = frozenset({"drums", "bass", "chords", "melody", "arp", "percussion", *PART_ORDER, ROLE_ALL})
#: Studio part names use the same note engines as the six composer roles.
COMPOSITION_ENGINE = {
    "Kick": "drums",
    "Snare": "drums",
    "Hats": "drums",
    "OpenHat": "drums",
    "Perc": "percussion",
    "FX": "percussion",
    "Sub": "bass",
    "Bass": "bass",
    "Stab": "chords",
    "Pad": "chords",
    "Guitar": "chords",
    "Horn": "melody",
    "Arp": "arp",
    "Lead": "melody",
    "Vocal": "melody",
}
PART_PITCH = {"Kick": 36, "Snare": 38, "Hats": 42, "OpenHat": 46}
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
    genre: str = ""
    mood: str = ""
    prompt: str = ""
    swing: float = 0.5
    mpc_groove: bool = False
    triplet_rolls: bool = False
    #: Chord symbol per bar of this clip ("" = no chord specified for that
    #: bar). Empty tuple means the clip carries no progression at all, in
    #: which case generation behaves exactly as before this field existed.
    chords_by_bar: tuple[str, ...] = field(default_factory=tuple)
    #: Section this clip belongs to (e.g. "Drop"), when the caller knows it.
    section_name: str = ""
    #: Section energy 0..1, when known. Context for providers; deterministic
    #: generators do not read it yet.
    section_energy: float | None = None

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
        if not 0 <= self.swing <= 1:
            raise ValueError("swing must be between 0 and 1")
        if not 1 <= self.velocity_min <= self.velocity_max <= 127:
            raise ValueError("velocity range is invalid")
        if not 0 < self.note_length_min <= self.note_length_max:
            raise ValueError("note length range is invalid")
        if self.variation_bar is not None and not 1 <= self.variation_bar <= self.bars:
            raise ValueError("variation_bar is outside the clip")
        object.__setattr__(self, "genre", str(self.genre or "").strip()[:80])
        object.__setattr__(self, "mood", str(self.mood or "").strip()[:80])
        object.__setattr__(self, "prompt", str(self.prompt or "").strip()[:500])
        chords = tuple(str(chord or "").strip()[:24] for chord in self.chords_by_bar)
        if chords and len(chords) != self.bars:
            raise ValueError("chords_by_bar must name one chord per bar of the clip")
        object.__setattr__(self, "chords_by_bar", chords)
        object.__setattr__(self, "section_name", str(self.section_name or "").strip()[:80])
        if self.section_energy is not None and not 0 <= self.section_energy <= 1:
            raise ValueError("section_energy must be between 0 and 1")

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        for key in ("genre", "mood", "prompt"):
            if not data.get(key):
                data.pop(key, None)
        if not data.get("chords_by_bar"):
            data.pop("chords_by_bar", None)
        if not data.get("section_name"):
            data.pop("section_name", None)
        if data.get("section_energy") is None:
            data.pop("section_energy", None)
        return data

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
            genre=str(data.get("genre") or ""),
            mood=str(data.get("mood") or ""),
            prompt=str(data.get("prompt") or ""),
            swing=float(data.get("swing") if data.get("swing") is not None else 0.5),
            mpc_groove=bool(data.get("mpc_groove")),
            triplet_rolls=bool(data.get("triplet_rolls")),
            chords_by_bar=tuple(str(chord or "") for chord in data.get("chords_by_bar") or ()),
            section_name=str(data.get("section_name") or ""),
            section_energy=(
                float(data["section_energy"]) if data.get("section_energy") is not None else None
            ),
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
class ContextLongNote:
    """One long note another part already plays, worth not doubling."""

    start: float
    pitch: int
    duration: float

    def to_dict(self) -> dict[str, Any]:
        return {"start": self.start, "pitch": self.pitch, "duration": self.duration}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        return cls(
            start=float(data.get("start") or 0),
            pitch=int(data.get("pitch") or 0),
            duration=float(data.get("duration") or 0),
        )


@dataclass(frozen=True)
class ContextPart:
    """Summary of one part that already plays in the target section.

    Cross-part context (Phase W): the composer sees what the other parts
    in the same section are doing — pitch set for pitched parts, onset
    positions, and long notes — so a new part can fit with them instead
    of improvising alone against the design alone. Drum parts carry no
    pitch set: their pitches are fixed and musically uninformative here.
    """

    part: str
    note_count: int
    pitches: tuple[int, ...] = ()
    onsets: tuple[float, ...] = ()
    long_notes: tuple[ContextLongNote, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "part": self.part,
            "note_count": self.note_count,
            "pitches": list(self.pitches),
            "onsets": list(self.onsets),
            "long_notes": [note.to_dict() for note in self.long_notes],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        return cls(
            part=str(data.get("part") or ""),
            note_count=int(data.get("note_count") or 0),
            pitches=tuple(int(value) for value in data.get("pitches") or ()),
            onsets=tuple(float(value) for value in data.get("onsets") or ()),
            long_notes=tuple(
                ContextLongNote.from_dict(item)
                for item in data.get("long_notes") or ()
                if isinstance(item, dict)
            ),
        )


@dataclass(frozen=True)
class MidiCompositionRequest:
    constraints: MusicalConstraints
    operation: str = "generate"
    source_notes: tuple[ComposedMidiNote, ...] = field(default_factory=tuple)
    #: Other parts already playing in this clip's section, when the caller
    #: can supply them. Empty means no cross-part context, in which case
    #: generation behaves exactly as before this field existed.
    context_parts: tuple[ContextPart, ...] = ()

    def __post_init__(self) -> None:
        if self.operation not in COMPOSITION_OPERATIONS:
            raise ValueError(f"unsupported composition operation '{self.operation}'")
        if self.operation != "generate" and not self.source_notes:
            raise ValueError("variation and revision require source_notes")

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "operation": self.operation,
            "constraints": self.constraints.to_dict(),
            "source_notes": [note.to_dict() for note in self.source_notes],
        }
        if self.context_parts:
            data["context_parts"] = [part.to_dict() for part in self.context_parts]
        return data
