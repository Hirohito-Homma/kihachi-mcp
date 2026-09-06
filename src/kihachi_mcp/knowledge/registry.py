from pathlib import Path

from kihachi_mcp.knowledge.errors import UnknownGenreError


def normalize_genre_name(name: str) -> str:
    """Normalize a genre label to a registry key."""
    return name.strip().lower().replace("-", "_").replace(" ", "_")


class GenreRegistry:
    """Map genre names to YAML files in a directory."""

    def __init__(self, directory: Path) -> None:
        self._directory = directory

    def resolve(self, name: str) -> Path:
        """Return the YAML path for a genre name."""
        key = normalize_genre_name(name)
        path = self._directory / f"{key}.yaml"
        if not path.is_file():
            raise UnknownGenreError(name)
        return path
