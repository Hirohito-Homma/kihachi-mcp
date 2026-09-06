from collections.abc import Callable
from typing import Any

from kihachi_mcp.models import AbletonExecutionResult, LiveExecutionRequest


class AbletonExecutionAdapter:
    """Execute approved requests through an injected Live transport only."""

    def __init__(
        self, transport: Callable[[dict[str, Any]], dict[str, Any]] | None = None
    ) -> None:
        self._transport = transport

    def execute(
        self, request: LiveExecutionRequest | dict[str, Any], approved: bool = False
    ) -> AbletonExecutionResult:
        """Return a truthful receipt without guessing Live state."""
        data = (
            request.to_dict() if isinstance(request, LiveExecutionRequest) else request
        )
        if data.get("status") != "approval_required":
            return AbletonExecutionResult(
                status="blocked",
                target="ableton_live",
                action=str(data.get("action") or "apply_ableton_plan"),
                mutation_count=0,
                error="request is not eligible for execution",
            )
        if not approved:
            return AbletonExecutionResult(
                status="approval_required",
                target="ableton_live",
                action=str(data.get("action") or "apply_ableton_plan"),
                mutation_count=1,
                error="human approval is required",
            )
        if self._transport is None:
            return AbletonExecutionResult(
                status="unavailable",
                target="ableton_live",
                action=str(data.get("action") or "apply_ableton_plan"),
                mutation_count=1,
                error="Ableton Live transport is not configured",
            )
        artifact = self._transport(data)
        return AbletonExecutionResult(
            status="executed",
            target="ableton_live",
            action=str(data.get("action") or "apply_ableton_plan"),
            mutation_count=1,
            artifact=artifact,
        )
