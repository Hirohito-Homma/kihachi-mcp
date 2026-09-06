from dataclasses import asdict, dataclass
from typing import Any, Self


@dataclass(frozen=True)
class Arrangement:
    """A song section with a name and bar range."""

    name: str
    start_bar: int
    length_bars: int

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        """Create an Arrangement from a JSON-compatible dict."""
        return cls(
            name=str(data.get("name") or ""),
            start_bar=int(data.get("start_bar") or 0),
            length_bars=max(1, int(data.get("length_bars") or 1)),
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize this arrangement to a JSON-compatible dict."""
        return asdict(self)
