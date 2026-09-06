from pathlib import Path
from typing import Any

import yaml

from kihachi_mcp.knowledge.errors import InvalidGenreTemplateError
from kihachi_mcp.models.genre_template import GenreTemplate

_REQUIRED_FIELDS = frozenset(
    {"name", "default_bpm", "default_key", "tracks", "arrangement", "mood"}
)


def load_genre_template(path: Path) -> GenreTemplate:
    """Read a genre YAML file and return a GenreTemplate."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise InvalidGenreTemplateError(f"{path} must contain a YAML mapping")

    missing = sorted(_REQUIRED_FIELDS - set(raw))
    if missing:
        raise InvalidGenreTemplateError(
            f"{path} is missing required fields: {', '.join(missing)}"
        )

    return GenreTemplate(
        name=_require_str(raw, "name"),
        default_bpm=_require_positive_int(raw, "default_bpm"),
        default_key=_require_str(raw, "default_key"),
        tracks=_require_str_list(raw, "tracks"),
        arrangement=_require_arrangement(raw, "arrangement"),
        mood=_require_str(raw, "mood"),
    )


def _require_str(raw: dict[str, Any], field: str) -> str:
    value = raw[field]
    if not isinstance(value, str) or not value.strip():
        raise InvalidGenreTemplateError(f"{field} must be a non-empty string")
    return value.strip()


def _require_positive_int(raw: dict[str, Any], field: str) -> int:
    value = raw[field]
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise InvalidGenreTemplateError(f"{field} must be a positive integer")
    return value


def _require_str_list(raw: dict[str, Any], field: str) -> list[str]:
    value = raw[field]
    if not isinstance(value, list) or not value:
        raise InvalidGenreTemplateError(f"{field} must be a non-empty list")
    tracks = [str(item).strip() for item in value]
    if any(not item for item in tracks):
        raise InvalidGenreTemplateError(f"{field} must not contain empty names")
    return tracks


def _require_arrangement(raw: dict[str, Any], field: str) -> dict[str, int]:
    value = raw[field]
    if not isinstance(value, dict) or not value:
        raise InvalidGenreTemplateError(f"{field} must be a non-empty mapping")
    arrangement: dict[str, int] = {}
    for name, length in value.items():
        if not str(name).strip():
            raise InvalidGenreTemplateError("arrangement keys must be non-empty")
        if isinstance(length, bool) or not isinstance(length, int) or length <= 0:
            raise InvalidGenreTemplateError(
                "arrangement values must be positive integers"
            )
        arrangement[str(name)] = length
    return arrangement
