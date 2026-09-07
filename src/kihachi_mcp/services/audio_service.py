from typing import Any

from kihachi_mcp.models import GenerationContext, GenerationRequest, ProjectPlan
from kihachi_mcp.models.audio_plan import AudioRenderRequest
from kihachi_mcp.services.generation_service import GenerationService
from kihachi_mcp.services.knowledge_service import KnowledgeService


class AudioService:
    """Build renderer-neutral audio requests without contacting a provider."""

    def __init__(
        self,
        knowledge: KnowledgeService | None = None,
        generation: GenerationService | None = None,
    ) -> None:
        self._knowledge = knowledge or KnowledgeService()
        self._generation = generation or GenerationService(knowledge=self._knowledge)

    def create_request(
        self,
        project_plan: ProjectPlan | dict[str, Any],
        target_track: str,
        prompt: str = "",
        negative_prompt: str = "",
    ) -> AudioRenderRequest:
        """Create one audio request from a project and an explicit target."""
        plan = self._plan(project_plan)
        target = target_track.strip()
        if not target:
            raise ValueError("target_track must not be empty")
        if plan.tracks and target not in {track.name for track in plan.tracks}:
            raise ValueError(f"target_track not found: {target}")
        return AudioRenderRequest(
            project_name=plan.project_name,
            genre=plan.genre,
            target_track=target,
            tempo=plan.tempo,
            key=plan.key,
            length_minutes=plan.length_minutes,
            bars=plan.bars,
            prompt=prompt.strip(),
            negative_prompt=negative_prompt.strip(),
        )

    def create_generation_context(
        self, project_plan: ProjectPlan | dict[str, Any]
    ) -> GenerationContext:
        """Retrieve genre knowledge for an audio request without failing if absent."""
        plan = self._plan(project_plan)
        return self._generation.build_context(
            GenerationRequest(
                genre=plan.genre,
                tempo=plan.tempo,
                key=plan.key,
                length_minutes=plan.length_minutes,
            )
        )

    @staticmethod
    def _plan(project_plan: ProjectPlan | dict[str, Any]) -> ProjectPlan:
        return (
            project_plan
            if isinstance(project_plan, ProjectPlan)
            else ProjectPlan.from_dict(project_plan)
        )
