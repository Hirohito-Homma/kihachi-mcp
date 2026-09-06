from collections.abc import Iterable

from kihachi_mcp.models import ReviewResult, SongSpec
from kihachi_mcp.services.song_service import SongService


class ReviewService:
    """Create ReviewResult values and aggregate comments and scores."""

    def __init__(self, song_service: SongService | None = None) -> None:
        self._song_service = song_service or SongService()

    def comments(self, songspec: SongSpec) -> list[str]:
        """Return validation comments for a SongSpec."""
        return self._song_service.validate(songspec)

    def warnings(self, songspec: SongSpec) -> list[str]:
        """Return non-blocking warnings for unusual musical parameters."""
        notes: list[str] = []
        if songspec.tempo < 60 or songspec.tempo > 180:
            notes.append("tempo is outside the common 60-180 BPM range")
        if songspec.length_minutes < 2:
            notes.append("length is under 2 minutes")
        if songspec.length_minutes > 12:
            notes.append("length is over 12 minutes")
        if len(songspec.tracks) < 3:
            notes.append("track count is thin for a full arrangement")
        return notes

    def suggestions(self, songspec: SongSpec) -> list[str]:
        """Return optional improvement suggestions."""
        notes: list[str] = []
        if "FX" not in songspec.tracks:
            notes.append("add an FX track for transitions")
        if "Kick" not in songspec.tracks:
            notes.append("add a Kick track to anchor the groove")
        if songspec.bars % 16 != 0:
            notes.append("round bars to a 16-bar grid for easier arrangement")
        return notes

    def score(self, songspec: SongSpec) -> float:
        """Score a SongSpec: 0.0 if invalid, otherwise reduced by warnings."""
        if self.comments(songspec):
            return 0.0
        return max(0.0, 1.0 - 0.1 * len(self.warnings(songspec)))

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

    def aggregate_scores(self, scores: Iterable[float]) -> float:
        """Return the mean score, or 0.0 when the list is empty."""
        values = [float(score) for score in scores]
        if not values:
            return 0.0
        return sum(values) / len(values)

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
        """Review a SongSpec using validation comments and the computed score."""
        comments = self.comments(songspec)
        return self.create_review_result(
            approved=not comments,
            score=self.score(songspec),
            comments=comments,
        )
