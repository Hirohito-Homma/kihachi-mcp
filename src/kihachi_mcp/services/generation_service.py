from kihachi_mcp.knowledge import UnknownGenreError
from kihachi_mcp.models import (
    GenerationContext,
    GenerationParameters,
    GenerationRequest,
    GenerationResult,
    KnowledgeContext,
)
from kihachi_mcp.services.knowledge_service import KnowledgeService
from kihachi_mcp.services.song_service import SongService

_BARS_PER_MINUTE = 32


class GenerationService:
    """Build a GenerationContext from knowledge, then produce a SongSpec."""

    def __init__(
        self,
        knowledge: KnowledgeService | None = None,
        song_service: SongService | None = None,
    ) -> None:
        self._knowledge = knowledge or KnowledgeService()
        self._songs = song_service or SongService()

    def build_context(self, request: GenerationRequest) -> GenerationContext:
        """Retrieve knowledge and resolve generation parameters."""
        knowledge = self._knowledge.retrieve(request.genre)
        return GenerationContext(
            request=request,
            knowledge=knowledge,
            parameters=_resolve_parameters(request, knowledge),
        )

    def generate(self, request: GenerationRequest) -> GenerationResult:
        """Generate a SongSpec from knowledge when available.

        Unknown genres still raise through SongService so existing callers
        keep the same error. An empty-knowledge context can be built separately.
        """
        context = self.build_context(request)
        if not context.knowledge.entries:
            raise UnknownGenreError(request.genre)
        songspec = self._songs.generate(
            genre=request.genre,
            tempo=request.tempo,
            key=request.key,
            length_minutes=request.length_minutes,
            mood=request.mood,
        )
        return GenerationResult(
            songspec=songspec,
            knowledge=context.knowledge,
            context=context,
        )


def _resolve_parameters(
    request: GenerationRequest, knowledge: KnowledgeContext
) -> GenerationParameters:
    template = knowledge.template()
    tempo = request.tempo if request.tempo is not None and request.tempo > 0 else 0
    key = request.key.strip() if request.key and request.key.strip() else ""
    tracks: tuple[str, ...] = ()
    mood = request.mood.strip() if request.mood and request.mood.strip() else ""
    if template is not None:
        if tempo <= 0:
            tempo = template.default_bpm
        if not key:
            key = template.default_key
        tracks = tuple(template.tracks)
        if not mood:
            mood = template.mood
    length_minutes = float(request.length_minutes)
    bars = max(1, round(length_minutes * _BARS_PER_MINUTE))
    return GenerationParameters(
        tempo=tempo,
        key=key,
        length_minutes=length_minutes,
        bars=bars,
        tracks=tracks,
        mood=mood,
    )
