"""Expand verified Session View patterns into an Arrangement View plan.

Two gates protect this step.

First, the Session receipt must be ``verified``. A pattern that Live never
confirmed is not a pattern worth committing to the timeline.

Second, every target time range must be empty on its track. Existing
Arrangement clips are never trimmed, moved, or replaced, so a user's edit
survives untouched and the request comes back as a conflict instead.

Bar positions convert to Live beat time with the Set's own meter, so a 3/4 Set
does not silently get 4/4 spacing.
"""

import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from kihachi_mcp.models.live_contract import (
    OP_CREATE_LOCATOR,
    OP_PLACE_ARRANGEMENT_CLIP,
    STATUS_VERIFIED,
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
from kihachi_mcp.models.live_receipt import LiveExecutionReceipt
from kihachi_mcp.models.live_state import TRACK_TYPE_MIDI, LiveStateSnapshot
from kihachi_mcp.models.project_plan import ProjectPlan
from kihachi_mcp.services.live_approval_gate import APPROVAL_TTL_SECONDS

CONFLICT_SESSION_UNVERIFIED = "session_not_verified"
CONFLICT_ARRANGEMENT_OCCUPIED = "arrangement_range_occupied"
CONFLICT_MISSING_SESSION_CLIP = "missing_session_clip"
CONFLICT_MISSING_TRACK = "missing_track"
CONFLICT_RECORDING = "live_is_recording"
CONFLICT_PLAYING = "live_is_playing"


def bar_to_beats(start_bar: int, beats_per_bar: float) -> float:
    """Convert a one-based bar number to Live beat time.

    Bar 1 is beat 0. The conversion is exact and reversible so a plan reviewed
    in bars can be verified in beats.
    """
    return round((max(1, start_bar) - 1) * beats_per_bar, 6)


def bars_to_beats(length_bars: int, beats_per_bar: float) -> float:
    """Convert a bar count to a Live beat duration."""
    return round(max(1, length_bars) * beats_per_bar, 6)


class ArrangementExpander:
    """Plan Arrangement View placement for verified Session patterns."""

    def __init__(
        self,
        request_id_factory: Callable[[], str] | None = None,
        clock: Callable[[], datetime] | None = None,
        ttl_seconds: int = APPROVAL_TTL_SECONDS,
    ) -> None:
        self._request_id_factory = request_id_factory or (lambda: uuid.uuid4().hex)
        self._clock = clock or (lambda: datetime.now(UTC))
        self._ttl_seconds = ttl_seconds

    def create_arrangement_plan(
        self,
        project_plan: ProjectPlan | dict[str, Any],
        snapshot: LiveStateSnapshot,
        session_receipt: LiveExecutionReceipt,
        clip_bars: int = 4,
    ) -> LiveMutationPlan:
        """Plan locators and Arrangement clips for a verified Session layout."""
        plan = (
            project_plan
            if isinstance(project_plan, ProjectPlan)
            else ProjectPlan.from_dict(project_plan)
        )
        request_id = self._request_id_factory()
        source_plan_hash = canonical_hash(plan.to_dict(include_arrangement=True))
        operations: list[LiveMutationOperation] = []
        conflicts: list[LiveConflict] = []
        warnings: list[str] = []

        if session_receipt.status != STATUS_VERIFIED:
            conflicts.append(
                LiveConflict(
                    CONFLICT_SESSION_UNVERIFIED,
                    "the Session View execution is "
                    f"'{session_receipt.status}', not 'verified'; "
                    "Arrangement expansion is refused until the Session "
                    "patterns are confirmed in Live",
                    {"request_id": session_receipt.request_id},
                )
            )
        if snapshot.is_recording:
            conflicts.append(
                LiveConflict(CONFLICT_RECORDING, "Ableton Live is recording")
            )
        if snapshot.is_playing:
            conflicts.append(
                LiveConflict(
                    CONFLICT_PLAYING,
                    "Ableton Live transport is running; stop playback before "
                    "placing Arrangement clips",
                )
            )

        if not conflicts:
            builder = _ArrangementBuilder(plan, snapshot, clip_bars)
            operations, conflicts, warnings = builder.build()

        return LiveMutationPlan(
            request_id=request_id,
            idempotency_key=canonical_hash(
                {
                    "request_id": request_id,
                    "source_plan_hash": source_plan_hash,
                    "set_fingerprint": snapshot.set_fingerprint,
                    "stage": "arrangement",
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


class _ArrangementBuilder:
    """Accumulate locator and clip placement operations."""

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
        self._reserved: dict[int, list[tuple[float, float]]] = {}

    def build(self) -> tuple[
        list[LiveMutationOperation], list[LiveConflict], list[str]
    ]:
        """Return the arrangement operations together with any conflicts."""
        beats_per_bar = self._snapshot.time_signature.beats_per_bar
        # Place clips first so Live's Arrangement timeline reaches every
        # approved section before set_or_delete_cue moves the insert marker.
        # An otherwise empty Arrangement can clamp current_song_time near its
        # existing end and prevent later locators from being created.
        for track in self._plan.tracks:
            if track.type.upper() != "MIDI":
                continue
            self._plan_track(track.name, beats_per_bar)
        for section in self._plan.arrangement:
            self._plan_locator(section.name, section.start_bar, beats_per_bar)
        if self._conflicts:
            return [], self._conflicts, self._warnings
        return self._operations, self._conflicts, self._warnings

    def _next_operation_id(self, op: str) -> str:
        self._sequence += 1
        return f"{self._sequence:03d}-{op}"

    def _plan_locator(
        self, name: str, start_bar: int, beats_per_bar: float
    ) -> None:
        if not name:
            return
        beats = bar_to_beats(start_bar, beats_per_bar)
        locator_name = managed_track_name(name)
        operation_id = self._next_operation_id(OP_CREATE_LOCATOR)
        self._operations.append(
            LiveMutationOperation(
                operation_id=operation_id,
                op=OP_CREATE_LOCATOR,
                target={"scope": "arrangement"},
                arguments={"name": locator_name, "beats": beats},
                preconditions=[LivePrecondition("not_recording")],
                destructive=False,
                expected_readback={"name": locator_name, "beats": beats},
            )
        )

    def _plan_track(self, source_name: str, beats_per_bar: float) -> None:
        managed_name = managed_track_name(source_name)
        track = self._snapshot.track_by_name(managed_name)
        if track is None or track.track_type != TRACK_TYPE_MIDI:
            self._conflicts.append(
                LiveConflict(
                    CONFLICT_MISSING_TRACK,
                    f"MIDI track '{managed_name}' is not present in the Set; "
                    "run the Session View plan first",
                    {"track_name": managed_name},
                )
            )
            return
        for section in self._plan.arrangement:
            if not section.name:
                continue
            scene = self._snapshot.scene_by_name(managed_track_name(section.name))
            if scene is None:
                self._conflicts.append(
                    LiveConflict(
                        CONFLICT_MISSING_SESSION_CLIP,
                        f"scene '{managed_track_name(section.name)}' is missing; "
                        "the Session View plan has not been applied",
                        {"section": section.name},
                    )
                )
                continue
            source_clip = self._snapshot.session_clip_at(track.index, scene.index)
            if source_clip is None:
                self._conflicts.append(
                    LiveConflict(
                        CONFLICT_MISSING_SESSION_CLIP,
                        f"no verified Session clip at track {track.index}, "
                        f"scene {scene.index} for section '{section.name}'",
                        {
                            "track_index": track.index,
                            "scene_index": scene.index,
                            "section": section.name,
                        },
                    )
                )
                continue
            self._plan_placement(
                track.index, scene.index, section, source_clip.name, beats_per_bar
            )

    def _plan_placement(
        self,
        track_index: int,
        scene_index: int,
        section: Any,
        source_clip_name: str,
        beats_per_bar: float,
    ) -> None:
        start = bar_to_beats(section.start_bar, beats_per_bar)
        length = bars_to_beats(section.length_bars, beats_per_bar)
        occupied_by = self._occupied_by(track_index, start, length)
        if occupied_by is not None:
            self._conflicts.append(
                LiveConflict(
                    CONFLICT_ARRANGEMENT_OCCUPIED,
                    f"bars {section.start_bar}-"
                    f"{section.start_bar + section.length_bars - 1} on track "
                    f"{track_index} already contain '{occupied_by}'; KIHACHI "
                    "will not move or trim existing Arrangement clips",
                    {
                        "track_index": track_index,
                        "start_beats": start,
                        "length_beats": length,
                        "existing_clip": occupied_by,
                    },
                )
            )
            return
        managed_id = canonical_hash(
            {"track": track_index, "section": section.name, "start": start}
        )[:8]
        clip_name = managed_clip_name(section.name, managed_id)
        operation_id = self._next_operation_id(OP_PLACE_ARRANGEMENT_CLIP)
        self._operations.append(
            LiveMutationOperation(
                operation_id=operation_id,
                op=OP_PLACE_ARRANGEMENT_CLIP,
                target={"track_index": track_index, "scene_index": scene_index},
                arguments={
                    "name": clip_name,
                    "start_beats": start,
                    "length_beats": length,
                    "source_clip_name": source_clip_name,
                },
                preconditions=[
                    LivePrecondition("not_recording"),
                    LivePrecondition("transport_stopped"),
                    LivePrecondition(
                        "arrangement_range_free",
                        {
                            "track_index": track_index,
                            "start_beats": start,
                            "length_beats": length,
                        },
                    ),
                ],
                destructive=False,
                expected_readback={
                    "track_index": track_index,
                    "name": clip_name,
                    "start_beats": start,
                    "length_beats": length,
                },
            )
        )
        self._reserved.setdefault(track_index, []).append((start, start + length))

    def _occupied_by(
        self, track_index: int, start: float, length: float
    ) -> str | None:
        end = start + length
        for clip in self._snapshot.arrangement_clips_on(track_index):
            if start < clip.end_beats and clip.start_beats < end:
                return clip.name
        for reserved_start, reserved_end in self._reserved.get(track_index, []):
            if start < reserved_end and reserved_start < end:
                return "a clip planned earlier in this request"
        return None
