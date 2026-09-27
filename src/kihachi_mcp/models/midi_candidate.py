"""Reviewable MIDI candidates that are the single source for preview and Live."""

from dataclasses import dataclass
from typing import Any, Self

from kihachi_mcp.models.live_contract import canonical_hash
from kihachi_mcp.models.production_brief import STUDIO_PARTS, ProductionBrief
from kihachi_mcp.services.session_pattern_builder import MidiNote


@dataclass(frozen=True)
class CandidateClip:
    """One part's notes for one section, stored relative to the clip start."""

    part: str
    section_name: str
    start_bar: int
    length_bars: int
    notes: tuple[MidiNote, ...]

    def __post_init__(self) -> None:
        if self.part not in STUDIO_PARTS:
            raise ValueError(f"unsupported part '{self.part}'")
        if self.start_bar < 1 or self.length_bars < 1:
            raise ValueError("clip bar range is invalid")

    def to_dict(self) -> dict[str, Any]:
        """Serialize the clip, including the note payload Live will receive."""
        return {
            "part": self.part,
            "track_name": self.part,
            "section_name": self.section_name,
            "start_bar": self.start_bar,
            "length_bars": self.length_bars,
            "note_count": len(self.notes),
            "notes": [note.to_dict() for note in self.notes],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        """Create a clip from JSON-compatible data."""
        notes = []
        for item in data.get("notes") or []:
            if not isinstance(item, dict):
                continue
            notes.append(
                MidiNote(
                    pitch=int(item.get("pitch") or 0),
                    start_beats=float(item.get("start") or 0),
                    duration_beats=float(item.get("duration") or 0),
                    velocity=int(item.get("velocity") or 0),
                )
            )
        return cls(
            part=str(data.get("part") or data.get("track_name") or ""),
            section_name=str(data.get("section_name") or ""),
            start_bar=int(data.get("start_bar") or 0),
            length_bars=int(data.get("length_bars") or 0),
            notes=tuple(notes),
        )


@dataclass(frozen=True)
class MidiCandidate:
    """One reproducible MIDI realisation of a production brief."""

    candidate_id: str
    seed: int
    brief: ProductionBrief
    clips: tuple[CandidateClip, ...]
    parent_candidate_id: str = ""

    def __post_init__(self) -> None:
        if not self.candidate_id:
            raise ValueError("candidate_id must not be empty")
        parts = {clip.part for clip in self.clips}
        if parts != set(STUDIO_PARTS):
            raise ValueError("a candidate must include Kick, Hats, Bass, and Stab")

    @property
    def note_fingerprint(self) -> str:
        """Return a hash over the exact notes this candidate will apply."""
        return canonical_hash(
            {
                "candidate_id": self.candidate_id,
                "seed": self.seed,
                "clips": [clip.to_dict() for clip in self.clips],
            }
        )

    def clips_for_part(self, part: str) -> list[CandidateClip]:
        """Return section clips for one part in bar order."""
        return [clip for clip in self.clips if clip.part == part]

    def note_count(self, part: str | None = None) -> int:
        """Return how many notes the candidate holds, optionally per part."""
        return sum(
            len(clip.notes) for clip in self.clips if part is None or clip.part == part
        )

    def used_pitches(self, part: str) -> tuple[int, ...]:
        """Return the distinct MIDI pitches written for one part."""
        pitches = {
            note.pitch for clip in self.clips_for_part(part) for note in clip.notes
        }
        return tuple(sorted(pitches))

    def to_dict(self) -> dict[str, Any]:
        """Serialize the candidate for the UI and apply confirmation."""
        return {
            "candidate_id": self.candidate_id,
            "seed": self.seed,
            "parent_candidate_id": self.parent_candidate_id,
            "note_fingerprint": self.note_fingerprint,
            "brief": self.brief.to_dict(),
            "clips": [clip.to_dict() for clip in self.clips],
            "note_counts": {
                "total": self.note_count(),
                **{part: self.note_count(part) for part in STUDIO_PARTS},
            },
            "used_pitches": {part: list(self.used_pitches(part)) for part in STUDIO_PARTS},
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        """Create a candidate from JSON-compatible data."""
        brief_data = data.get("brief")
        if not isinstance(brief_data, dict):
            raise TypeError("candidate brief is missing")
        return cls(
            candidate_id=str(data.get("candidate_id") or ""),
            seed=int(data.get("seed") or 0),
            brief=ProductionBrief.from_dict(brief_data),
            clips=tuple(
                CandidateClip.from_dict(item)
                for item in data.get("clips") or []
                if isinstance(item, dict)
            ),
            parent_candidate_id=str(data.get("parent_candidate_id") or ""),
        )
