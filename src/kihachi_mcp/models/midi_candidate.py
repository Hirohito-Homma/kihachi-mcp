"""Reviewable MIDI candidates that are the single source for preview and Live."""

from dataclasses import dataclass
from typing import Any, Self

from kihachi_mcp.models.live_contract import canonical_hash
from kihachi_mcp.models.production_brief import (
    OPTIONAL_STUDIO_PARTS,
    PART_ORDER,
    STUDIO_PARTS,
    ProductionBrief,
)
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
        if self.part not in (*STUDIO_PARTS, *OPTIONAL_STUDIO_PARTS):
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
        if not set(STUDIO_PARTS) <= parts or not parts <= {
            *STUDIO_PARTS,
            *OPTIONAL_STUDIO_PARTS,
        }:
            raise ValueError("a candidate must include Kick, Hats, Bass, and Stab")

    @property
    def parts(self) -> tuple[str, ...]:
        """Present parts in stable track order, including optional new voices."""
        present = {clip.part for clip in self.clips}
        return tuple(part for part in PART_ORDER if part in present)

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

    @property
    def content_hash(self) -> str:
        """Return a hash over all SongSpec metadata, Arrangement sections, and MIDI clips."""
        brief = self.brief
        return canonical_hash(
            {
                "candidate_id": self.candidate_id,
                "seed": self.seed,
                "songspec": {
                    "tempo": brief.tempo.value,
                    "key": brief.key.value,
                    "genre": brief.genre.value,
                    "bars": brief.bars.value,
                    "mood": brief.mood.value,
                    "time_signature": f"{brief.meter_numerator}/{brief.meter_denominator}",
                    "meter_numerator": brief.meter_numerator,
                    "meter_denominator": brief.meter_denominator,
                    "beats_per_bar": brief.beats_per_bar,
                },
                "arrangement": [
                    {
                        "name": s.name,
                        "start_bar": s.start_bar,
                        "length_bars": s.length_bars,
                        "end_bar": s.end_bar,
                    }
                    for s in brief.sections
                ],
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
            "content_hash": self.content_hash,
            "brief": self.brief.to_dict(),
            "clips": [clip.to_dict() for clip in self.clips],
            "note_counts": {
                "total": self.note_count(),
                **{part: self.note_count(part) for part in self.parts},
            },
            "used_pitches": {
                part: list(self.used_pitches(part)) for part in self.parts
            },
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
