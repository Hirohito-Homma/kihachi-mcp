from importlib.resources import files
from pathlib import Path

from kihachi_mcp.knowledge.errors import UnknownGenreError
from kihachi_mcp.knowledge.genre_loader import load_genre_template
from kihachi_mcp.knowledge.registry import GenreRegistry
from kihachi_mcp.models.genre_template import GenreTemplate


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
