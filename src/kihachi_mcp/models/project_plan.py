from dataclasses import asdict, dataclass
from typing import Any, Self

from kihachi_mcp.models.track import TrackSpec


@dataclass(frozen=True)
class ProjectPlan:
    """Project layout generated from a SongSpec."""

    project_name: str
    genre: str
    tempo: int
    key: str
    length_minutes: float
    bars: int
    tracks: list[TrackSpec]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        """Create a ProjectPlan from a JSON-compatible dict."""
        return cls(
            project_name=str(data.get("project_name") or "Untitled"),
            genre=str(data.get("genre") or ""),
            tempo=int(data.get("tempo") or 0),
            key=str(data.get("key") or ""),
            length_minutes=float(data.get("length_minutes") or 0),
            bars=max(1, int(data.get("bars") or 1)),
            tracks=[
                TrackSpec.from_dict(track)
                for track in data.get("tracks") or []
                if isinstance(track, dict)
            ],
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize this ProjectPlan to a JSON-compatible dict."""
        return asdict(self)
