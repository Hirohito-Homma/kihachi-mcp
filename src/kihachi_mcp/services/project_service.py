from typing import Any

from kihachi_mcp.models import ProjectPlan, SongSpec, TrackSpec

_TRACK_COLORS: dict[str, str] = {
    "Kick": "Red",
    "Bass": "Blue",
    "Dub Chords": "Purple",
    "Lead": "Green",
    "FX": "Gray",
}


class ProjectService:
    """Create a ProjectPlan and prepare project metadata."""

    def prepare_project_metadata(self, songspec: SongSpec) -> dict[str, Any]:
        """Copy SongSpec fields into project metadata."""
        return {
            "project_name": "Untitled",
            "genre": songspec.genre,
            "tempo": songspec.tempo,
            "key": songspec.key,
            "length_minutes": songspec.length_minutes,
            "bars": songspec.bars,
        }

    def create_project_from_songspec(
        self, songspec: SongSpec | dict[str, Any]
    ) -> ProjectPlan:
        """Expand a SongSpec into a typed, colored ProjectPlan."""
        spec = (
            songspec
            if isinstance(songspec, SongSpec)
            else SongSpec.from_dict(songspec)
        )
        return ProjectPlan(
            **self.prepare_project_metadata(spec),
            tracks=[
                TrackSpec(
                    name=name,
                    type="Audio" if name == "FX" else "MIDI",
                    color=_TRACK_COLORS.get(name, "Gray"),
                )
                for name in spec.tracks
            ],
        )
