from dataclasses import asdict, dataclass
from typing import Any, Self


@dataclass(frozen=True)
class MidiClipPlan:
    """A deterministic MIDI clip placement for a song section."""

    track_name: str
    section_name: str
    start_bar: int
    length_bars: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MidiPlan:
    """JSON-safe MIDI clip plan for a future Ableton adapter."""

    tempo: int
    bars: int
    clips: list[MidiClipPlan]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        return cls(
            tempo=int(data.get("tempo") or 0),
            bars=max(1, int(data.get("bars") or 1)),
            clips=[
                MidiClipPlan(
                    track_name=str(clip.get("track_name") or ""),
                    section_name=str(clip.get("section_name") or ""),
                    start_bar=int(clip.get("start_bar") or 0),
                    length_bars=max(1, int(clip.get("length_bars") or 1)),
                )
                for clip in data.get("clips") or []
                if isinstance(clip, dict)
            ],
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
