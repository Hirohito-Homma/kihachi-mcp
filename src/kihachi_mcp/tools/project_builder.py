from typing import Any

from kihachi_mcp.api import Brain

_brain = Brain()


def create_project_from_songspec(
    songspec: dict[str, Any],
    include_arrangement: bool = False,
) -> dict[str, Any]:
    """Build a ProjectPlan, optionally including its arrangement."""
    return _brain.create_project(songspec).to_dict(
        include_arrangement=include_arrangement
    )
