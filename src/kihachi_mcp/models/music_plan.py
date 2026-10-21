"""Musical plan produced by a composer. It contains no Live operations."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Self

MUSIC_PLAN_SCHEMA = "kihachi.music_plan.v1"


@dataclass(frozen=True)
class GenerationScope:
    """The only tracks and bars a plan is allowed to describe."""

    tracks: tuple[str, ...]
    start_bar: int = 1
    length_bars: int = 1
    preserve: tuple[str, ...] = ()
    numerator: int = 4
    denominator: int = 4

    def to_dict(self) -> dict[str, Any]:
        return {
            "tracks": list(self.tracks),
            "range": {"start_bar": self.start_bar, "length_bars": self.length_bars},
            "preserve": list(self.preserve),
            "time_signature": [self.numerator, self.denominator],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        window = data.get("range") if isinstance(data.get("range"), dict) else {}
        meter = data.get("time_signature") or [4, 4]
        return cls(
            tracks=tuple(str(item) for item in data.get("tracks") or ()),
            start_bar=int(window.get("start_bar") or data.get("start_bar") or 1),
            length_bars=int(window.get("length_bars") or data.get("length_bars") or 1),
            preserve=tuple(str(item) for item in data.get("preserve") or ()),
            numerator=int(meter[0]),
            denominator=int(meter[1]),
        )


@dataclass(frozen=True)
class MusicNote:
    pitch: int
    start: float
    duration: float
    velocity: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "pitch": self.pitch,
            "start": self.start,
            "duration": self.duration,
            "velocity": self.velocity,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        return cls(
            pitch=int(data.get("pitch") or 0),
            start=float(data.get("start") or 0),
            duration=float(data.get("duration") or 0),
            velocity=int(data.get("velocity") or 0),
        )


@dataclass(frozen=True)
class MusicClip:
    start_bar: int
    length_bars: int
    notes: tuple[MusicNote, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "start_bar": self.start_bar,
            "length_bars": self.length_bars,
            "notes": [note.to_dict() for note in self.notes],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        return cls(
            start_bar=int(data.get("start_bar") or 1),
            length_bars=int(data.get("length_bars") or 1),
            notes=tuple(
                MusicNote.from_dict(item)
                for item in data.get("notes") or ()
                if isinstance(item, dict)
            ),
        )


@dataclass(frozen=True)
class MusicTrack:
    id: str
    name: str
    type: str = "midi"
    clips: tuple[MusicClip, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "type": self.type,
            "clips": [clip.to_dict() for clip in self.clips],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        return cls(
            id=str(data.get("id") or ""),
            name=str(data.get("name") or ""),
            type=str(data.get("type") or "midi"),
            clips=tuple(
                MusicClip.from_dict(item)
                for item in data.get("clips") or ()
                if isinstance(item, dict)
            ),
        )


@dataclass(frozen=True)
class MusicPlan:
    """Editable musical candidate. Committing it requires a separate Live plan."""

    tempo: int
    key: str
    bars: int
    tracks: tuple[MusicTrack, ...]
    scope: GenerationScope
    numerator: int = 4
    denominator: int = 4
    schema: str = MUSIC_PLAN_SCHEMA
    warnings: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "tempo": self.tempo,
            "key": self.key,
            "time_signature": [self.numerator, self.denominator],
            "bars": self.bars,
            "tracks": [track.to_dict() for track in self.tracks],
            "scope": self.scope.to_dict(),
            "warnings": list(self.warnings),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        meter = data.get("time_signature") or [4, 4]
        scope = data.get("scope") if isinstance(data.get("scope"), dict) else {}
        return cls(
            tempo=int(data.get("tempo") or 0),
            key=str(data.get("key") or ""),
            bars=int(data.get("bars") or 0),
            tracks=tuple(
                MusicTrack.from_dict(item)
                for item in data.get("tracks") or ()
                if isinstance(item, dict)
            ),
            scope=GenerationScope.from_dict(scope),
            numerator=int(meter[0]),
            denominator=int(meter[1]),
            schema=str(data.get("schema") or ""),
            warnings=tuple(str(item) for item in data.get("warnings") or ()),
        )
