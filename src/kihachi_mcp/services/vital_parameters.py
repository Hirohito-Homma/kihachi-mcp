"""StudioからVitalのホスト公開パラメータを安全に変更する計画。"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from kihachi_mcp.models.live_contract import (
    OP_SET_DEVICE_PARAMETER,
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

VITAL_DEVICE_NAME = "Vital"
VITAL_CONFIGURE_GUIDANCE = (
    "Vitalは現在「Device On」以外をLiveへ公開していません。"
    "Vitalを開き、LiveのConfigureで操作したいパラメータを登録してから、"
    "デバイスヘッダの保存アイコンで.vstpresetとして保存してください。"
)


def is_vital_device(name: str) -> bool:
    """Liveが返したデバイス名がVitalそのものかを厳密に判定する。"""
    return name.strip().casefold() == VITAL_DEVICE_NAME.casefold()


def parameter_view(parameter: dict[str, Any]) -> dict[str, Any]:
    """Liveの生値へ、Studio表示用の現在itemまたは正規化値を加える。"""
    low = _finite_float(parameter.get("min"))
    high = _finite_float(parameter.get("max"))
    current = _finite_float(parameter.get("value"))
    if (
        low is None
        or high is None
        or current is None
        or high < low
        or current < low
        or current > high
    ):
        raise ValueError("Liveが返したパラメータ範囲が不正です")
    items = [str(item) for item in parameter.get("value_items") or []]
    quantized = bool(items)
    view = {
        "name": str(parameter.get("name") or ""),
        "min": low,
        "max": high,
        "value": current,
        "is_quantized": quantized,
        "value_items": items,
    }
    if quantized and items:
        position = round(current - low)
        if not 0 <= position < len(items):
            raise ValueError("Liveが返した選択値が候補範囲外です")
        view["current_item"] = items[position]
    else:
        view["normalized_value"] = (
            round((current - low) / (high - low), 6) if high > low else 0.0
        )
    return view


def parameter_signature(parameter: dict[str, Any]) -> str:
    """プレビュー後に値・範囲・候補が変わっていないか比較する署名を返す。"""
    return canonical_hash(parameter_view(parameter))


def create_vital_parameter_plan(
    snapshot: LiveStateSnapshot,
    *,
    track_index: int,
    device_index: int,
    device_name: str,
    parameter: dict[str, Any],
    value: Any = None,
    item: Any = None,
    ttl_seconds: int = 300,
) -> LiveMutationPlan:
    """管理トラック上のVital公開パラメータ1件だけを変更する保留計画を作る。"""
    request_id = uuid4().hex
    conflicts: list[LiveConflict] = []
    track = next((row for row in snapshot.tracks if row.index == track_index), None)
    if track is None or not is_managed_name(track.name):
        conflicts.append(
            LiveConflict(
                "unmanaged_track",
                "KIHACHIが作成したトラック上のVitalだけ操作できます",
                {"track_index": track_index},
            )
        )
    elif track.is_frozen:
        conflicts.append(
            LiveConflict(
                "frozen_track",
                "フリーズ中のトラックは解除してから操作してください",
                {"track_index": track_index},
            )
        )

    observed_name = ""
    if track is not None and 0 <= device_index < len(track.device_names):
        observed_name = track.device_names[device_index]
    if (
        not is_vital_device(device_name)
        or not is_vital_device(observed_name)
        or observed_name != device_name
    ):
        conflicts.append(
            LiveConflict(
                "not_vital",
                "指定位置のデバイスがVitalではありません",
                {"track_index": track_index, "device_index": device_index},
            )
        )

    try:
        current = parameter_view(parameter)
    except ValueError as exc:
        current = {}
        conflicts.append(LiveConflict("invalid_parameter", str(exc)))
    parameter_name = str(current.get("name") or "")
    if not parameter_name:
        conflicts.append(
            LiveConflict("invalid_parameter", "パラメータ名を読み取れません")
        )

    arguments: dict[str, Any] = {
        "device_name": device_name,
        "parameter_name": parameter_name,
    }
    expected: dict[str, Any] = {
        "track_index": track_index,
        "device_index": device_index,
        "parameter_name": parameter_name,
    }
    target_label = ""
    if current.get("is_quantized"):
        target_item = str(item) if item is not None else ""
        choices = [str(choice) for choice in current.get("value_items") or []]
        if not target_item or target_item not in choices:
            conflicts.append(
                LiveConflict(
                    "invalid_item",
                    "選択値がLiveの公開候補にありません",
                    {"parameter_name": parameter_name},
                )
            )
        elif target_item == current.get("current_item"):
            conflicts.append(
                LiveConflict("unchanged_parameter", "選択値は現在値と同じです")
            )
        arguments["item"] = target_item
        expected["item"] = target_item
        target_label = target_item
    else:
        target_value = _finite_float(value)
        if target_value is None or not 0.0 <= target_value <= 1.0:
            conflicts.append(
                LiveConflict(
                    "invalid_value",
                    "連続パラメータは0から1の有限値で指定してください",
                    {"parameter_name": parameter_name},
                )
            )
            target_value = 0.0
        elif abs(target_value - float(current.get("normalized_value", 0.0))) <= 0.0005:
            conflicts.append(
                LiveConflict("unchanged_parameter", "指定値は現在値と同じです")
            )
        arguments["value"] = target_value
        expected["normalized_value"] = round(target_value, 3)
        target_label = f"{target_value:.3f}"

    source = {
        "track_index": track_index,
        "track_name": track.name if track is not None else "",
        "device_index": device_index,
        "device_name": device_name,
        "parameter": current,
        "target": target_label,
    }
    operations: list[LiveMutationOperation] = []
    if not conflicts and track is not None:
        operations.append(
            LiveMutationOperation(
                operation_id="001-set_device_parameter",
                op=OP_SET_DEVICE_PARAMETER,
                target={
                    "track_index": track.index,
                    "device_index": device_index,
                },
                arguments=arguments,
                preconditions=[
                    LivePrecondition("not_recording"),
                    LivePrecondition(
                        "track_name_at_index",
                        {"track_index": track.index, "name": track.name},
                    ),
                    LivePrecondition(
                        "device_name_at_index",
                        {
                            "track_index": track.index,
                            "device_index": device_index,
                            "name": device_name,
                        },
                    ),
                ],
                destructive=False,
                expected_readback=expected,
            )
        )
    source_hash = canonical_hash(source)
    return LiveMutationPlan(
        request_id=request_id,
        idempotency_key=canonical_hash(
            {"request_id": request_id, "source": source_hash}
        ),
        source_plan_hash=source_hash,
        set_fingerprint=snapshot.set_fingerprint,
        expires_at=(datetime.now(UTC) + timedelta(seconds=ttl_seconds)).isoformat(),
        operations=operations,
        conflicts=conflicts,
        warnings=[
            (
                f"{track.name if track is not None else '対象トラック'}の"
                f" Vital / {parameter_name or '不明なパラメータ'} を"
                f" {target_label} に変更します"
            )
        ],
    )


def _finite_float(value: Any) -> float | None:
    """boolを除く有限数だけをfloatへ変換する。"""
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None
