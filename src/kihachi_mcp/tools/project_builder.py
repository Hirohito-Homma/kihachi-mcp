from typing import Any

from kihachi_mcp.api import Brain

_brain = Brain()


def create_project_from_songspec(songspec: dict[str, Any]) -> dict[str, Any]:
    """Build a ProjectPlan via Brain and return it as JSON."""
    return _brain.create_project(songspec).to_dict()
