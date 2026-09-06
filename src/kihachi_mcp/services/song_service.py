from kihachi_mcp.models import SongSpec

_DEFAULT_TRACKS: list[str] = ["Kick", "Bass", "Dub Chords", "Lead", "FX"]


class SongService:
    """Build and validate SongSpec values."""

    def generate_songspec(
        self,
        genre: str,
        tempo: int,
        key: str,
        length_minutes: float,
        mood: str,
    ) -> SongSpec:
        """Generate a SongSpec from musical parameters.

        mood is accepted as creative brief context and is not stored on the spec.
        Brain generation rules are unchanged.
        """
        _ = mood
        return SongSpec(
            genre=genre,
            tempo=tempo,
            key=key,
            length_minutes=length_minutes,
            bars=max(1, round(length_minutes * 32)),
            tracks=list(_DEFAULT_TRACKS),
        )

    def validate_songspec(self, songspec: SongSpec) -> list[str]:
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
