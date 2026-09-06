from typing import Any

from kihachi_mcp.services import ProjectService

_project_service = ProjectService()


def create_project_from_songspec(songspec: dict[str, Any]) -> dict[str, Any]:
    """Build a ProjectPlan via ProjectService and return it as JSON."""
    return _project_service.create_project_from_songspec(songspec).to_dict()
