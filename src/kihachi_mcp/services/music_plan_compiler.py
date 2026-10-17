"""Compile a MusicPlan into the existing LiveMutationPlan.

The operations are the ones Ableton execution already implements. This module
does not connect to Live, approve a plan, or send one.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from kihachi_mcp.models.instrument_spec import InstrumentSpec
from kihachi_mcp.models.live_contract import (
    MANAGED_MARKER,
    OP_CREATE_MIDI_TRACK,
    OP_CREATE_SCENE,
    OP_CREATE_SESSION_CLIP,
    OP_REPLACE_CLIP_NOTES,
    OP_SET_TEMPO,
    canonical_hash,
    managed_clip_name,
    managed_track_name,
)
from kihachi_mcp.models.live_mutation import (
    LiveMutationOperation,
    LiveMutationPlan,
    LivePrecondition,
)
from kihachi_mcp.models.live_state import TRACK_TYPE_MIDI
from kihachi_mcp.models.music_plan import MusicPlan
from kihachi_mcp.services.music_plan_validator import validate_music_plan
from kihachi_mcp.services.musical_time import bar_start_beats, beats_per_bar

LIVEPLAN_INVALID = "LIVEPLAN_INVALID"


class MusicPlanCompileError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def compile_music_plan(
    plan: MusicPlan,
    instruments: dict[str, InstrumentSpec] | None = None,
    *,
    change_tempo: bool = False,
    track_index: int = 0,
    scene_index: int = 0,
    set_fingerprint: str = "uninspected",
    label: str = "",
    now: datetime | None = None,
) -> LiveMutationPlan:
    """Build an inert Live plan. track_index is provisional until Live is read."""
    validation = validate_music_plan(plan, instruments)
    if not validation.ok:
        raise MusicPlanCompileError(
            validation.issues[0].code,
            validation.issues[0].message,
        )
    meter = beats_per_bar(plan.numerator, plan.denominator)
    if set_fingerprint == "uninspected":
        warnings = [
            "Live のトラック位置は未読です。track_index は仮の値で、commit 前の読取りが必要です。",
            "再生状態は UNKNOWN です。",
        ]
    else:
        warnings = [f"読んだ Live の末尾、トラック {track_index + 1} 番目とシーン {scene_index + 1} 番目から足します。"]
    warnings.extend(plan.warnings)
    operations: list[LiveMutationOperation] = []
    if change_tempo:
        operations.append(
            LiveMutationOperation(
                operation_id="001-set_tempo",
                op=OP_SET_TEMPO,
                target={},
                arguments={"tempo": plan.tempo},
                preconditions=[LivePrecondition("not_recording")],
                destructive=True,
                expected_readback={"tempo": plan.tempo},
            )
        )
    else:
        warnings.append("テンポは MusicPlan にありますが、Set のテンポは変えません。")
    scene_name = managed_track_name(f"Main {label}" if label else "Main")
    operations.append(
        LiveMutationOperation(
            operation_id=f"{len(operations) + 1:03d}-{OP_CREATE_SCENE}",
            op=OP_CREATE_SCENE,
            target={"scene_index": scene_index},
            arguments={"name": scene_name, "index": scene_index},
            preconditions=[LivePrecondition("not_recording")],
            destructive=False,
            expected_readback={"scene_index": scene_index, "name": scene_name},
        )
    )
    next_track_index = track_index
    for track in plan.tracks:
        track_index = next_track_index
        next_track_index += 1
        track_name = managed_track_name(f"KIHACHI {track.name} {label}" if label else track.name)
        if MANAGED_MARKER not in track_name:
            raise MusicPlanCompileError(LIVEPLAN_INVALID, "管理名を付けられません。")
        operations.append(
            LiveMutationOperation(
                operation_id=f"{len(operations) + 1:03d}-{OP_CREATE_MIDI_TRACK}",
                op=OP_CREATE_MIDI_TRACK,
                target={"track_index": track_index},
                arguments={"name": track_name, "color": "Blue", "index": track_index},
                preconditions=[
                    LivePrecondition("not_recording"),
                    LivePrecondition("track_missing", {"name": track_name}),
                ],
                destructive=False,
                expected_readback={
                    "track_index": track_index,
                    "name": track_name,
                    "track_type": TRACK_TYPE_MIDI,
                },
            )
        )
        for clip in track.clips:
            length_beats = round(clip.length_bars * meter, 6)
            clip_id = canonical_hash({"track": track.id, "start_bar": clip.start_bar})[:8]
            clip_name = managed_clip_name("Main", clip_id)
            operations.append(
                LiveMutationOperation(
                    operation_id=f"{len(operations) + 1:03d}-{OP_CREATE_SESSION_CLIP}",
                    op=OP_CREATE_SESSION_CLIP,
                    target={"track_index": track_index, "scene_index": scene_index},
                    arguments={"name": clip_name, "length_beats": length_beats, "looping": True},
                    preconditions=[
                        LivePrecondition("not_recording"),
                        LivePrecondition(
                            "track_name_at_index",
                            {"track_index": track_index, "name": track_name},
                        ),
                        LivePrecondition(
                            "session_slot_empty",
                            {"track_index": track_index, "scene_index": scene_index},
                        ),
                    ],
                    destructive=False,
                    expected_readback={
                        "track_index": track_index,
                        "scene_index": scene_index,
                        "name": clip_name,
                        "length_beats": length_beats,
                        "looping": True,
                    },
                )
            )
            notes = [note.to_dict() for note in clip.notes]
            operations.append(
                LiveMutationOperation(
                    operation_id=f"{len(operations) + 1:03d}-{OP_REPLACE_CLIP_NOTES}",
                    op=OP_REPLACE_CLIP_NOTES,
                    target={"track_index": track_index, "scene_index": scene_index},
                    arguments={"notes": notes, "clip_name": clip_name},
                    preconditions=[
                        LivePrecondition("not_recording"),
                        LivePrecondition(
                            "track_name_at_index",
                            {"track_index": track_index, "name": track_name},
                        ),
                    ],
                    destructive=False,
                    expected_readback={
                        "track_index": track_index,
                        "scene_index": scene_index,
                        "note_count": len(notes),
                    },
                )
            )
    source_hash = canonical_hash(plan.to_dict())
    request_id = uuid4().hex
    moment = now or datetime.now(UTC)
    return LiveMutationPlan(
        request_id=request_id,
        idempotency_key=canonical_hash({"request_id": request_id, "source_plan_hash": source_hash}),
        source_plan_hash=source_hash,
        set_fingerprint=set_fingerprint,
        expires_at=(moment + timedelta(seconds=900)).isoformat(),
        operations=operations,
        warnings=warnings,
    )


def dry_run_summary(plan: MusicPlan, live: LiveMutationPlan) -> dict[str, object]:
    """Describe what a later commit would change. Nothing is sent."""
    meter = beats_per_bar(plan.numerator, plan.denominator)
    start = bar_start_beats(plan.scope.start_bar, plan.numerator, plan.denominator)
    end = round(start + plan.scope.length_bars * meter, 6)
    note_count = sum(len(clip.notes) for track in plan.tracks for clip in track.clips)
    clip_count = sum(len(track.clips) for track in plan.tracks)
    return {
        "status": live.status,
        "changes": {"tracks": len(plan.tracks), "clips": clip_count, "notes": note_count},
        "range": {"start": start, "end": end},
        "conflicts": [conflict.to_dict() for conflict in live.conflicts],
        "playback": "UNKNOWN",
        "save": False,
        "executed": False,
        "live_changed": False,
        "approval_required": live.approval_required,
        "plan_hash": live.plan_hash,
    }
