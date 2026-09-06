from dataclasses import asdict, dataclass
from typing import Any

from kihachi_mcp.models.ableton_plan import AbletonProjectPlan


@dataclass(frozen=True)
class AbletonHandoff:
    """Read-only readiness report for a future Ableton execution boundary."""

    ready: bool
    errors: list[str]
    warnings: list[str]
    plan: AbletonProjectPlan

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["plan"] = self.plan.to_dict()
        return data
