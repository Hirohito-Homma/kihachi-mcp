"""Playable limits a composer must obey for one instrument.

This is not a Live device map. Articulation numbers are musical labels until a
real instrument confirms them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Self

INSTRUMENT_SCHEMA = "kihachi.instrument_spec.v1"


@dataclass(frozen=True)
class PitchRange:
    """Inclusive MIDI pitch bounds."""

    low: int
    high: int

    def contains(self, pitch: int) -> bool:
        return self.low <= pitch <= self.high

    def to_dict(self) -> dict[str, int]:
        return {"min": self.low, "max": self.high}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        return cls(low=int(data.get("min") or 0), high=int(data.get("max") or 0))


@dataclass(frozen=True)
class InstrumentSpec:
    """Constraints for composing MIDI that can be played on one instrument."""

    instrument_id: str
    pitch_range: PitchRange
    preferred_range: PitchRange
    articulations: dict[str, int]
    velocity: dict[str, tuple[int, int]]
    max_polyphony: int = 1
    max_leap: int = 12
    ghost_note_probability: float = 0.0
    octave_probability: float = 0.0
    schema: str = INSTRUMENT_SCHEMA

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "instrument_id": self.instrument_id,
            "range": self.pitch_range.to_dict(),
            "preferred_range": self.preferred_range.to_dict(),
            "articulations": dict(self.articulations),
            "velocity": {name: [low, high] for name, (low, high) in self.velocity.items()},
            "rules": {
                "max_polyphony": self.max_polyphony,
                "max_leap": self.max_leap,
                "ghost_note_probability": self.ghost_note_probability,
                "octave_probability": self.octave_probability,
            },
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        if data.get("schema") not in (None, INSTRUMENT_SCHEMA):
            raise ValueError(f"unsupported instrument schema '{data.get('schema')}'")
        rules = data.get("rules") if isinstance(data.get("rules"), dict) else {}
        velocity = {
            str(name): (int(pair[0]), int(pair[1]))
            for name, pair in (data.get("velocity") or {}).items()
            if isinstance(pair, (list, tuple)) and len(pair) == 2
        }
        return cls(
            instrument_id=str(data.get("instrument_id") or ""),
            pitch_range=PitchRange.from_dict(data.get("range") or {}),
            preferred_range=PitchRange.from_dict(data.get("preferred_range") or {}),
            articulations={str(name): int(value) for name, value in (data.get("articulations") or {}).items()},
            velocity=velocity,
            max_polyphony=int(rules.get("max_polyphony") or 1),
            max_leap=int(rules.get("max_leap") or 12),
            ghost_note_probability=float(rules.get("ghost_note_probability") or 0),
            octave_probability=float(rules.get("octave_probability") or 0),
        )


def slap_bass_spec() -> InstrumentSpec:
    """Return the KIHACHI slap-bass limits used by the first Astra slice."""
    return InstrumentSpec(
        instrument_id="kihachi_slap_bass",
        pitch_range=PitchRange(28, 64),
        preferred_range=PitchRange(32, 52),
        articulations={"normal": 1, "slap": 2, "pop": 3, "ghost": 4, "mute": 5},
        velocity={"ghost": (35, 65), "normal": (70, 100), "accent": (105, 127)},
        max_polyphony=1,
        max_leap=12,
        ghost_note_probability=0.18,
        octave_probability=0.12,
    )
