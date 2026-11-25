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


def create_vital_batch_plan(
    snapshot: LiveStateSnapshot,
    *,
    track_index: int,
    device_index: int,
    device_name: str,
    parameters: list[dict[str, Any]],
    target_values: dict[str, Any],
    ttl_seconds: int = 300,
) -> tuple[LiveMutationPlan, dict[str, Any]]:
    """管理トラック上のVital公開パラメータ複数件（Macro 1〜4等）を一括変更する保留計画を作る。"""
    request_id = uuid4().hex
    conflicts: list[LiveConflict] = []
    warnings: list[str] = []

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

    changes: list[dict[str, Any]] = []
    skipped_unexposed: list[str] = []
    skipped_unchanged: list[dict[str, Any]] = []

    for param_name, raw_value in target_values.items():
        name = str(param_name or "").strip()
        if not name:
            continue
        matching = [
            row
            for row in parameters
            if isinstance(row, dict) and str(row.get("name") or "") == name
        ]
        if not matching:
            skipped_unexposed.append(name)
            continue
        if len(matching) > 1:
            conflicts.append(
                LiveConflict(
                    "duplicate_parameter",
                    f"同名の公開パラメータ '{name}' が複数あるため、安全に対象を特定できません",
                    {"parameter_name": name},
                )
            )
            continue
        raw_param = matching[0]
        try:
            current = parameter_view(raw_param)
            sig = parameter_signature(raw_param)
        except ValueError as exc:
            conflicts.append(
                LiveConflict("invalid_parameter", str(exc), {"parameter_name": name})
            )
            continue
        if current.get("is_quantized"):
            conflicts.append(
                LiveConflict(
                    "invalid_parameter",
                    f"'{name}' は連続パラメータではないため一括推定の対象外です",
                    {"parameter_name": name},
                )
            )
            continue
        target_value = _finite_float(raw_value)
        if target_value is None or not 0.0 <= target_value <= 1.0:
            conflicts.append(
                LiveConflict(
                    "invalid_value",
                    f"'{name}' は0から1の有限値で指定してください",
                    {"parameter_name": name},
                )
            )
            continue
        target_value = round(target_value, 3)
        current_norm = float(current.get("normalized_value", 0.0))
        if abs(target_value - current_norm) <= 0.0005:
            skipped_unchanged.append(
                {
                    "parameter_name": name,
                    "current_value": current_norm,
                    "target_value": target_value,
                }
            )
            continue
        changes.append(
            {
                "parameter_name": name,
                "current": current,
                "current_value": current_norm,
                "target_value": target_value,
                "parameter_signature": sig,
            }
        )

    if skipped_unexposed:
        warnings.append(
            "未公開のためスキップ: "
            + "、".join(skipped_unexposed)
            + "（LiveのConfigureで登録すると一括適用対象になります）"
        )

    if not conflicts and not changes:
        if skipped_unchanged:
            conflicts.append(
                LiveConflict(
                    "unchanged_parameter",
                    "推定値は公開中のVital現在値とすべて同じです",
                )
            )
        else:
            conflicts.append(
                LiveConflict(
                    "no_exposed_macros",
                    "対象のMacro（Macro 1〜4）がVitalから公開されていません。"
                    "LiveのConfigureでMacro 1〜4を登録してください",
                )
            )

    operations: list[LiveMutationOperation] = []
    if not conflicts and track is not None:
        for idx, item in enumerate(changes, start=1):
            operations.append(
                LiveMutationOperation(
                    operation_id=f"{idx:03d}-set_device_parameter",
                    op=OP_SET_DEVICE_PARAMETER,
                    target={
                        "track_index": track.index,
                        "device_index": device_index,
                    },
                    arguments={
                        "device_name": device_name,
                        "parameter_name": item["parameter_name"],
                        "value": item["target_value"],
                    },
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
                    expected_readback={
                        "track_index": track_index,
                        "device_index": device_index,
                        "parameter_name": item["parameter_name"],
                        "normalized_value": round(item["target_value"], 3),
                    },
                )
            )
        summary_pairs = " / ".join(
            f"{item['parameter_name']}: {item['current_value']:.3f} → {item['target_value']:.3f}"
            for item in changes
        )
        warnings.insert(
            0,
            f"{track.name} の Vital を一括変更します（{summary_pairs}）",
        )

    source = {
        "track_index": track_index,
        "track_name": track.name if track is not None else "",
        "device_index": device_index,
        "device_name": device_name,
        "changes": [
            {
                "parameter_name": item["parameter_name"],
                "current_value": item["current_value"],
                "target_value": item["target_value"],
                "parameter_signature": item["parameter_signature"],
            }
            for item in changes
        ],
    }
    source_hash = canonical_hash(source)
    plan = LiveMutationPlan(
        request_id=request_id,
        idempotency_key=canonical_hash(
            {"request_id": request_id, "source": source_hash}
        ),
        source_plan_hash=source_hash,
        set_fingerprint=snapshot.set_fingerprint,
        expires_at=(datetime.now(UTC) + timedelta(seconds=ttl_seconds)).isoformat(),
        operations=operations,
        conflicts=conflicts,
        warnings=warnings,
    )
    details = {
        "changes": changes,
        "skipped_unexposed": skipped_unexposed,
        "skipped_unchanged": skipped_unchanged,
    }
    return plan, details


def _finite_float(value: Any) -> float | None:
    """boolを除く有限数だけをfloatへ変換する。"""
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None
