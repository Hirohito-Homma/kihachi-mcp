from models.project_plan import ProjectPlan
from models.songspec import SongSpec
from models.track import TrackSpec

_TRACK_COLORS: dict[str, str] = {
    "Kick": "Red",
    "Bass": "Blue",
    "Dub Chords": "Purple",
    "Lead": "Green",
    "FX": "Gray",
}


class ProjectService:
    """Convert a SongSpec into a ProjectPlan."""

    def create_project_from_songspec(self, songspec: SongSpec) -> ProjectPlan:
        """Expand SongSpec track names into typed, colored project tracks."""
        return ProjectPlan(
            project_name="Untitled",
            genre=songspec.genre,
            tempo=songspec.tempo,
            key=songspec.key,
            length_minutes=songspec.length_minutes,
            bars=songspec.bars,
            tracks=[
                TrackSpec(
                    name=name,
                    type="Audio" if name == "FX" else "MIDI",
                    color=_TRACK_COLORS.get(name, "Gray"),
                )
                for name in songspec.tracks
            ],
        )
