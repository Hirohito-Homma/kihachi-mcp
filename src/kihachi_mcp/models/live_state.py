"""Read-only models describing observed Ableton Live state.

A snapshot is the only thing the planner is allowed to reason about. The
``set_fingerprint`` is always recomputed from the observed structure rather than
trusted from the transport payload, so a Set that changed between planning and
execution cannot be mistaken for an unchanged one.
"""

from dataclasses import asdict, dataclass, field
from typing import Any, Self

from kihachi_mcp.models.live_contract import (
    SCHEMA_VERSION,
    canonical_hash,
    is_managed_name,
    require_schema_version,
)
from kihachi_mcp.models.live_paths import normalize_set_path

TRACK_TYPE_MIDI = "midi"
TRACK_TYPE_AUDIO = "audio"


@dataclass(frozen=True)
class LiveTimeSignature:
    """Live transport time signature."""

    numerator: int = 4
    denominator: int = 4

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        """Create a time signature from JSON-compatible data."""
        return cls(
            numerator=max(1, int(data.get("numerator") or 4)),
            denominator=max(1, int(data.get("denominator") or 4)),
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize the time signature to JSON-compatible data."""
        return asdict(self)

    @property
    def beats_per_bar(self) -> float:
        """Return one bar in Live beat units, where a beat is a quarter note."""
        return self.numerator * 4.0 / self.denominator


@dataclass(frozen=True)
class LiveTrack:
    """One observed Live track."""

    index: int
    name: str
    track_type: str
    color: str = ""
    is_armed: bool = False
    is_frozen: bool = False
    device_names: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        """Create a track from JSON-compatible data."""
        return cls(
            index=int(data.get("index") or 0),
            name=str(data.get("name") or ""),
            track_type=str(data.get("track_type") or TRACK_TYPE_MIDI).lower(),
            color=str(data.get("color") or ""),
            is_armed=bool(data.get("is_armed")),
            is_frozen=bool(data.get("is_frozen")),
            device_names=[str(item) for item in data.get("device_names") or []],
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize the track to JSON-compatible data."""
        return asdict(self)

    @property
    def is_managed(self) -> bool:
        """Report whether KIHACHI owns this track."""
        return is_managed_name(self.name)


@dataclass(frozen=True)
class LiveScene:
    """One observed Live scene."""

    index: int
    name: str = ""
    color: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        """Create a scene from JSON-compatible data."""
        return cls(
            index=int(data.get("index") or 0),
            name=str(data.get("name") or ""),
            color=str(data.get("color") or ""),
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize the scene to JSON-compatible data."""
        return asdict(self)

    @property
    def is_managed(self) -> bool:
        """Report whether KIHACHI owns this scene."""
        return is_managed_name(self.name)


@dataclass(frozen=True)
class LiveSessionClip:
    """One observed Session View clip slot that holds a clip."""

    track_index: int
    scene_index: int
    name: str = ""
    length_beats: float = 0.0
    note_count: int = 0
    is_midi: bool = True
    looping: bool = True

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        """Create a session clip from JSON-compatible data."""
        return cls(
            track_index=int(data.get("track_index") or 0),
            scene_index=int(data.get("scene_index") or 0),
            name=str(data.get("name") or ""),
            length_beats=float(data.get("length_beats") or 0.0),
            note_count=int(data.get("note_count") or 0),
            is_midi=bool(data.get("is_midi", True)),
            looping=bool(data.get("looping", True)),
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize the session clip to JSON-compatible data."""
        return asdict(self)

    @property
    def is_managed(self) -> bool:
        """Report whether KIHACHI owns this clip."""
        return is_managed_name(self.name)


@dataclass(frozen=True)
class LiveArrangementClip:
    """One observed Arrangement View clip occupying a time range."""

    track_index: int
    name: str = ""
    start_beats: float = 0.0
    length_beats: float = 0.0
    note_count: int = 0

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        """Create an arrangement clip from JSON-compatible data."""
        return cls(
            track_index=int(data.get("track_index") or 0),
            name=str(data.get("name") or ""),
            start_beats=float(data.get("start_beats") or 0.0),
            length_beats=float(data.get("length_beats") or 0.0),
            note_count=int(data.get("note_count") or 0),
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize the arrangement clip to JSON-compatible data."""
        return asdict(self)

    @property
    def end_beats(self) -> float:
        """Return the exclusive end of the occupied time range."""
        return self.start_beats + self.length_beats

    @property
    def is_managed(self) -> bool:
        """Report whether KIHACHI owns this clip."""
        return is_managed_name(self.name)


@dataclass(frozen=True)
class LiveDevice:
    """One Live device the running Live installation reports as loadable."""

    name: str
    available: bool = True
    category: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        """Create a device entry from JSON-compatible data."""
        return cls(
            name=str(data.get("name") or ""),
            available=bool(data.get("available", True)),
            category=str(data.get("category") or ""),
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize the device entry to JSON-compatible data."""
        return asdict(self)


@dataclass(frozen=True)
class LiveStateSnapshot:
    """A JSON-safe, read-only observation of one Ableton Live Set."""

    live_version: str
    set_name: str
    set_path: str
    tempo: float
    time_signature: LiveTimeSignature
    is_playing: bool
    is_recording: bool
    observed_at: str
    schema_version: int = SCHEMA_VERSION
    tracks: list[LiveTrack] = field(default_factory=list)
    scenes: list[LiveScene] = field(default_factory=list)
    session_clips: list[LiveSessionClip] = field(default_factory=list)
    arrangement_clips: list[LiveArrangementClip] = field(default_factory=list)
    devices: list[LiveDevice] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        """Create a snapshot from JSON-compatible data, rejecting other schemas."""
        require_schema_version(data, "LiveStateSnapshot")
        return cls(
            live_version=str(data.get("live_version") or ""),
            set_name=str(data.get("set_name") or ""),
            set_path=normalize_set_path(str(data.get("set_path") or "")),
            tempo=float(data.get("tempo") or 0.0),
            time_signature=LiveTimeSignature.from_dict(
                data.get("time_signature")
                if isinstance(data.get("time_signature"), dict)
                else {}
            ),
            is_playing=bool(data.get("is_playing")),
            is_recording=bool(data.get("is_recording")),
            observed_at=str(data.get("observed_at") or ""),
            tracks=[
                LiveTrack.from_dict(item)
                for item in data.get("tracks") or []
                if isinstance(item, dict)
            ],
            scenes=[
                LiveScene.from_dict(item)
                for item in data.get("scenes") or []
                if isinstance(item, dict)
            ],
            session_clips=[
                LiveSessionClip.from_dict(item)
                for item in data.get("session_clips") or []
                if isinstance(item, dict)
            ],
            arrangement_clips=[
                LiveArrangementClip.from_dict(item)
                for item in data.get("arrangement_clips") or []
                if isinstance(item, dict)
            ],
            devices=[
                LiveDevice.from_dict(item)
                for item in data.get("devices") or []
                if isinstance(item, dict)
            ],
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize the snapshot, including the recomputed set fingerprint."""
        data = asdict(self)
        data["set_fingerprint"] = self.set_fingerprint
        return data

    @property
    def set_fingerprint(self) -> str:
        """Return a stable hash of the structural parts of this Set.

        Transport state and observation time are excluded so that merely
        pressing play does not invalidate an approved plan, while any change to
        tracks, scenes, clips, tempo, or meter does.
        """
        return canonical_hash(
            {
                "set_path": self.set_path,
                "set_name": self.set_name,
                "tempo": round(self.tempo, 6),
                "time_signature": self.time_signature.to_dict(),
                "tracks": [
                    {
                        "index": track.index,
                        "name": track.name,
                        "track_type": track.track_type,
                        "color": track.color,
                        "device_names": list(track.device_names),
                    }
                    for track in self.tracks
                ],
                "scenes": [scene.to_dict() for scene in self.scenes],
                "session_clips": [clip.to_dict() for clip in self.session_clips],
                "arrangement_clips": [
                    clip.to_dict() for clip in self.arrangement_clips
                ],
            }
        )

    def track_by_name(self, name: str) -> LiveTrack | None:
        """Return the first track with an exact name match."""
        for track in self.tracks:
            if track.name == name:
                return track
        return None

    def track_by_index(self, index: int) -> LiveTrack | None:
        """Return the track at a Live track index."""
        for track in self.tracks:
            if track.index == index:
                return track
        return None

    def scene_by_name(self, name: str) -> LiveScene | None:
        """Return the first scene with an exact name match."""
        for scene in self.scenes:
            if scene.name == name:
                return scene
        return None

    def session_clip_at(
        self, track_index: int, scene_index: int
    ) -> LiveSessionClip | None:
        """Return the clip occupying one Session View slot, if any."""
        for clip in self.session_clips:
            if clip.track_index == track_index and clip.scene_index == scene_index:
                return clip
        return None

    def arrangement_clips_on(self, track_index: int) -> list[LiveArrangementClip]:
        """Return arrangement clips on one track ordered by start time."""
        return sorted(
            (clip for clip in self.arrangement_clips if clip.track_index == track_index),
            key=lambda clip: clip.start_beats,
        )

    def available_device_names(self) -> frozenset[str]:
        """Return the names of devices Live reports as loadable."""
        return frozenset(
            device.name for device in self.devices if device.available and device.name
        )
