from dataclasses import asdict, dataclass
from typing import Any, Self


@dataclass(frozen=True)
class TrackSpec:
    """A project track with a name, type, and color."""

    name: str
    type: str
    color: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        """Create a TrackSpec from a JSON-compatible dict."""
        return cls(
            name=str(data.get("name") or ""),
            type=str(data.get("type") or "MIDI"),
            color=str(data.get("color") or "Gray"),
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize this track to a JSON-compatible dict."""
        return asdict(self)
