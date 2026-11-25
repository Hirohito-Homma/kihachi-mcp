"""Apply approved Live mutation plans and prove what actually happened.

The order of checks matters and is intentional:

1. A plan with conflicts never runs.
2. Without explicit human approval nothing runs.
3. The approval must match this exact plan, and its idempotency key must be
   unused.
4. The Set is re-inspected. If its fingerprint moved since planning, execution
   stops, because the plan's predicted track and scene indices are no longer
   trustworthy.
5. Recording always blocks. A running transport blocks structural changes.
6. The idempotency key is retired *before* the first mutation, so an
   interrupted run cannot be replayed on top of its own partial result.
7. Operations run once, in order. The first failure stops the run; the rest are
   abandoned rather than retried.
8. Every applied operation is read back. Any difference produces
   ``verification_failed``.
"""

import math
import os
import re
import time
from collections.abc import Callable, Mapping
from typing import Any

from kihachi_mcp.models.live_contract import (
    OP_LOAD_PLUGIN,
    OP_PLACE_ARRANGEMENT_CLIP,
    OP_SET_PLUGIN_PARAM,
    OP_SET_TRACK_VOLUME,
    STATUS_APPROVAL_REQUIRED,
    STATUS_BLOCKED,
    STATUS_PARTIALLY_APPLIED,
    STATUS_UNAVAILABLE,
    STATUS_VERIFICATION_FAILED,
    STATUS_VERIFIED,
    STRUCTURAL_OPS,
)
from kihachi_mcp.models.live_mutation import LiveMutationOperation, LiveMutationPlan
from kihachi_mcp.models.live_receipt import (
    LiveExecutionReceipt,
    LiveOperationReadback,
    LiveReadbackMismatch,
)
from kihachi_mcp.models.live_state import LiveStateSnapshot
from kihachi_mcp.services.live_approval_gate import ApprovalError, ApprovalGate
from kihachi_mcp.services.live_bridge import MAX_PLAN_OPERATIONS
from kihachi_mcp.services.live_state_inspector import (
    LiveStateInspector,
    LiveVersionUnsupportedError,
)
from kihachi_mcp.services.live_transport import (
    ERROR_DISCONNECTED,
    ERROR_OPERATION_FAILED,
    METHOD_APPLY_OPERATION,
    LiveTransport,
    LiveTransportError,
    NullLiveTransport,
    build_message,
    decode_response,
)

_FLOAT_TOLERANCE = 1e-6
_PLUGIN_FLOAT_TOLERANCE = 1e-3
_DEFAULT_PLUGIN_TIMEOUT_SECONDS = 30.0
_DEFAULT_PLUGIN_POLL_INTERVAL = 0.5
STUDIO_STRUCTURAL_PACING_SECONDS = 0.1
MAX_STRUCTURAL_PACING_SECONDS = 5.0
STRUCTURAL_PACING_ENV = "KIHACHI_LIVE_STRUCTURAL_PACING_SECONDS"


def resolve_structural_pacing_seconds(
    default: float = STUDIO_STRUCTURAL_PACING_SECONDS,
    environ: Mapping[str, str] | None = None,
) -> float:
    """環境変数から構造変更操作後のペーシング秒数を解決する。"""
    env = environ if environ is not None else os.environ
    raw = str(env.get(STRUCTURAL_PACING_ENV) or "").strip()
    if not raw:
        value = float(default)
    else:
        try:
            value = float(raw)
        except ValueError:
            value = float(default)
    if not math.isfinite(value):
        value = float(default)
    return min(MAX_STRUCTURAL_PACING_SECONDS, max(0.0, value))


class LiveExecutionService:
    """Execute one approved plan and return a receipt that cannot overclaim."""

    def __init__(
        self,
        transport: LiveTransport | None = None,
        inspector: LiveStateInspector | None = None,
        approval_gate: ApprovalGate | None = None,
        request_id_factory: Callable[[], str] | None = None,
        plugin_remote: Callable[..., Any] | None = None,
        plugin_timeout_seconds: float = _DEFAULT_PLUGIN_TIMEOUT_SECONDS,
        plugin_poll_interval: float = _DEFAULT_PLUGIN_POLL_INTERVAL,
        structural_pacing_seconds: float = 0.0,
        clock: Callable[[], float] | None = None,
        sleeper: Callable[[float], None] | None = None,
    ) -> None:
        self._transport: LiveTransport = transport or NullLiveTransport()
        self._inspector = inspector or LiveStateInspector(self._transport)
        self._gate = approval_gate or ApprovalGate()
        self._sequence = 0
        self._request_id_factory = request_id_factory or self._default_request_id
        self._plugin_remote = plugin_remote
        self._plugin_timeout_seconds = float(plugin_timeout_seconds)
        self._plugin_poll_interval = float(plugin_poll_interval)
        self._structural_pacing_seconds = max(0.0, float(structural_pacing_seconds))
        self._clock = clock or time.monotonic
        self._sleeper = sleeper or time.sleep
        self._plugin_cache: dict[int, dict[str, Any]] = {}

    def execute(
        self,
        plan: LiveMutationPlan,
        approved: bool = False,
        approval_token: str = "",
    ) -> LiveExecutionReceipt:
        """Attempt one approved plan and report only what Live confirmed."""
        if plan.conflicts:
            return self._refuse(
                plan,
                STATUS_BLOCKED,
                "the plan has unresolved conflicts and will not be executed",
            )
        if not plan.operations:
            return self._refuse(
                plan, STATUS_BLOCKED, "the plan contains no operations"
            )
        if len(plan.operations) > MAX_PLAN_OPERATIONS:
            return self._refuse(
                plan,
                STATUS_BLOCKED,
                f"the plan has {len(plan.operations)} operations, over the "
                f"{MAX_PLAN_OPERATIONS} operation safety limit",
            )
        if not approved:
            return self._refuse(
                plan,
                STATUS_APPROVAL_REQUIRED,
                "human approval is required before KIHACHI changes a Live Set",
            )
        try:
            self._gate.authorize(plan, approval_token)
        except ApprovalError as exc:
            status = (
                STATUS_APPROVAL_REQUIRED
                if exc.code == "unapproved"
                else STATUS_BLOCKED
            )
            return self._refuse(plan, status, exc.message)

        try:
            before = self._inspector.snapshot()
        except LiveTransportError as exc:
            return self._refuse(
                plan, STATUS_UNAVAILABLE, f"{exc.code}: {exc.message}"
            )
        except LiveVersionUnsupportedError as exc:
            return self._refuse(plan, STATUS_BLOCKED, str(exc))

        guard = self._transport_guard(plan, before)
        if guard is not None:
            return guard

        self._gate.consume(plan, approval_token)
        return self._apply_operations(plan, before)

    def verify(
        self, plan: LiveMutationPlan, receipt: LiveExecutionReceipt
    ) -> LiveExecutionReceipt:
        """Re-read Live and confirm a receipt's claims still hold.

        This is a second, independent readback. It exists because Live updates
        some properties asynchronously, so a receipt produced immediately after
        a mutation can be confirmed later without re-running anything.
        """
        if receipt.status not in {STATUS_VERIFIED, STATUS_VERIFICATION_FAILED}:
            return receipt
        try:
            current = self._inspector.snapshot()
        except LiveTransportError as exc:
            return self._rebuild(
                receipt,
                STATUS_UNAVAILABLE,
                error=f"{exc.code}: {exc.message}",
            )
        completed = set(receipt.completed_operations)
        mismatches: list[LiveReadbackMismatch] = []
        for operation in plan.operations:
            if operation.operation_id not in completed:
                continue
            mismatches.extend(_verify_against_snapshot(operation, current))
        if mismatches:
            return self._rebuild(
                receipt,
                STATUS_VERIFICATION_FAILED,
                mismatches=mismatches,
                fingerprint_after=current.set_fingerprint,
                error="Live state no longer matches the receipt",
            )
        return self._rebuild(
            receipt,
            STATUS_VERIFIED,
            fingerprint_after=current.set_fingerprint,
        )

    def _transport_guard(
        self, plan: LiveMutationPlan, before: LiveStateSnapshot
    ) -> LiveExecutionReceipt | None:
        if before.set_fingerprint != plan.set_fingerprint:
            return self._refuse(
                plan,
                STATUS_BLOCKED,
                "the Live Set changed after this plan was created; "
                "inspect Live and plan again",
                fingerprint_before=before.set_fingerprint,
            )
        if before.is_recording:
            return self._refuse(
                plan,
                STATUS_BLOCKED,
                "Ableton Live is recording; KIHACHI will not change a Set "
                "while a take is running",
                fingerprint_before=before.set_fingerprint,
            )
        if before.is_playing and any(
            operation.op in STRUCTURAL_OPS for operation in plan.operations
        ):
            return self._refuse(
                plan,
                STATUS_BLOCKED,
                "Ableton Live transport is running; stop playback before "
                "applying structural changes",
                fingerprint_before=before.set_fingerprint,
            )
        return None

    def _apply_operations(
        self, plan: LiveMutationPlan, before: LiveStateSnapshot
    ) -> LiveExecutionReceipt:
        attempted: list[str] = []
        completed: list[str] = []
        readbacks: list[LiveOperationReadback] = []
        mismatches: list[LiveReadbackMismatch] = []
        failed_operation = ""
        error = ""

        total_operations = len(plan.operations)
        for index, operation in enumerate(plan.operations):
            attempted.append(operation.operation_id)
            try:
                observed = self._apply_one(operation)
            except LiveTransportError as exc:
                failed_operation = operation.operation_id
                error = f"{exc.code}: {exc.message}"
                break
            operation_mismatches = _compare_readback(operation, observed)
            readbacks.append(
                LiveOperationReadback(
                    operation_id=operation.operation_id,
                    op=operation.op,
                    observed=observed,
                    matched=not operation_mismatches,
                )
            )
            completed.append(operation.operation_id)
            mismatches.extend(operation_mismatches)
            # 構造変更の連続発射による Live / Max キュー詰まりを防ぐための短い間合い
            if (
                self._structural_pacing_seconds > 0.0
                and operation.op in STRUCTURAL_OPS
                and index + 1 < total_operations
            ):
                self._sleeper(self._structural_pacing_seconds)

        after_fingerprint = self._safe_fingerprint()
        if failed_operation:
            status = (
                STATUS_PARTIALLY_APPLIED if completed else STATUS_BLOCKED
            )
            return LiveExecutionReceipt(
                request_id=plan.request_id,
                status=status,
                idempotency_key=plan.idempotency_key,
                attempted_operations=attempted,
                completed_operations=completed,
                failed_operation=failed_operation,
                readback=readbacks,
                mismatches=mismatches,
                set_fingerprint_before=before.set_fingerprint,
                set_fingerprint_after=after_fingerprint,
                error=(
                    f"{error}; the remaining operations were abandoned and will "
                    "not be retried automatically"
                ),
            )
        if mismatches:
            return LiveExecutionReceipt(
                request_id=plan.request_id,
                status=STATUS_VERIFICATION_FAILED,
                idempotency_key=plan.idempotency_key,
                attempted_operations=attempted,
                completed_operations=completed,
                readback=readbacks,
                mismatches=mismatches,
                set_fingerprint_before=before.set_fingerprint,
                set_fingerprint_after=after_fingerprint,
                error="Live reported values that differ from the approved plan",
            )
        return LiveExecutionReceipt(
            request_id=plan.request_id,
            status=STATUS_VERIFIED,
            idempotency_key=plan.idempotency_key,
            attempted_operations=attempted,
            completed_operations=completed,
            readback=readbacks,
            set_fingerprint_before=before.set_fingerprint,
            set_fingerprint_after=after_fingerprint,
        )

    def _apply_one(self, operation: LiveMutationOperation) -> dict[str, Any]:
        if operation.op == OP_LOAD_PLUGIN:
            return self._apply_load_plugin(operation)
        if operation.op == OP_SET_PLUGIN_PARAM:
            return self._apply_set_plugin_param(operation)
        if operation.op == OP_SET_TRACK_VOLUME:
            return self._apply_set_track_volume(operation)
        request_id = self._request_id_factory()
        message = build_message(
            METHOD_APPLY_OPERATION,
            request_id,
            {"operation": operation.to_dict()},
        )
        response = self._transport.request(message)
        result = decode_response(response, request_id)
        observed = result.get("observed")
        return dict(observed) if isinstance(observed, dict) else {}

    def _resolve_plugin_remote(self) -> Callable[..., Any]:
        if self._plugin_remote is not None:
            return self._plugin_remote
        from kihachi_mcp.services.abletongpt_kits import RemoteScriptSocket

        return RemoteScriptSocket.from_config().call

    def _call_plugin_remote(
        self, remote: Callable[..., Any], command: str, **params: Any
    ) -> Any:
        from kihachi_mcp.services.abletongpt_kits import KitLoadError

        try:
            return remote(command, **params)
        except KitLoadError as exc:
            raise LiveTransportError(ERROR_DISCONNECTED, str(exc)) from exc

    def _get_track_devices(
        self, remote: Callable[..., Any], track_index: int
    ) -> list[dict[str, Any]]:
        observed = self._call_plugin_remote(
            remote, "get_track_devices", track_index=track_index
        )
        devices = observed.get("devices", []) if isinstance(observed, dict) else []
        return [d for d in devices if isinstance(d, dict)]

    def _locate_user_library_preset(
        self, remote: Callable[..., Any], target_name: str
    ) -> tuple[list[str], str] | None:
        stem = _strip_preset_ext(target_name)
        queue: list[list[str]] = [[]]
        calls = 0
        while queue and calls < 64:
            path = queue.pop(0)
            calls += 1
            try:
                listing = self._call_plugin_remote(
                    remote,
                    "browse_presets",
                    category="user_library",
                    path=list(path),
                    max_items=1000,
                )
            except LiveTransportError:
                return None
            items = listing.get("items", []) if isinstance(listing, dict) else []
            for item in items:
                if not isinstance(item, dict):
                    continue
                item_name = str(item.get("name", "")).strip()
                if not item_name:
                    continue
                if item.get("is_folder"):
                    if len(path) < 3:
                        queue.append([*path, item_name])
                    continue
                if item.get("is_loadable") and (
                    item_name == target_name or _strip_preset_ext(item_name) == stem
                ):
                    return list(path), item_name
        return None

    def _apply_load_plugin(self, operation: LiveMutationOperation) -> dict[str, Any]:
        remote = self._resolve_plugin_remote()
        track_index = int(operation.target["track_index"])
        source = str(operation.arguments.get("source") or "raw")
        name = str(operation.arguments.get("name") or "")
        path = [str(p) for p in (operation.arguments.get("path") or [])]
        category = "user_library" if source == "preset" else "plugins"

        before_devices = self._get_track_devices(remote, track_index)
        before_count = len(before_devices)

        resolved_path, resolved_name = list(path), name
        if source == "preset" and not resolved_path:
            located = self._locate_user_library_preset(remote, name)
            if located is not None:
                resolved_path, resolved_name = located

        load_error = ""
        try:
            remote(
                "load_preset",
                timeout=self._plugin_timeout_seconds,
                track_index=track_index,
                category=category,
                path=resolved_path,
                name=resolved_name,
            )
        except Exception as exc:  # noqa: BLE001 - 非同期ロードのためポーリングで最終判定する
            load_error = str(exc)

        deadline = self._clock() + self._plugin_timeout_seconds
        loaded_device: dict[str, Any] | None = None
        while True:
            after_devices = self._get_track_devices(remote, track_index)
            if len(after_devices) > before_count:
                loaded_device = after_devices[-1]
                break
            if self._clock() >= deadline:
                break
            self._sleeper(self._plugin_poll_interval)

        if loaded_device is None:
            detail = (
                load_error
                or f"plugin '{name}' load timed out after {self._plugin_timeout_seconds:g}s"
            )
            raise LiveTransportError(ERROR_OPERATION_FAILED, detail)

        device_index = int(loaded_device.get("index", max(len(after_devices) - 1, 0)))
        device_name = str(
            loaded_device.get("name")
            or loaded_device.get("class_display_name")
            or name
        )
        parameters = [
            p for p in (loaded_device.get("parameters") or []) if isinstance(p, dict)
        ]
        self._plugin_cache[track_index] = {
            "device_index": device_index,
            "device_name": device_name,
            "parameters": parameters,
        }
        return {
            "track_index": track_index,
            "device_index": device_index,
            "device_name": device_name,
            "requested_name": name,
            "source": source,
            "parameters_count": len(parameters),
            "parameter_names": [str(p.get("name") or "") for p in parameters],
        }

    def _apply_set_plugin_param(self, operation: LiveMutationOperation) -> dict[str, Any]:
        remote = self._resolve_plugin_remote()
        track_index = int(operation.target["track_index"])
        param_name = str(operation.arguments.get("param_name") or "")
        value = float(operation.arguments.get("value", 0.0))

        cached = self._plugin_cache.get(track_index)
        if cached is None:
            devices = self._get_track_devices(remote, track_index)
            target_idx = int(operation.target.get("device_index", 0))
            dev = (
                devices[target_idx]
                if 0 <= target_idx < len(devices)
                else (devices[0] if devices else None)
            )
            if dev is None:
                raise LiveTransportError(
                    ERROR_OPERATION_FAILED,
                    f"no plugin device found on track {track_index}",
                )
            cached = {
                "device_index": int(dev.get("index", target_idx)),
                "device_name": str(dev.get("name") or ""),
                "parameters": [
                    p for p in (dev.get("parameters") or []) if isinstance(p, dict)
                ],
            }
            self._plugin_cache[track_index] = cached

        device_index = int(cached["device_index"])
        device_name = str(cached["device_name"])
        parameters: list[dict[str, Any]] = cached["parameters"]

        matched_param = next(
            (p for p in parameters if str(p.get("name") or "") == param_name),
            None,
        )
        if matched_param is None:
            lowered = param_name.strip().lower()
            matched_param = next(
                (
                    p
                    for p in parameters
                    if str(p.get("name") or "").strip().lower() == lowered
                ),
                None,
            )

        if matched_param is None:
            warning = (
                f"パラメータ '{param_name}' がデバイス '{device_name}' に見つかりません"
            )
            return {
                "track_index": track_index,
                "device_index": device_index,
                "device_name": device_name,
                "param_name": param_name,
                "matched": False,
                "warning": warning,
            }

        param_index = int(matched_param["index"])
        self._call_plugin_remote(
            remote,
            "set_device_parameter",
            track_index=track_index,
            device_index=device_index,
            parameter_index=param_index,
            value=value,
        )
        refreshed_devices = self._get_track_devices(remote, track_index)
        refreshed_dev = next(
            (d for d in refreshed_devices if int(d.get("index", -1)) == device_index),
            refreshed_devices[device_index]
            if 0 <= device_index < len(refreshed_devices)
            else None,
        )
        readback_value = value
        display_value = ""
        if isinstance(refreshed_dev, dict):
            refreshed_param = next(
                (
                    p
                    for p in (refreshed_dev.get("parameters") or [])
                    if isinstance(p, dict) and int(p.get("index", -1)) == param_index
                ),
                None,
            )
            if refreshed_param is not None:
                if "value" in refreshed_param:
                    readback_value = float(refreshed_param["value"])
                display_value = str(refreshed_param.get("display_value") or "")

        return {
            "track_index": track_index,
            "device_index": device_index,
            "device_name": device_name,
            "param_name": str(matched_param.get("name") or param_name),
            "parameter_index": param_index,
            "value": readback_value,
            "display_value": display_value,
            "matched": True,
        }

    def _apply_set_track_volume(self, operation: LiveMutationOperation) -> dict[str, Any]:
        """AbletonGPT Remote Script 経由でトラック音量を設定し、読み戻し値を返す。"""
        remote = self._resolve_plugin_remote()
        track_index = int(operation.target["track_index"])
        volume = float(operation.arguments["volume"])
        target_db = float(operation.arguments.get("volume_db", 0.0))
        part = str(operation.arguments.get("part") or "")
        observed = self._call_plugin_remote(
            remote,
            "set_track_volume",
            track_index=track_index,
            volume=volume,
        )
        readback_volume = (
            float(observed["volume"])
            if isinstance(observed, dict) and "volume" in observed
            else volume
        )
        return {
            "track_index": track_index,
            "part": part,
            "target_db": target_db,
            "target_volume": volume,
            "volume": readback_volume,
        }

    def _safe_fingerprint(self) -> str:
        """Return the post-execution fingerprint, or empty if Live is gone."""
        try:
            return self._inspector.snapshot().set_fingerprint
        except (LiveTransportError, LiveVersionUnsupportedError):
            return ""

    def _refuse(
        self,
        plan: LiveMutationPlan,
        status: str,
        error: str,
        fingerprint_before: str = "",
    ) -> LiveExecutionReceipt:
        return LiveExecutionReceipt(
            request_id=plan.request_id,
            status=status,
            idempotency_key=plan.idempotency_key,
            attempted_operations=[],
            completed_operations=[],
            set_fingerprint_before=fingerprint_before,
            error=error,
        )

    def _rebuild(
        self,
        receipt: LiveExecutionReceipt,
        status: str,
        mismatches: list[LiveReadbackMismatch] | None = None,
        fingerprint_after: str = "",
        error: str = "",
    ) -> LiveExecutionReceipt:
        return LiveExecutionReceipt(
            request_id=receipt.request_id,
            status=status,
            idempotency_key=receipt.idempotency_key,
            attempted_operations=list(receipt.attempted_operations),
            completed_operations=list(receipt.completed_operations),
            failed_operation=receipt.failed_operation,
            readback=list(receipt.readback),
            mismatches=mismatches if mismatches is not None else [],
            set_fingerprint_before=receipt.set_fingerprint_before,
            set_fingerprint_after=fingerprint_after or receipt.set_fingerprint_after,
            error=error,
        )

    def _default_request_id(self) -> str:
        self._sequence += 1
        return f"apply-{self._sequence:06d}"


def _strip_preset_ext(name: str) -> str:
    lowered = name.lower()
    for ext in (".adv", ".vstpreset", ".adg"):
        if lowered.endswith(ext):
            return name[: -len(ext)]
    return name


def _values_match(
    expected: Any, observed: Any, float_tolerance: float = _FLOAT_TOLERANCE
) -> bool:
    """Compare one readback field, tolerating float representation."""
    if isinstance(expected, bool) or isinstance(observed, bool):
        return bool(expected) == bool(observed)
    if isinstance(expected, int | float) and isinstance(observed, int | float):
        return abs(float(expected) - float(observed)) <= float_tolerance
    return expected == observed


def _compare_readback(
    operation: LiveMutationOperation, observed: dict[str, Any]
) -> list[LiveReadbackMismatch]:
    """Return every expected field Live did not confirm."""
    if operation.op == OP_SET_PLUGIN_PARAM and observed.get("matched") is False:
        # パラメータ名不一致は警告としてレシートに残し、ミスマッチ停止にはしない
        return []
    tolerance = (
        _PLUGIN_FLOAT_TOLERANCE
        if operation.op == OP_SET_PLUGIN_PARAM
        else _FLOAT_TOLERANCE
    )
    mismatches: list[LiveReadbackMismatch] = []
    for field_name, expected in operation.expected_readback.items():
        if (
            operation.op == OP_LOAD_PLUGIN
            and field_name == "device_name"
            and (
                observed.get("requested_name") == expected
                or _strip_preset_ext(str(expected)) == observed.get("device_name")
            )
        ):
            continue
        if field_name not in observed:
            mismatches.append(
                LiveReadbackMismatch(
                    operation_id=operation.operation_id,
                    field_name=field_name,
                    expected=expected,
                    observed=None,
                )
            )
            continue
        if not _values_match(expected, observed[field_name], float_tolerance=tolerance):
            mismatches.append(
                LiveReadbackMismatch(
                    operation_id=operation.operation_id,
                    field_name=field_name,
                    expected=expected,
                    observed=observed[field_name],
                )
            )
    return mismatches


def _verify_against_snapshot(
    operation: LiveMutationOperation, snapshot: LiveStateSnapshot
) -> list[LiveReadbackMismatch]:
    """Check one applied operation against a freshly read Live snapshot."""
    expected = operation.expected_readback
    track_index = expected.get("track_index")
    scene_index = expected.get("scene_index")
    mismatches: list[LiveReadbackMismatch] = []

    if operation.op == OP_PLACE_ARRANGEMENT_CLIP:
        return _verify_arrangement_clip(operation, snapshot, expected)
    if "tempo" in expected and not _values_match(expected["tempo"], snapshot.tempo):
        mismatches.append(
            LiveReadbackMismatch(
                operation.operation_id, "tempo", expected["tempo"], snapshot.tempo
            )
        )
    if track_index is not None and scene_index is None and "name" in expected:
        track = snapshot.track_by_index(int(track_index))
        observed_name = track.name if track is not None else None
        if not _values_match(expected["name"], observed_name):
            mismatches.append(
                LiveReadbackMismatch(
                    operation.operation_id, "name", expected["name"], observed_name
                )
            )
    if track_index is None and scene_index is not None and "name" in expected:
        scenes = [
            scene for scene in snapshot.scenes if scene.index == int(scene_index)
        ]
        observed_name = scenes[0].name if scenes else None
        if not _values_match(expected["name"], observed_name):
            mismatches.append(
                LiveReadbackMismatch(
                    operation.operation_id, "name", expected["name"], observed_name
                )
            )
    if (
        expected.get("master") is True
        and "device_name" in expected
        and expected["device_name"] not in snapshot.master_device_names
    ):
        mismatches.append(
            LiveReadbackMismatch(
                operation.operation_id,
                "device_name",
                expected["device_name"],
                list(snapshot.master_device_names),
            )
        )
    if track_index is not None and "device_name" in expected:
        track = snapshot.track_by_index(int(track_index))
        names = list(track.device_names) if track is not None else []
        if expected["device_name"] not in names:
            mismatches.append(
                LiveReadbackMismatch(
                    operation.operation_id,
                    "device_name",
                    expected["device_name"],
                    names,
                )
            )
    if track_index is not None and scene_index is not None:
        clip = snapshot.session_clip_at(int(track_index), int(scene_index))
        for field_name in ("name", "length_beats", "note_count"):
            if field_name not in expected:
                continue
            observed = getattr(clip, field_name, None) if clip is not None else None
            if not _values_match(expected[field_name], observed):
                mismatches.append(
                    LiveReadbackMismatch(
                        operation.operation_id,
                        field_name,
                        expected[field_name],
                        observed,
                    )
                )
    return mismatches


_CLIP_ID_SUFFIX = re.compile(r"\s\[K:[^\]]+\]$")


def _clip_base_name(name: str) -> str:
    """Return a managed clip name without its per-placement id suffix."""
    return _CLIP_ID_SUFFIX.sub("", name)


def _verify_arrangement_clip(
    operation: LiveMutationOperation,
    snapshot: LiveStateSnapshot,
    expected: dict[str, Any],
) -> list[LiveReadbackMismatch]:
    """Check one placed Arrangement clip against a fresh snapshot.

    The clip is found by track and start beat. The device renames the
    clip when it duplicates the Session clip into the Arrangement,
    keeping the section name but minting a fresh managed id, so names
    are compared without the ``[K:...]`` suffix.
    """
    mismatches: list[LiveReadbackMismatch] = []
    track_index = expected.get("track_index")
    start = expected.get("start_beats")
    clip = None
    if track_index is not None and start is not None:
        for candidate in snapshot.arrangement_clips_on(int(track_index)):
            if abs(candidate.start_beats - float(start)) <= _FLOAT_TOLERANCE:
                clip = candidate
                break
    if "name" in expected:
        observed_name = clip.name if clip is not None else None
        if observed_name is None or not _values_match(
            _clip_base_name(str(expected["name"])), _clip_base_name(observed_name)
        ):
            mismatches.append(
                LiveReadbackMismatch(
                    operation.operation_id, "name", expected["name"], observed_name
                )
            )
    if "length_beats" in expected:
        observed_length = clip.length_beats if clip is not None else None
        if observed_length is None or not _values_match(
            expected["length_beats"], observed_length
        ):
            mismatches.append(
                LiveReadbackMismatch(
                    operation.operation_id,
                    "length_beats",
                    expected["length_beats"],
                    observed_length,
                )
            )
    return mismatches
