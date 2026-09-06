from dataclasses import asdict, dataclass
from typing import Any

from models.track import TrackSpec


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

    def to_dict(self) -> dict[str, Any]:
        """Serialize this ProjectPlan to a JSON-compatible dict."""
        return asdict(self)
