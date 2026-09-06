from typing import Any

from kihachi_mcp.models import AbletonHandoff, ProjectPlan
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

    def prepare_handoff(
        self, project_plan: ProjectPlan | dict[str, Any]
    ) -> AbletonHandoff:
        """Validate an Ableton plan without contacting or mutating Live."""
        plan = self.create_plan(project_plan)
        errors: list[str] = []
        warnings: list[str] = []
        names = [track.name for track in plan.tracks]
        if len(names) != len(set(names)):
            errors.append("track names must be unique")
        if not 20 <= plan.tempo <= 999:
            errors.append("tempo must be between 20 and 999 BPM")
        previous_end = 0
        for locator in plan.locators:
            end = locator.start_bar + locator.length_bars - 1
            if locator.start_bar < 1 or end > plan.bars:
                errors.append(f"locator '{locator.name}' is outside the project bars")
            if locator.start_bar < previous_end:
                errors.append(f"locator '{locator.name}' overlaps a previous locator")
            previous_end = max(previous_end, end)
        if not plan.tracks:
            warnings.append("project has no tracks")
        if not plan.locators:
            warnings.append("project has no arrangement locators")
        return AbletonHandoff(
            ready=not errors, errors=errors, warnings=warnings, plan=plan
        )
