from kihachi_mcp.models import OrchestrationResult
from kihachi_mcp.services.memory_service import MemoryService
from kihachi_mcp.services.project_service import ProjectService
from kihachi_mcp.services.review_service import ReviewService
from kihachi_mcp.services.song_service import SongService


class Orchestrator:
    """Coordinate the deterministic local song-production workflow."""

    def __init__(
        self,
        song_service: SongService | None = None,
        review_service: ReviewService | None = None,
        memory_service: MemoryService | None = None,
        project_service: ProjectService | None = None,
    ) -> None:
        self._songs = song_service or SongService()
        self._reviews = review_service or ReviewService(self._songs)
        self._memory = memory_service or MemoryService()
        self._projects = project_service or ProjectService()

    def generate_song(
        self,
        genre: str,
        tempo: int | None = None,
        key: str | None = None,
        length_minutes: float = 5.0,
        mood: str | None = None,
        stop_on_review_failure: bool = False,
    ) -> OrchestrationResult:
        """Run generation, review, memory, and project preparation in order."""
        songspec = self._songs.generate(
            genre=genre,
            tempo=tempo,
            key=key,
            length_minutes=length_minutes,
            mood=mood,
        )
        review = self._reviews.review_songspec(songspec)
        memory = self._memory.remember(songspec.to_dict(), review.to_dict())
        project = None
        if not stop_on_review_failure or review.approved:
            project = self._projects.create_project_from_songspec(
                songspec,
                arrangement=self._songs.default_arrangement(
                    songspec.genre, songspec.bars
                ),
            )
        return OrchestrationResult(
            songspec=songspec,
            review=review,
            memory=memory,
            project=project,
        )
