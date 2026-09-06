from typing import Any

from kihachi_mcp.knowledge import KnowledgeEngine
from kihachi_mcp.models import Arrangement, SongSpec
from kihachi_mcp.models.genre_template import GenreTemplate

_BARS_PER_MINUTE = 32


class SongService:
    """Build and validate SongSpec values from genre knowledge."""

    def __init__(self, knowledge: KnowledgeEngine | None = None) -> None:
        self._knowledge = knowledge or KnowledgeEngine()

    def default_tracks(self, genre: str) -> list[str]:
        """Return default tracks for a genre from the Knowledge Engine."""
        return list(self._knowledge.genre(genre).tracks)

    def bars_from_minutes(self, length_minutes: float) -> int:
        """Convert minutes to bars using the Brain 32-bars-per-minute grid."""
        return max(1, round(float(length_minutes) * _BARS_PER_MINUTE))

    def minutes_from_bars(self, bars: int) -> float:
        """Convert bars back to minutes on the Brain grid."""
        return max(1, int(bars)) / _BARS_PER_MINUTE

    def estimate_duration(self, songspec: SongSpec) -> float:
        """Estimate duration in minutes from the SongSpec bar count."""
        return self.minutes_from_bars(songspec.bars)

    def default_arrangement(
        self, genre: str, bars: int | None = None
    ) -> list[Arrangement]:
        """Return arrangement sections from the genre template."""
        return _arrangements_from_template(self._knowledge.genre(genre), bars)

    def generate(
        self,
        genre: str,
        tempo: int,
        key: str,
        length_minutes: float,
        mood: str,
    ) -> SongSpec:
        """Generate a SongSpec using genre knowledge for tracks.

        mood is accepted as creative brief context and is not stored on the spec.
        """
        _ = mood
        template = self._knowledge.genre(genre)
        return SongSpec(
            genre=genre,
            tempo=tempo,
            key=key,
            length_minutes=length_minutes,
            bars=self.bars_from_minutes(length_minutes),
            tracks=list(template.tracks),
        )

    def generate_songspec(
        self,
        genre: str,
        tempo: int,
        key: str,
        length_minutes: float,
        mood: str,
    ) -> SongSpec:
        """Compatibility wrapper for generate()."""
        return self.generate(
            genre=genre,
            tempo=tempo,
            key=key,
            length_minutes=length_minutes,
            mood=mood,
        )

    def validate(self, songspec: SongSpec) -> list[str]:
        """Return validation comments. An empty list means the spec is valid."""
        comments: list[str] = []
        if not songspec.genre.strip():
            comments.append("genre is required")
        if songspec.tempo <= 0:
            comments.append("tempo must be positive")
        if not songspec.key.strip():
            comments.append("key is required")
        if songspec.length_minutes <= 0:
            comments.append("length_minutes must be positive")
        if songspec.bars < 1:
            comments.append("bars must be at least 1")
        if not songspec.tracks:
            comments.append("tracks must not be empty")
        return comments

    def validate_songspec(self, songspec: SongSpec) -> list[str]:
        """Compatibility wrapper for validate()."""
        return self.validate(songspec)

    def to_dict(self, songspec: SongSpec) -> dict[str, Any]:
        """Serialize a SongSpec through the service layer."""
        return songspec.to_dict()

    def from_dict(self, data: dict[str, Any]) -> SongSpec:
        """Deserialize a SongSpec through the service layer."""
        return SongSpec.from_dict(data)


def _arrangements_from_template(
    template: GenreTemplate, bars: int | None
) -> list[Arrangement]:
    """Turn a template arrangement map into ordered Arrangement sections."""
    total = sum(template.arrangement.values())
    target = max(1, int(bars)) if bars is not None else total
    scale = target / total if total else 1
    sections: list[Arrangement] = []
    start = 1
    lengths = [
        max(1, round(length * scale)) for length in template.arrangement.values()
    ]
    if lengths:
        lengths[-1] = max(1, target - sum(lengths[:-1]))
    for name, length_bars in zip(template.arrangement, lengths, strict=True):
        sections.append(
            Arrangement(
                name=name.replace("_", " ").title(),
                start_bar=start,
                length_bars=length_bars,
            )
        )
        start += length_bars
    return sections
