from collections.abc import Sequence

from kihachi_mcp.knowledge import KnowledgeEngine, UnknownGenreError
from kihachi_mcp.models import KnowledgeContext, KnowledgeEntry


class KnowledgeService:
    """Retrieve and filter knowledge without exposing storage to generators."""

    def __init__(self, engine: KnowledgeEngine | None = None) -> None:
        self._engine = engine or KnowledgeEngine()

    def retrieve(self, genre: str | None = None) -> KnowledgeContext:
        """Return matching knowledge. Missing genres produce an empty context."""
        return self._engine.query(genre)

    def select(self, names: Sequence[str]) -> KnowledgeContext:
        """Return knowledge for the given names, skipping unknowns."""
        return self._engine.select(list(names))

    def filter(
        self,
        context: KnowledgeContext,
        *,
        kind: str | None = None,
        source: str | None = None,
        ids: list[str] | None = None,
    ) -> KnowledgeContext:
        """Filter an already-retrieved context."""
        return context.select(kind=kind, source=source, ids=ids)

    def get(self, name: str) -> KnowledgeEntry:
        """Return one required knowledge entry or raise."""
        context = self.retrieve(name)
        entry = context.primary()
        if entry is None:
            raise UnknownGenreError(name)
        return entry
