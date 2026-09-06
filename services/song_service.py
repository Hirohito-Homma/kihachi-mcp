from models.songspec import SongSpec

_DEFAULT_TRACKS: list[str] = ["Kick", "Bass", "Dub Chords", "Lead", "FX"]


class SongService:
    """Create SongSpec values from musical parameters."""

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
