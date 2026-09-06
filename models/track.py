from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class TrackSpec:
    """A project track with a name, type, and color."""

    name: str
    type: str
    color: str

    def to_dict(self) -> dict[str, Any]:
        """Serialize this track to a JSON-compatible dict."""
        return asdict(self)
