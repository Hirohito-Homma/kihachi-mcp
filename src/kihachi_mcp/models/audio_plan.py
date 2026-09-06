from dataclasses import asdict, dataclass
from typing import Any, Self


@dataclass(frozen=True)
class AudioRenderRequest:
    """Renderer-neutral request for one generated audio target."""

    project_name: str
    genre: str
    target_track: str
    tempo: int
    key: str
    length_minutes: float
    bars: int
    prompt: str = ""
    negative_prompt: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        """Create a request from a JSON-compatible dict."""
        return cls(
            project_name=str(data.get("project_name") or "Untitled"),
            genre=str(data.get("genre") or ""),
            target_track=str(data.get("target_track") or ""),
            tempo=int(data.get("tempo") or 0),
            key=str(data.get("key") or ""),
            length_minutes=float(data.get("length_minutes") or 0),
            bars=max(1, int(data.get("bars") or 1)),
            prompt=str(data.get("prompt") or ""),
            negative_prompt=str(data.get("negative_prompt") or ""),
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize this request without provider credentials."""
        return asdict(self)


@dataclass(frozen=True)
class AudioRenderResult:
    """Provider-independent receipt for a generated audio artifact."""

    status: str
    task_id: str = ""
    artifact_path: str = ""
    duration_seconds: float = 0
    sample_rate: int = 0
    channels: int = 0
    sha256: str = ""
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize the artifact receipt without provider response data."""
        return asdict(self)
