from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class AbletonExecutionResult:
    """Receipt for an attempted, approval-gated Ableton execution."""

    status: str
    target: str
    action: str
    mutation_count: int
    artifact: dict[str, Any] | None = None
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
