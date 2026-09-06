from datetime import UTC, datetime
from typing import Any

from kihachi_mcp.models import Arrangement, ProjectPlan, SongSpec, TrackSpec

_TRACK_COLORS: dict[str, str] = {
    "Kick": "Red",
    "Bass": "Blue",
    "Dub Chords": "Purple",
    "Lead": "Green",
    "FX": "Gray",
}


class ProjectService:
    """Create a ProjectPlan and prepare project metadata."""

    def project_name(self, songspec: SongSpec | None = None) -> str:
        """Return the default project name. SongSpec is reserved for later naming."""
        _ = songspec
        return "Untitled"

    def output_directory(self, songspec: SongSpec | None = None) -> str:
        """Return the logical project output path. No filesystem writes."""
        return f"projects/{self.project_name(songspec)}"

    def created_at(self) -> str:
        """Return the current UTC timestamp in ISO-8601 form."""
        return datetime.now(UTC).isoformat()

    def prepare_project_metadata(self, songspec: SongSpec) -> dict[str, Any]:
        """Copy SongSpec fields into ProjectPlan constructor metadata."""
        return {
            "project_name": self.project_name(songspec),
            "genre": songspec.genre,
            "tempo": songspec.tempo,
            "key": songspec.key,
            "length_minutes": songspec.length_minutes,
            "bars": songspec.bars,
        }

    def metadata(self, songspec: SongSpec) -> dict[str, Any]:
        """Return ProjectPlan fields plus output path and created_at."""
        return {
            **self.prepare_project_metadata(songspec),
            "output_directory": self.output_directory(songspec),
            "created_at": self.created_at(),
        }

    def create_project_from_songspec(
        self,
        songspec: SongSpec | dict[str, Any],
        arrangement: list[Arrangement] | None = None,
    ) -> ProjectPlan:
        """Expand a SongSpec into a typed, colored ProjectPlan."""
        spec = (
            songspec if isinstance(songspec, SongSpec) else SongSpec.from_dict(songspec)
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
            arrangement=list(arrangement or []),
        )
