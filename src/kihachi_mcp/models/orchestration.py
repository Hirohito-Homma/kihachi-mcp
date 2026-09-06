from dataclasses import dataclass
from typing import Any

from kihachi_mcp.models.memory import MemoryEntry
from kihachi_mcp.models.project_plan import ProjectPlan
from kihachi_mcp.models.review_result import ReviewResult
from kihachi_mcp.models.songspec import SongSpec


@dataclass(frozen=True)
class OrchestrationResult:
    """The complete local result of generating and preparing a song."""

    songspec: SongSpec
    review: ReviewResult
    memory: MemoryEntry
    project: ProjectPlan | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "songspec": self.songspec.to_dict(),
            "review": self.review.to_dict(),
            "memory": self.memory.to_dict(),
            "project": (
                self.project.to_dict(include_arrangement=True)
                if self.project is not None
                else None
            ),
        }
