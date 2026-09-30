"""Approval-gated replacement of one managed Drum Rack sample."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from kihachi_mcp.models.live_contract import (
    OP_LOAD_DRUM_PAD_SAMPLE,
    OP_REPLACE_DRUM_PAD_SAMPLE,
    canonical_hash,
    is_managed_name,
)
from kihachi_mcp.models.live_mutation import (
    LiveConflict,
    LiveMutationOperation,
    LiveMutationPlan,
    LivePrecondition,
)
from kihachi_mcp.models.live_state import LiveStateSnapshot


def create_sample_replacement_plan(
    snapshot: LiveStateSnapshot,
    *,
    track_index: int,
    note: int,
    current_sample_path: str,
    replacement_sample_path: str,
    ttl_seconds: int = 600,
) -> LiveMutationPlan:
    """Plan one exact sample replacement without touching notes or other pads."""
    request_id = uuid4().hex
    track = next((item for item in snapshot.tracks if item.index == track_index), None)
    conflicts: list[LiveConflict] = []
    if track is None or not is_managed_name(track.name):
        conflicts.append(
            LiveConflict(
                "unmanaged_track",
                "KIHACHIが作成したトラックだけ差し替えできます",
                {"track_index": track_index},
            )
        )
    if note != 36:
        conflicts.append(
            LiveConflict("unsupported_pad", "現在はKickノート36だけ差し替えできます")
        )
    old_path = Path(current_sample_path).expanduser()
    new_path = Path(replacement_sample_path).expanduser()
    empty_pad = not current_sample_path
    if not old_path.is_absolute() and not empty_pad:
        conflicts.append(LiveConflict("missing_current_sample", "現在のKickを読み取れません"))
    if not new_path.is_absolute() or not new_path.is_file():
        conflicts.append(LiveConflict("missing_replacement", "選択したサンプルが見つかりません"))
    source_hash = canonical_hash(
        {
            "track_index": track_index,
            "note": note,
            "current": current_sample_path,
            "replacement": replacement_sample_path,
        }
    )
    operations = []
    if not conflicts and track is not None:
        operations.append(
            LiveMutationOperation(
                operation_id="001-load_drum_pad_sample" if empty_pad else "001-replace_drum_pad_sample",
                op=OP_LOAD_DRUM_PAD_SAMPLE if empty_pad else OP_REPLACE_DRUM_PAD_SAMPLE,
                target={"track_index": track.index},
                arguments={"note": note, "sample_path": str(new_path.resolve())},
                preconditions=[
                    LivePrecondition("not_recording"),
                    LivePrecondition("transport_stopped"),
                    LivePrecondition(
                        "track_name_at_index",
                        {"track_index": track.index, "name": track.name},
                    ),
                    *([] if empty_pad else [LivePrecondition(
                        "drum_pad_sample_path",
                        {
                            "track_index": track.index,
                            "note": note,
                            "sample_path": current_sample_path,
                        },
                    )]),
                ],
                destructive=not empty_pad,
                expected_readback={
                    "track_index": track.index,
                    "note": note,
                    "occupied": True,
                    "sample_path": str(new_path.resolve()),
                },
            )
        )
    return LiveMutationPlan(
        request_id=request_id,
        idempotency_key=canonical_hash({"request_id": request_id, "source": source_hash}),
        source_plan_hash=source_hash,
        set_fingerprint=snapshot.set_fingerprint,
        expires_at=(datetime.now(UTC) + timedelta(seconds=ttl_seconds)).isoformat(),
        operations=operations,
        conflicts=conflicts,
        warnings=["Kickノート36の空パッドへサンプルを読み込みます" if empty_pad else "Kickノート36のSimplerサンプルだけを差し替えます"],
    )
