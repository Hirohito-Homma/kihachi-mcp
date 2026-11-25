"""Reviewable MIDI candidates that are the single source for preview and Live."""

from dataclasses import dataclass, field
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
        if not str(self.part).strip():
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
    # SongSpec.elements の引き継ぎ（空時は to_dict() に出力せず後方互換を維持）
    elements: dict[str, dict[str, Any]] = field(default_factory=dict)
    # arrangement.mix の引き継ぎ（空時は to_dict() に出力せず後方互換を維持）
    mix: dict[str, float] = field(default_factory=dict)
    # arrangement.sound_variants 由来の変容先パート→親パート対応表（空時は出力しない）
    variant_parents: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.candidate_id:
            raise ValueError("candidate_id must not be empty")
        parts = {clip.part for clip in self.clips}
        covered_parts = parts | {
            self.variant_parents[part] for part in parts if part in self.variant_parents
        }
        allowed_parts = {
            *STUDIO_PARTS,
            *OPTIONAL_STUDIO_PARTS,
            *self.variant_parents.keys(),
        }
        if not set(STUDIO_PARTS) <= covered_parts or not parts <= allowed_parts:
            raise ValueError("a candidate must include Kick, Hats, Bass, and Stab")

    @property
    def parts(self) -> tuple[str, ...]:
        """Present parts in stable track order, including optional new voices."""
        present = {clip.part for clip in self.clips}
        ordered: list[str] = []
        children_by_parent: dict[str, list[str]] = {}
        for variant_part, parent_part in self.variant_parents.items():
            if variant_part in present and variant_part not in PART_ORDER:
                children_by_parent.setdefault(parent_part, []).append(variant_part)
        for part in PART_ORDER:
            if part in present:
                ordered.append(part)
            ordered.extend(children_by_parent.get(part, ()))
        ordered.extend(sorted(p for p in present if p not in ordered))
        return tuple(ordered)

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
        data: dict[str, Any] = {
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
        if self.elements:
            data["elements"] = {k: dict(v) for k, v in self.elements.items()}
        if self.mix:
            data["mix"] = {str(k): float(v) for k, v in self.mix.items()}
        if self.variant_parents:
            data["variant_parents"] = {
                str(k): str(v) for k, v in self.variant_parents.items()
            }
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        """Create a candidate from JSON-compatible data."""
        brief_data = data.get("brief")
        if not isinstance(brief_data, dict):
            raise TypeError("candidate brief is missing")
        raw_elements = data.get("elements")
        raw_mix = data.get("mix")
        raw_variants = data.get("variant_parents")
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
            elements=(
                {str(k).strip().lower(): dict(v) for k, v in raw_elements.items() if isinstance(v, dict)}
                if isinstance(raw_elements, dict)
                else {}
            ),
            mix=(
                {
                    str(k).strip(): float(v)
                    for k, v in raw_mix.items()
                    if str(k).strip()
                    and not isinstance(v, bool)
                    and isinstance(v, (int, float))
                }
                if isinstance(raw_mix, dict)
                else {}
            ),
            variant_parents=(
                {
                    str(k).strip(): str(v).strip()
                    for k, v in raw_variants.items()
                    if str(k).strip() and str(v).strip()
                }
                if isinstance(raw_variants, dict)
                else {}
            ),
        )
