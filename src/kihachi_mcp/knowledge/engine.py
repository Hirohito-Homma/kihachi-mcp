from importlib.resources import files
from pathlib import Path

from kihachi_mcp.knowledge.errors import UnknownGenreError
from kihachi_mcp.knowledge.genre_loader import load_genre_template
from kihachi_mcp.knowledge.registry import GenreRegistry, normalize_genre_name
from kihachi_mcp.models.genre_template import GenreTemplate
from kihachi_mcp.models.knowledge import KnowledgeContext, KnowledgeEntry

_PACKAGED_KNOWLEDGE_VERSION = "1"
_PACKAGED_KNOWLEDGE_SOURCE = "genre_yaml"
_PACKAGED_KNOWLEDGE_KIND = "genre"


def default_genres_directory() -> Path:
    """Return the packaged genres directory. Hides resource lookup from callers."""
    return Path(str(files("kihachi_mcp.resources.genres")))


class KnowledgeEngine:
    """Look up genre knowledge without exposing the filesystem to services."""

    def __init__(self, registry: GenreRegistry | None = None) -> None:
        self._registry = registry or GenreRegistry(default_genres_directory())

    def genre(self, name: str) -> GenreTemplate:
        """Return the GenreTemplate for a known genre name."""
        if not name or not name.strip():
            raise UnknownGenreError(name)
        return load_genre_template(self._registry.resolve(name))

    def query(self, genre: str | None = None) -> KnowledgeContext:
        """Return matching packaged knowledge. Unknown names yield an empty context."""
        if genre is None:
            names = self._registry.list_names()
        elif not genre.strip():
            return KnowledgeContext()
        else:
            names = [genre]
        entries: list[KnowledgeEntry] = []
        for name in names:
            try:
                template = self.genre(name)
            except UnknownGenreError:
                continue
            entries.append(self._entry_for(name, template))
        return KnowledgeContext.from_entries(entries)

    def select(self, names: list[str]) -> KnowledgeContext:
        """Return knowledge for the given names, skipping unknowns."""
        entries: list[KnowledgeEntry] = []
        for name in names:
            entries.extend(self.query(name).entries)
        return KnowledgeContext.from_entries(entries)

    def _entry_for(self, name: str, template: GenreTemplate) -> KnowledgeEntry:
        return KnowledgeEntry(
            id=normalize_genre_name(name),
            version=_PACKAGED_KNOWLEDGE_VERSION,
            source=_PACKAGED_KNOWLEDGE_SOURCE,
            kind=_PACKAGED_KNOWLEDGE_KIND,
            template=template,
        )
