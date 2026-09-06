from dataclasses import dataclass
from typing import Any

from kihachi_mcp.models.ableton_plan import AbletonProjectPlan


@dataclass(frozen=True)
class LiveExecutionRequest:
    """Approval-gated, non-executing request for one Live mutation."""

    status: str
    target: str
    action: str
    mutation_count: int
    approval_required: bool
    plan: AbletonProjectPlan
    errors: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "target": self.target,
            "action": self.action,
            "mutation_count": self.mutation_count,
            "approval_required": self.approval_required,
            "plan": self.plan.to_dict(),
            "errors": list(self.errors),
        }
