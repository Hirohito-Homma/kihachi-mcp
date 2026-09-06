from typing import Any

from kihachi_mcp.models import ProjectPlan
from kihachi_mcp.models.ableton_plan import (
    AbletonLocator,
    AbletonProjectPlan,
    AbletonTrackPlan,
)


class AbletonService:
    """Translate ProjectPlan values into a non-mutating Ableton structure."""

    def create_plan(
        self, project_plan: ProjectPlan | dict[str, Any]
    ) -> AbletonProjectPlan:
        """Create an Ableton plan without contacting Ableton Live."""
        plan = (
            project_plan
            if isinstance(project_plan, ProjectPlan)
            else ProjectPlan.from_dict(project_plan)
        )
        return AbletonProjectPlan(
            set_name=plan.project_name,
            genre=plan.genre,
            tempo=plan.tempo,
            key=plan.key,
            length_minutes=plan.length_minutes,
            bars=plan.bars,
            tracks=[
                AbletonTrackPlan(
                    name=track.name,
                    track_type=track.type,
                    color=track.color,
                )
                for track in plan.tracks
            ],
            locators=[
                AbletonLocator(
                    name=section.name,
                    start_bar=section.start_bar,
                    length_bars=section.length_bars,
                )
                for section in plan.arrangement
            ],
        )
