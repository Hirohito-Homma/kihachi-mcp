from collections.abc import Iterable

from kihachi_mcp.models import ReviewResult, SongSpec
from kihachi_mcp.services.song_service import SongService


class ReviewService:
    """Create ReviewResult values and aggregate review comments."""

    def __init__(self, song_service: SongService | None = None) -> None:
        self._song_service = song_service or SongService()

    def create_review_result(
        self,
        approved: bool,
        score: float,
        comments: list[str],
    ) -> ReviewResult:
        """Build a ReviewResult from an approval decision and comments."""
        return ReviewResult(
            approved=approved,
            score=score,
            comments=self.aggregate_comments(comments),
        )

    def aggregate_comments(self, *comment_groups: Iterable[str]) -> list[str]:
        """Flatten comment groups and drop blanks and duplicates."""
        aggregated: list[str] = []
        seen: set[str] = set()
        for group in comment_groups:
            for comment in group:
                text = comment.strip()
                if text and text not in seen:
                    seen.add(text)
                    aggregated.append(text)
        return aggregated

    def review_songspec(self, songspec: SongSpec) -> ReviewResult:
        """Review a SongSpec using SongService validation comments."""
        comments = self._song_service.validate_songspec(songspec)
        approved = not comments
        return self.create_review_result(
            approved=approved,
            score=1.0 if approved else 0.0,
            comments=comments,
        )
