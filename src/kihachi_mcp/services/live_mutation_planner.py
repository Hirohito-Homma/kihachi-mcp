"""Turn a ProjectPlan plus observed Live state into an inert mutation plan.

The planner is where all the safety decisions are made, because a plan is the
last artefact a human reviews before anything touches a Set. Two rules shape
everything here:

* KIHACHI only ever changes elements it owns. Ownership is the ``[KIHACHI]``
  marker in a name; anything without it belongs to the user and is left alone.
* An identical track name is never sufficient evidence of ownership, so a
  user's ``Kick`` never becomes KIHACHI's ``Kick``.

Nothing in this module talks to Live.
"""

import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from kihachi_mcp.models.live_contract import (
    OP_CREATE_AUDIO_TRACK,
    OP_CREATE_MIDI_TRACK,
    OP_CREATE_SCENE,
    OP_CREATE_SESSION_CLIP,
    OP_LOAD_LIVE_DEVICE,
    OP_REPLACE_CLIP_NOTES,
    OP_SET_TEMPO,
    canonical_hash,
    managed_clip_name,
    managed_track_name,
)
from kihachi_mcp.models.live_mutation import (
    LiveConflict,
    LiveMutationOperation,
    LiveMutationPlan,
    LivePrecondition,
)
from kihachi_mcp.models.live_state import (
    TRACK_TYPE_AUDIO,
    TRACK_TYPE_MIDI,
    LiveStateSnapshot,
)
from kihachi_mcp.models.project_plan import ProjectPlan
from kihachi_mcp.services import live_device_catalog
from kihachi_mcp.services.live_approval_gate import APPROVAL_TTL_SECONDS
from kihachi_mcp.services.session_pattern_builder import build_notes

CONFLICT_RECORDING = "live_is_recording"
CONFLICT_PLAYING = "live_is_playing"
CONFLICT_USER_CLIP = "user_owned_clip"
CONFLICT_DEVICE_MISSING = "device_unavailable"
CONFLICT_NO_TRACKS = "no_tracks"
CONFLICT_NO_SECTIONS = "no_sections"

DEFAULT_CLIP_BARS = 4


class LiveMutationPlanner:
    """Build a Session View mutation plan from a ProjectPlan and a snapshot."""

    def __init__(
        self,
        request_id_factory: Callable[[], str] | None = None,
        clock: Callable[[], datetime] | None = None,
        ttl_seconds: int = APPROVAL_TTL_SECONDS,
    ) -> None:
        self._request_id_factory = request_id_factory or (lambda: uuid.uuid4().hex)
        self._clock = clock or (lambda: datetime.now(UTC))
        self._ttl_seconds = ttl_seconds

    def create_session_plan(
        self,
        project_plan: ProjectPlan | dict[str, Any],
        snapshot: LiveStateSnapshot,
        clip_bars: int = DEFAULT_CLIP_BARS,
    ) -> LiveMutationPlan:
        """Plan the Session View patterns for a ProjectPlan.

        Returns a plan whose ``conflicts`` list is non-empty, and whose status
        is therefore ``blocked``, whenever the Set cannot accept the change.
        """
        plan = (
            project_plan
            if isinstance(project_plan, ProjectPlan)
            else ProjectPlan.from_dict(project_plan)
        )
        request_id = self._request_id_factory()
        source_plan_hash = canonical_hash(plan.to_dict(include_arrangement=True))
        builder = _SessionPlanBuilder(plan, snapshot, clip_bars)
        operations, conflicts, warnings = builder.build()
        return LiveMutationPlan(
            request_id=request_id,
            idempotency_key=canonical_hash(
                {
                    "request_id": request_id,
                    "source_plan_hash": source_plan_hash,
                    "set_fingerprint": snapshot.set_fingerprint,
                }
            ),
            source_plan_hash=source_plan_hash,
            set_fingerprint=snapshot.set_fingerprint,
            expires_at=(
                self._clock() + timedelta(seconds=self._ttl_seconds)
            ).isoformat(),
            operations=operations,
            conflicts=conflicts,
            warnings=warnings,
        )


class _SessionPlanBuilder:
    """Accumulate operations, conflicts, and warnings for one Set."""

    def __init__(
        self, plan: ProjectPlan, snapshot: LiveStateSnapshot, clip_bars: int
    ) -> None:
        self._plan = plan
        self._snapshot = snapshot
        self._clip_bars = max(1, clip_bars)
        self._operations: list[LiveMutationOperation] = []
        self._conflicts: list[LiveConflict] = []
        self._warnings: list[str] = []
        self._sequence = 0
        self._next_track_index = len(snapshot.tracks)
        self._next_scene_index = len(snapshot.scenes)
        self._track_index_by_name: dict[str, int] = {
            track.name: track.index for track in snapshot.tracks
        }
        self._scene_index_by_name: dict[str, int] = {
            scene.name: scene.index for scene in snapshot.scenes
        }

    def build(self) -> tuple[
        list[LiveMutationOperation], list[LiveConflict], list[str]
    ]:
        """Return the planned operations together with anything blocking them."""
        self._check_transport()
        if not self._plan.tracks:
            self._conflicts.append(
                LiveConflict(CONFLICT_NO_TRACKS, "the ProjectPlan has no tracks")
            )
        sections = self._sections()
        if not sections:
            self._conflicts.append(
                LiveConflict(
                    CONFLICT_NO_SECTIONS, "the ProjectPlan has no arrangement sections"
                )
            )
        if self._conflicts:
            return [], self._conflicts, self._warnings

        self._plan_tempo()
        midi_tracks = self._plan_tracks()
        self._plan_devices(midi_tracks)
        scene_indices = self._plan_scenes(sections)
        self._plan_clips(midi_tracks, sections, scene_indices)
        if self._conflicts:
            return [], self._conflicts, self._warnings
        return self._operations, self._conflicts, self._warnings

    def _sections(self) -> list[str]:
        return [section.name for section in self._plan.arrangement if section.name]

    def _next_operation_id(self, op: str) -> str:
        self._sequence += 1
        return f"{self._sequence:03d}-{op}"

    def _beats_per_bar(self) -> float:
        return self._snapshot.time_signature.beats_per_bar

    def _check_transport(self) -> None:
        """Refuse to plan while Live is recording or playing."""
        if self._snapshot.is_recording:
            self._conflicts.append(
                LiveConflict(
                    CONFLICT_RECORDING,
                    "Ableton Live is recording; stop recording before planning",
                )
            )
        if self._snapshot.is_playing:
            self._conflicts.append(
                LiveConflict(
                    CONFLICT_PLAYING,
                    "Ableton Live transport is running; structural changes are "
                    "refused while playing",
                )
            )

    def _plan_tempo(self) -> None:
        target = float(self._plan.tempo)
        if target <= 0 or abs(self._snapshot.tempo - target) < 1e-6:
            return
        operation_id = self._next_operation_id(OP_SET_TEMPO)
        self._operations.append(
            LiveMutationOperation(
                operation_id=operation_id,
                op=OP_SET_TEMPO,
                target={"scope": "song"},
                arguments={"tempo": target},
                preconditions=[
                    LivePrecondition("not_recording"),
                    LivePrecondition("transport_stopped"),
                ],
                destructive=True,
                expected_readback={"tempo": target},
            )
        )
        self._warnings.append(
            f"Set tempo changes from {self._snapshot.tempo} to {target} BPM"
        )

    def _plan_tracks(self) -> list[dict[str, Any]]:
        """Reserve one KIHACHI-owned track per ProjectPlan track."""
        planned: list[dict[str, Any]] = []
        for track in self._plan.tracks:
            managed_name = managed_track_name(track.name)
            existing_unmanaged = self._snapshot.track_by_name(track.name)
            if existing_unmanaged is not None and not existing_unmanaged.is_managed:
                self._warnings.append(
                    f"existing track '{track.name}' is user owned and will not be "
                    f"touched; KIHACHI will use '{managed_name}' instead"
                )
            existing = self._snapshot.track_by_name(managed_name)
            if existing is not None:
                planned.append(
                    {
                        "name": managed_name,
                        "index": existing.index,
                        "track_type": existing.track_type,
                        "created": False,
                        "device_names": list(existing.device_names),
                        "source_name": track.name,
                    }
                )
                continue
            index = self._next_track_index
            self._next_track_index += 1
            is_midi = track.type.upper() == "MIDI"
            op = OP_CREATE_MIDI_TRACK if is_midi else OP_CREATE_AUDIO_TRACK
            operation_id = self._next_operation_id(op)
            self._operations.append(
                LiveMutationOperation(
                    operation_id=operation_id,
                    op=op,
                    target={"track_index": index},
                    arguments={
                        "name": managed_name,
                        "color": track.color,
                        "index": index,
                    },
                    preconditions=[
                        LivePrecondition("not_recording"),
                        LivePrecondition(
                            "track_missing", {"name": managed_name}
                        ),
                    ],
                    destructive=False,
                    expected_readback={
                        "track_index": index,
                        "name": managed_name,
                        "track_type": (
                            TRACK_TYPE_MIDI if is_midi else TRACK_TYPE_AUDIO
                        ),
                    },
                )
            )
            self._track_index_by_name[managed_name] = index
            planned.append(
                {
                    "name": managed_name,
                    "index": index,
                    "track_type": TRACK_TYPE_MIDI if is_midi else TRACK_TYPE_AUDIO,
                    "created": True,
                    "device_names": [],
                    "source_name": track.name,
                }
            )
        return planned

    def _plan_devices(self, tracks: list[dict[str, Any]]) -> None:
        """Load one stock instrument per new MIDI track, or block."""
        available = self._snapshot.available_device_names()
        for track in tracks:
            if track["track_type"] != TRACK_TYPE_MIDI:
                continue
            if track["device_names"]:
                continue
            device_name = live_device_catalog.suggest_instrument(track["source_name"])
            try:
                live_device_catalog.resolve(device_name, available)
            except live_device_catalog.LiveDeviceUnavailableError as exc:
                self._conflicts.append(
                    LiveConflict(
                        CONFLICT_DEVICE_MISSING,
                        str(exc),
                        {"track_name": track["name"], "device_name": device_name},
                    )
                )
                continue
            operation_id = self._next_operation_id(OP_LOAD_LIVE_DEVICE)
            self._operations.append(
                LiveMutationOperation(
                    operation_id=operation_id,
                    op=OP_LOAD_LIVE_DEVICE,
                    target={"track_index": track["index"]},
                    arguments={"device_name": device_name},
                    preconditions=[
                        LivePrecondition("not_recording"),
                        LivePrecondition(
                            "track_name_at_index",
                            {"track_index": track["index"], "name": track["name"]},
                        ),
                        LivePrecondition(
                            "device_available", {"device_name": device_name}
                        ),
                    ],
                    destructive=False,
                    expected_readback={
                        "track_index": track["index"],
                        "device_name": device_name,
                    },
                )
            )

    def _plan_scenes(self, sections: list[str]) -> dict[str, int]:
        """Create only the scenes that are missing, keyed by section name."""
        indices: dict[str, int] = {}
        for section in sections:
            scene_name = managed_track_name(section)
            existing = self._snapshot.scene_by_name(scene_name)
            if existing is not None:
                indices[section] = existing.index
                continue
            index = self._next_scene_index
            self._next_scene_index += 1
            operation_id = self._next_operation_id(OP_CREATE_SCENE)
            self._operations.append(
                LiveMutationOperation(
                    operation_id=operation_id,
                    op=OP_CREATE_SCENE,
                    target={"scene_index": index},
                    arguments={"name": scene_name, "index": index},
                    preconditions=[LivePrecondition("not_recording")],
                    destructive=False,
                    expected_readback={"scene_index": index, "name": scene_name},
                )
            )
            self._scene_index_by_name[scene_name] = index
            indices[section] = index
        return indices

    def _plan_clips(
        self,
        tracks: list[dict[str, Any]],
        sections: list[str],
        scene_indices: dict[str, int],
    ) -> None:
        """Fill Session View slots that are empty or already KIHACHI owned."""
        beats_per_bar = self._beats_per_bar()
        length_beats = round(self._clip_bars * beats_per_bar, 6)
        for track in tracks:
            if track["track_type"] != TRACK_TYPE_MIDI:
                continue
            for section in sections:
                scene_index = scene_indices[section]
                track_index = track["index"]
                existing = self._snapshot.session_clip_at(track_index, scene_index)
                managed_id = _managed_clip_id(track["name"], section)
                clip_name = managed_clip_name(section, managed_id)
                if existing is not None and not existing.is_managed:
                    self._conflicts.append(
                        LiveConflict(
                            CONFLICT_USER_CLIP,
                            f"session slot (track {track_index}, scene "
                            f"{scene_index}) holds the user clip "
                            f"'{existing.name}'; KIHACHI will not overwrite it",
                            {
                                "track_index": track_index,
                                "scene_index": scene_index,
                                "clip_name": existing.name,
                            },
                        )
                    )
                    continue
                notes = build_notes(
                    track_name=track["source_name"],
                    key=self._plan.key,
                    length_bars=self._clip_bars,
                    beats_per_bar=beats_per_bar,
                    density=_section_density(section),
                )
                note_payload = [note.to_dict() for note in notes]
                if existing is None:
                    self._append_create_clip(
                        track, scene_index, clip_name, length_beats
                    )
                else:
                    self._warnings.append(
                        f"replacing notes in KIHACHI clip '{existing.name}' at "
                        f"track {track_index}, scene {scene_index}"
                    )
                self._append_replace_notes(
                    track,
                    scene_index,
                    clip_name,
                    note_payload,
                    destructive=existing is not None,
                )

    def _append_create_clip(
        self,
        track: dict[str, Any],
        scene_index: int,
        clip_name: str,
        length_beats: float,
    ) -> None:
        operation_id = self._next_operation_id(OP_CREATE_SESSION_CLIP)
        self._operations.append(
            LiveMutationOperation(
                operation_id=operation_id,
                op=OP_CREATE_SESSION_CLIP,
                target={
                    "track_index": track["index"],
                    "scene_index": scene_index,
                },
                arguments={
                    "name": clip_name,
                    "length_beats": length_beats,
                    "looping": True,
                },
                preconditions=[
                    LivePrecondition("not_recording"),
                    LivePrecondition(
                        "track_name_at_index",
                        {"track_index": track["index"], "name": track["name"]},
                    ),
                    LivePrecondition(
                        "session_slot_empty",
                        {
                            "track_index": track["index"],
                            "scene_index": scene_index,
                        },
                    ),
                ],
                destructive=False,
                expected_readback={
                    "track_index": track["index"],
                    "scene_index": scene_index,
                    "name": clip_name,
                    "length_beats": length_beats,
                    "looping": True,
                },
            )
        )

    def _append_replace_notes(
        self,
        track: dict[str, Any],
        scene_index: int,
        clip_name: str,
        notes: list[dict[str, Any]],
        destructive: bool,
    ) -> None:
        operation_id = self._next_operation_id(OP_REPLACE_CLIP_NOTES)
        preconditions = [
            LivePrecondition("not_recording"),
            LivePrecondition(
                "track_name_at_index",
                {"track_index": track["index"], "name": track["name"]},
            ),
        ]
        if destructive:
            preconditions.append(
                LivePrecondition(
                    "clip_is_managed",
                    {"track_index": track["index"], "scene_index": scene_index},
                )
            )
        self._operations.append(
            LiveMutationOperation(
                operation_id=operation_id,
                op=OP_REPLACE_CLIP_NOTES,
                target={"track_index": track["index"], "scene_index": scene_index},
                arguments={"notes": notes, "clip_name": clip_name},
                preconditions=preconditions,
                destructive=destructive,
                expected_readback={
                    "track_index": track["index"],
                    "scene_index": scene_index,
                    "note_count": len(notes),
                },
            )
        )


def _managed_clip_id(track_name: str, section: str) -> str:
    """Return a short, stable ownership id for one planned clip."""
    return canonical_hash({"track": track_name, "section": section})[:8]


def _section_density(section: str) -> float:
    """Return the deterministic density KIHACHI uses for a section name."""
    lowered = section.lower()
    if any(word in lowered for word in ("intro", "outro", "break")):
        return 0.5
    if "build" in lowered:
        return 0.75
    return 1.0
