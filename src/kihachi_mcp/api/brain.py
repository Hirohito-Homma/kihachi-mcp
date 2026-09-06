from typing import Any

from kihachi_mcp.knowledge import UnknownGenreError
from kihachi_mcp.models import (
    Arrangement,
    MemoryEntry,
    ProjectPlan,
    ReviewResult,
    SongSpec,
)
from kihachi_mcp.services import (
    MemoryService,
    ProjectService,
    ReviewService,
    SongService,
)


class Brain:
    """Facade that coordinates song, project, and review services."""

    def __init__(
        self,
        song_service: SongService | None = None,
        project_service: ProjectService | None = None,
        review_service: ReviewService | None = None,
        memory_service: MemoryService | None = None,
    ) -> None:
        self._songs = song_service or SongService()
        self._projects = project_service or ProjectService()
        self._reviews = review_service or ReviewService(self._songs)
        self._memory = memory_service or MemoryService()

    def generate_song(
        self,
        genre: str,
        tempo: int | None = None,
        key: str | None = None,
        length_minutes: float = 5.0,
        mood: str | None = None,
    ) -> SongSpec:
        """Create a SongSpec through SongService."""
        return self._songs.generate(
            genre=genre,
            tempo=tempo,
            key=key,
            length_minutes=length_minutes,
            mood=mood,
        )

    def create_project(self, songspec: SongSpec | dict[str, Any]) -> ProjectPlan:
        """Create a ProjectPlan with a knowledge-driven arrangement."""
        spec = (
            songspec
            if isinstance(songspec, SongSpec)
            else self._songs.from_dict(songspec)
        )
        try:
            arrangement = self._songs.default_arrangement(spec.genre, spec.bars)
        except UnknownGenreError:
            arrangement = []
        return self._projects.create_project_from_songspec(
            spec, arrangement=arrangement
        )

    def review_song(self, songspec: SongSpec | dict[str, Any]) -> ReviewResult:
        """Review a SongSpec through ReviewService."""
        spec = (
            songspec
            if isinstance(songspec, SongSpec)
            else self._songs.from_dict(songspec)
        )
        return self._reviews.review_songspec(spec)

    def remember_song(
        self,
        songspec: SongSpec | dict[str, Any],
        review: ReviewResult | dict[str, Any] | None = None,
    ) -> MemoryEntry:
        """Remember a SongSpec and optional review result."""
        spec = (
            songspec
            if isinstance(songspec, SongSpec)
            else self._songs.from_dict(songspec)
        )
        review_dict = review.to_dict() if isinstance(review, ReviewResult) else review
        return self._memory.remember(spec.to_dict(), review_dict)

    def search_memory(self, genre: str | None = None) -> list[MemoryEntry]:
        """Search remembered song decisions by genre."""
        return self._memory.search(genre)

    def default_tracks(self, genre: str) -> list[str]:
        """Return default tracks for a genre."""
        return self._songs.default_tracks(genre)

    def default_arrangement(
        self, genre: str, bars: int | None = None
    ) -> list[Arrangement]:
        """Return the genre arrangement, optionally scaled to a bar count."""
        return self._songs.default_arrangement(genre, bars)
