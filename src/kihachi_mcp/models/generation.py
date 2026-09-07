from dataclasses import dataclass
from typing import Any, Self

from kihachi_mcp.models.knowledge import KnowledgeContext
from kihachi_mcp.models.songspec import SongSpec


@dataclass(frozen=True)
class GenerationRequest:
    """Application-level request for knowledge-driven generation."""

    genre: str
    tempo: int | None = None
    key: str | None = None
    length_minutes: float = 5.0
    mood: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        """Create a GenerationRequest from a JSON-compatible dict."""
        raw_tempo = data.get("tempo")
        raw_key = data.get("key")
        raw_mood = data.get("mood")
        return cls(
            genre=str(data.get("genre") or ""),
            tempo=int(raw_tempo) if raw_tempo is not None else None,
            key=None if raw_key is None else str(raw_key),
            length_minutes=float(data.get("length_minutes") or 5.0),
            mood=None if raw_mood is None else str(raw_mood),
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize this request."""
        return {
            "genre": self.genre,
            "tempo": self.tempo,
            "key": self.key,
            "length_minutes": self.length_minutes,
            "mood": self.mood,
        }


@dataclass(frozen=True)
class GenerationParameters:
    """Resolved generation values after knowledge is applied."""

    tempo: int
    key: str
    length_minutes: float
    bars: int
    tracks: tuple[str, ...]
    mood: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize resolved parameters."""
        return {
            "tempo": self.tempo,
            "key": self.key,
            "length_minutes": self.length_minutes,
            "bars": self.bars,
            "tracks": list(self.tracks),
            "mood": self.mood,
        }


@dataclass(frozen=True)
class GenerationContext:
    """Request plus the structured knowledge used to resolve it."""

    request: GenerationRequest
    knowledge: KnowledgeContext
    parameters: GenerationParameters

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        """Create a GenerationContext from a JSON-compatible dict."""
        raw_request = data.get("request") or {}
        raw_knowledge = data.get("knowledge") or {}
        raw_parameters = data.get("parameters") or {}
        return cls(
            request=GenerationRequest.from_dict(
                raw_request if isinstance(raw_request, dict) else {}
            ),
            knowledge=KnowledgeContext.from_dict(
                raw_knowledge if isinstance(raw_knowledge, dict) else {}
            ),
            parameters=GenerationParameters(
                tempo=int(raw_parameters.get("tempo") or 0),
                key=str(raw_parameters.get("key") or ""),
                length_minutes=float(raw_parameters.get("length_minutes") or 0),
                bars=max(1, int(raw_parameters.get("bars") or 1)),
                tracks=tuple(str(name) for name in raw_parameters.get("tracks") or []),
                mood=str(raw_parameters.get("mood") or ""),
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize the generation context."""
        return {
            "request": self.request.to_dict(),
            "knowledge": self.knowledge.to_dict(),
            "parameters": self.parameters.to_dict(),
        }


@dataclass(frozen=True)
class GenerationResult:
    """Structured generation outcome with knowledge provenance."""

    songspec: SongSpec
    knowledge: KnowledgeContext
    context: GenerationContext

    def to_dict(self) -> dict[str, Any]:
        """Serialize the result without changing the SongSpec JSON shape."""
        return {
            "songspec": self.songspec.to_dict(),
            "knowledge": self.knowledge.to_provenance(),
            "parameters": self.context.parameters.to_dict(),
        }
