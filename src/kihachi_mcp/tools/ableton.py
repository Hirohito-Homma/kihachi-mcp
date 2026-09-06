from typing import Any

from kihachi_mcp.services import AbletonService

_ableton = AbletonService()


def create_ableton_plan(project_plan: dict[str, Any]) -> dict[str, Any]:
    """Translate a ProjectPlan JSON value into an Ableton plan."""
    return _ableton.create_plan(project_plan).to_dict()
