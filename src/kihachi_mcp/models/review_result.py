from dataclasses import asdict, dataclass
from typing import Any, Self


@dataclass(frozen=True)
class ReviewResult:
    """Outcome of reviewing a SongSpec or ProjectPlan."""

    approved: bool
    score: float
    comments: list[str]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        """Create a ReviewResult from a JSON-compatible dict."""
        return cls(
            approved=bool(data.get("approved")),
            score=float(data.get("score") or 0),
            comments=[str(comment) for comment in data.get("comments") or []],
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize this review result to a JSON-compatible dict."""
        return asdict(self)
