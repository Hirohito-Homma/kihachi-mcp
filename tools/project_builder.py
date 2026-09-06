from typing import Any

from models.songspec import SongSpec
from services.project_service import ProjectService

_project_service = ProjectService()


def create_project_from_songspec(songspec: dict[str, Any]) -> dict[str, Any]:
    """Build a ProjectPlan via ProjectService and return it as JSON."""
    return _project_service.create_project_from_songspec(
        SongSpec.from_dict(songspec)
    ).to_dict()
