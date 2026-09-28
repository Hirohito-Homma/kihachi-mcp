"""Learn stock device parameter names from the running Live, once, on a probe track.

A recipe names parameters, and a name Live does not know stops an apply part
way through. So before an effect chain is used, each device is inserted on one
KIHACHI-owned audio track and its parameter list is read back and saved. The
probe track is left in the Set: KIHACHI never deletes tracks.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from kihachi_mcp.models.live_contract import (
    OP_CREATE_AUDIO_TRACK,
    OP_LOAD_LIVE_DEVICE,
    canonical_hash,
    managed_track_name,
)
from kihachi_mcp.models.live_mutation import (
    LiveConflict,
    LiveMutationOperation,
    LiveMutationPlan,
    LivePrecondition,
)
from kihachi_mcp.models.live_state import TRACK_TYPE_AUDIO, LiveStateSnapshot
from kihachi_mcp.services.live_approval_gate import APPROVAL_TTL_SECONDS

PROBE_TRACK = managed_track_name("KIHACHI Device Probe")
PROBE_DEVICES = (
    "EQ Eight",
    "Compressor",
    "Glue Compressor",
    "Saturator",
    "Drum Buss",
    "Auto Filter",
    "Echo",
    "Hybrid Reverb",
    "Utility",
    "Limiter",
)
#: Verified parameter lists, keyed by device name. Committed with the code.
PARAMETER_FILE = Path(__file__).resolve().parents[1] / "knowledge" / "live_device_parameters.json"


def create_probe_plan(
    snapshot: LiveStateSnapshot,
    devices: tuple[str, ...] = PROBE_DEVICES,
    request_id: str | None = None,
    now: datetime | None = None,
) -> LiveMutationPlan:
    """Plan the probe track and one insert per device it does not hold yet."""
    conflicts: list[LiveConflict] = []
    if snapshot.is_recording:
        conflicts.append(LiveConflict("live_is_recording", "録音を止めてから実行してください"))
    if snapshot.is_playing:
        conflicts.append(LiveConflict("live_is_playing", "再生を止めてから実行してください"))
    operations: list[LiveMutationOperation] = []
    warnings: list[str] = []
    available = snapshot.available_device_names()
    existing = snapshot.track_by_name(PROBE_TRACK)
    index = existing.index if existing is not None else len(snapshot.tracks)
    held = list(existing.device_names) if existing is not None else []

    def next_id(op: str) -> str:
        return f"{len(operations) + 1:03d}-{op}"

    if not conflicts and existing is None:
        operations.append(
            LiveMutationOperation(
                operation_id=next_id(OP_CREATE_AUDIO_TRACK),
                op=OP_CREATE_AUDIO_TRACK,
                target={"track_index": index},
                arguments={"name": PROBE_TRACK, "color": "0", "index": index},
                preconditions=[
                    LivePrecondition("not_recording"),
                    LivePrecondition("track_missing", {"name": PROBE_TRACK}),
                ],
                destructive=False,
                expected_readback={
                    "track_index": index,
                    "name": PROBE_TRACK,
                    "track_type": TRACK_TYPE_AUDIO,
                },
            )
        )
    for device in devices if not conflicts else ():
        if device in held:
            continue
        if device not in available:
            warnings.append(f"'{device}' はこのLiveでは読み込めないため調べません")
            continue
        operations.append(
            LiveMutationOperation(
                operation_id=next_id(OP_LOAD_LIVE_DEVICE),
                op=OP_LOAD_LIVE_DEVICE,
                target={"track_index": index},
                arguments={"device_name": device},
                preconditions=[
                    LivePrecondition("not_recording"),
                    LivePrecondition(
                        "track_name_at_index", {"track_index": index, "name": PROBE_TRACK}
                    ),
                    LivePrecondition("device_available", {"device_name": device}),
                ],
                destructive=False,
                expected_readback={
                    "track_index": index,
                    "device_name": device,
                    "device_index": len(held),
                },
            )
        )
        held.append(device)
    request = request_id or uuid.uuid4().hex
    return LiveMutationPlan(
        request_id=request,
        idempotency_key=canonical_hash(
            {"request_id": request, "kind": "device_probe", "set": snapshot.set_fingerprint}
        ),
        source_plan_hash=canonical_hash({"device_probe": list(devices)}),
        set_fingerprint=snapshot.set_fingerprint,
        expires_at=(
            (now or datetime.now(UTC)) + timedelta(seconds=APPROVAL_TTL_SECONDS)
        ).isoformat(),
        operations=operations,
        conflicts=conflicts,
        warnings=warnings,
    )


def summarize(reply: dict[str, Any]) -> dict[str, Any]:
    """Keep what a recipe needs from one get_device_parameters reply."""
    return {
        "class_name": str(reply.get("class_name") or ""),
        "parameters": [
            {
                "name": str(item.get("name") or ""),
                "min": item.get("min"),
                "max": item.get("max"),
                "is_quantized": bool(item.get("is_quantized")),
                "value_items": [str(value) for value in item.get("value_items") or []],
                "default": item.get("value"),
                **({"displays": list(item["displays"])} if item.get("displays") else {}),
            }
            for item in reply.get("parameters") or []
        ],
    }


def load_parameters(path: Path = PARAMETER_FILE) -> dict[str, Any]:
    """The verified parameter lists, or an empty table before any probe."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"live_version": "", "devices": {}}
    return data if isinstance(data, dict) else {"live_version": "", "devices": {}}


def save_parameters(
    live_version: str, devices: dict[str, Any], path: Path = PARAMETER_FILE
) -> None:
    """Merge newly read devices into the table, keeping ones read before."""
    table = load_parameters(path)
    merged = dict(table.get("devices") or {})
    merged.update(devices)
    path.write_text(
        json.dumps(
            {"live_version": live_version, "devices": dict(sorted(merged.items()))},
            ensure_ascii=False,
            indent=1,
        )
        + "\n",
        encoding="utf-8",
    )
