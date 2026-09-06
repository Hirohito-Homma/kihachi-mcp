from dataclasses import asdict, dataclass
from typing import Any, Self

from kihachi_mcp.models.arrangement import Arrangement


@dataclass(frozen=True)
class GenreTemplate:
    """External genre knowledge used to build a SongSpec."""

    name: str
    default_bpm: int
    default_key: str
    tracks: list[str]
    arrangement: list[Arrangement]
    mood: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        """Create a GenreTemplate from a JSON-compatible dict."""
        return cls(
            name=str(data.get("name") or ""),
            default_bpm=int(data.get("default_bpm") or 0),
            default_key=str(data.get("default_key") or ""),
            tracks=[str(name) for name in data.get("tracks") or []],
            arrangement=[
                Arrangement.from_dict(section)
                for section in data.get("arrangement") or []
                if isinstance(section, dict)
            ],
            mood=str(data.get("mood") or ""),
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize this genre template to a JSON-compatible dict."""
        return asdict(self)
