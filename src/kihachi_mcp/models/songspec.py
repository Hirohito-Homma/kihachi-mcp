from dataclasses import asdict, dataclass
from typing import Any, Self


@dataclass(frozen=True)
class SongSpec:
    """Musical specification used to build a KIHACHI project."""

    genre: str
    tempo: int
    key: str
    length_minutes: float
    bars: int
    tracks: list[str]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        """Create a SongSpec from a JSON-compatible dict."""
        length_minutes = float(data.get("length_minutes") or 0)
        raw_bars = data.get("bars")
        bars = (
            int(raw_bars)
            if raw_bars is not None
            else max(1, round(length_minutes * 32))
        )
        return cls(
            genre=str(data.get("genre") or ""),
            tempo=int(data.get("tempo") or 0),
            key=str(data.get("key") or ""),
            length_minutes=length_minutes,
            bars=max(1, bars),
            tracks=[str(name) for name in data.get("tracks") or []],
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize this SongSpec to a JSON-compatible dict."""
        return asdict(self)
