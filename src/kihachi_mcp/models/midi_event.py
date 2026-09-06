from dataclasses import asdict, dataclass
from typing import Any, Self


@dataclass(frozen=True)
class MidiEvent:
    """A JSON-safe MIDI note intent within a project bar grid."""

    track_name: str
    start_bar: int
    duration_bars: int
    pitch: int
    velocity: int

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        """Create an event from JSON-compatible data."""
        return cls(
            track_name=str(data.get("track_name") or ""),
            start_bar=int(data.get("start_bar") or 0),
            duration_bars=int(data.get("duration_bars") or 0),
            pitch=int(data.get("pitch") or 0),
            velocity=int(data.get("velocity") or 0),
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize the event to JSON-compatible data."""
        return asdict(self)

    def validate(self, project_bars: int) -> list[str]:
        """Return validation errors without mutating the event."""
        errors: list[str] = []
        if not self.track_name:
            errors.append("track_name must not be empty")
        if self.start_bar < 1:
            errors.append("start_bar must be at least 1")
        if self.duration_bars < 1:
            errors.append("duration_bars must be at least 1")
        if not 0 <= self.pitch <= 127:
            errors.append("pitch must be between 0 and 127")
        if not 1 <= self.velocity <= 127:
            errors.append("velocity must be between 1 and 127")
        if self.start_bar + self.duration_bars - 1 > project_bars:
            errors.append("event is outside the project bars")
        return errors
