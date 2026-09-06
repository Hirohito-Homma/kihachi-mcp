from dataclasses import asdict, dataclass
from typing import Any, Self


@dataclass(frozen=True)
class AbletonTrackPlan:
    """Track definition for a future Ableton Set adapter."""

    name: str
    track_type: str
    color: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        """Create an Ableton track plan from JSON-compatible data."""
        return cls(
            name=str(data.get("name") or ""),
            track_type=str(data.get("track_type") or "MIDI"),
            color=str(data.get("color") or "Gray"),
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize the track plan to JSON-compatible data."""
        return asdict(self)


@dataclass(frozen=True)
class AbletonLocator:
    """Arrangement locator expressed in the Brain bar grid."""

    name: str
    start_bar: int
    length_bars: int

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        """Create an Ableton locator from JSON-compatible data."""
        return cls(
            name=str(data.get("name") or ""),
            start_bar=int(data.get("start_bar") or 0),
            length_bars=max(1, int(data.get("length_bars") or 1)),
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize the locator to JSON-compatible data."""
        return asdict(self)


@dataclass(frozen=True)
class AbletonProjectPlan:
    """Deterministic, non-mutating structure for an Ableton Set."""

    set_name: str
    genre: str
    tempo: int
    key: str
    length_minutes: float
    bars: int
    tracks: list[AbletonTrackPlan]
    locators: list[AbletonLocator]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        """Create an Ableton project plan from JSON-compatible data."""
        return cls(
            set_name=str(data.get("set_name") or "Untitled"),
            genre=str(data.get("genre") or ""),
            tempo=int(data.get("tempo") or 0),
            key=str(data.get("key") or ""),
            length_minutes=float(data.get("length_minutes") or 0),
            bars=max(1, int(data.get("bars") or 1)),
            tracks=[
                AbletonTrackPlan.from_dict(track)
                for track in data.get("tracks") or []
                if isinstance(track, dict)
            ],
            locators=[
                AbletonLocator.from_dict(locator)
                for locator in data.get("locators") or []
                if isinstance(locator, dict)
            ],
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize the Ableton project plan to JSON-compatible data."""
        return asdict(self)
