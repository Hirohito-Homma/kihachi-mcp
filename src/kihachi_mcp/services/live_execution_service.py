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

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from kihachi_mcp.models.live_contract import (
    OP_CREATE_LOCATOR,
    OP_PLACE_ARRANGEMENT_CLIP,
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
    METHOD_APPLY_OPERATION,
    LiveTransport,
    LiveTransportError,
    NullLiveTransport,
    build_message,
    decode_response,
)

_FLOAT_TOLERANCE = 1e-6
LOCATORS_UNREPORTED_FIELD = "locators_reported"
ARRANGEMENT_UNREPORTED_FIELD = "arrangement_clips_reported"
AMBIGUOUS_MATCH_FIELD = "unique_match"


class LiveExecutionService:
    """Execute one approved plan and return a receipt that cannot overclaim."""

    def __init__(
        self,
        transport: LiveTransport | None = None,
        inspector: LiveStateInspector | None = None,
        approval_gate: ApprovalGate | None = None,
        request_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._transport: LiveTransport = transport or NullLiveTransport()
        self._inspector = inspector or LiveStateInspector(self._transport)
        self._gate = approval_gate or ApprovalGate()
        self._sequence = 0
        self._request_id_factory = request_id_factory or self._default_request_id

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
        mismatches = readback_mismatches(plan, receipt, current)
        if mismatches:
            return self._rebuild(
                receipt,
                STATUS_VERIFICATION_FAILED,
                mismatches=mismatches,
                fingerprint_after=current.set_fingerprint,
                error=(
                    unconfirmed_reason(mismatches)
                    or "Live state no longer matches the receipt"
                ),
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

        for operation in plan.operations:
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


def _values_match(expected: Any, observed: Any) -> bool:
    """Compare one readback field, tolerating float representation."""
    if isinstance(expected, bool) or isinstance(observed, bool):
        return bool(expected) == bool(observed)
    if isinstance(expected, int | float) and isinstance(observed, int | float):
        return abs(float(expected) - float(observed)) <= _FLOAT_TOLERANCE
    return expected == observed


def _compare_readback(
    operation: LiveMutationOperation, observed: dict[str, Any]
) -> list[LiveReadbackMismatch]:
    """Return every expected field Live did not confirm."""
    mismatches: list[LiveReadbackMismatch] = []
    for field_name, expected in operation.expected_readback.items():
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
        if not _values_match(expected, observed[field_name]):
            mismatches.append(
                LiveReadbackMismatch(
                    operation_id=operation.operation_id,
                    field_name=field_name,
                    expected=expected,
                    observed=observed[field_name],
                )
            )
    return mismatches


def readback_mismatches(
    plan: LiveMutationPlan,
    receipt: LiveExecutionReceipt,
    snapshot: LiveStateSnapshot,
) -> list[LiveReadbackMismatch]:
    """Compare every completed operation of a receipt with one Live snapshot."""
    completed = set(receipt.completed_operations)
    operations = [op for op in plan.operations if op.operation_id in completed]
    pairs = _pair_arrangement(operations, snapshot)
    mismatches: list[LiveReadbackMismatch] = []
    for operation in operations:
        mismatches.extend(_verify_against_snapshot(operation, snapshot, pairs))
    return mismatches


def unconfirmed_reason(mismatches: list[LiveReadbackMismatch]) -> str:
    """Explain mismatches that mean "Live did not tell us", not "Live differs"."""
    fields = {item.field_name for item in mismatches}
    reasons = []
    if LOCATORS_UNREPORTED_FIELD in fields:
        reasons.append(
            "Live did not report locators, so the created locators are "
            "unconfirmed; update the KIHACHI Live Device"
        )
    if ARRANGEMENT_UNREPORTED_FIELD in fields:
        reasons.append(
            "Live did not report a readable Arrangement clip list, so the "
            "placed clips are unconfirmed"
        )
    return "; ".join(reasons)


def _verify_against_snapshot(
    operation: LiveMutationOperation,
    snapshot: LiveStateSnapshot,
    pairs: dict[str, Any],
) -> list[LiveReadbackMismatch]:
    """Check one applied operation against a freshly read Live snapshot."""
    expected = operation.expected_readback
    track_index = expected.get("track_index")
    scene_index = expected.get("scene_index")
    mismatches: list[LiveReadbackMismatch] = []

    if "tempo" in expected and not _values_match(expected["tempo"], snapshot.tempo):
        mismatches.append(
            LiveReadbackMismatch(
                operation.operation_id, "tempo", expected["tempo"], snapshot.tempo
            )
        )
    if (
        track_index is not None
        and scene_index is None
        and "name" in expected
        and operation.op != OP_PLACE_ARRANGEMENT_CLIP
    ):
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
    if operation.op == OP_PLACE_ARRANGEMENT_CLIP:
        # note_count is not compared: Live's Arrangement summary does not
        # count notes, so only the first, per-operation readback confirms it.
        mismatches.extend(
            _compare_paired(
                operation,
                pairs.get(operation.operation_id),
                ARRANGEMENT_UNREPORTED_FIELD,
                ("track_index", "name", "start_beats", "length_beats"),
            )
        )
    if operation.op == OP_CREATE_LOCATOR:
        mismatches.extend(
            _compare_paired(
                operation,
                pairs.get(operation.operation_id),
                LOCATORS_UNREPORTED_FIELD,
                ("name", "beats"),
            )
        )
    return mismatches


@dataclass(frozen=True)
class _Ambiguous:
    count: int


_UNREPORTED = object()


def _pair_arrangement(
    operations: list[LiveMutationOperation], snapshot: LiveStateSnapshot
) -> dict[str, Any]:
    """Pair each placed clip and created locator with at most one Live element."""
    pairs: dict[str, Any] = {}
    clips = [op for op in operations if op.op == OP_PLACE_ARRANGEMENT_CLIP]
    if clips and not snapshot.arrangement_clips_reported:
        pairs.update({op.operation_id: _UNREPORTED for op in clips})
    elif clips:
        pairs.update(
            _pair_unique(
                clips,
                list(snapshot.arrangement_clips),
                (
                    lambda want, clip: _same_track(want, clip)
                    and _values_match(want.get("name"), clip.name)
                    and _values_match(want.get("start_beats"), clip.start_beats),
                    lambda want, clip: _same_track(want, clip)
                    and _values_match(want.get("start_beats"), clip.start_beats),
                    lambda want, clip: _same_track(want, clip)
                    and _values_match(want.get("name"), clip.name),
                ),
            )
        )
    locators = [op for op in operations if op.op == OP_CREATE_LOCATOR]
    if locators and snapshot.locators is None:
        pairs.update({op.operation_id: _UNREPORTED for op in locators})
    elif locators:
        pairs.update(
            _pair_unique(
                locators,
                list(snapshot.locators or []),
                (
                    lambda want, item: _values_match(want.get("name"), item.name)
                    and _values_match(want.get("beats"), item.beats),
                    lambda want, item: _values_match(want.get("beats"), item.beats),
                    lambda want, item: _values_match(want.get("name"), item.name),
                ),
            )
        )
    return pairs


def _same_track(expected: dict[str, Any], clip: Any) -> bool:
    return _values_match(expected.get("track_index"), clip.track_index)


def _pair_unique(
    operations: list[LiveMutationOperation],
    observed: list[Any],
    tiers: tuple[Callable[[dict[str, Any], Any], bool], ...],
) -> dict[str, Any]:
    """Assign observed elements to operations, strictest rule first.

    An element is never given to two operations. When an exact match is not
    unique the operation is reported as ambiguous; a looser rule that is not
    unique leaves the operation unmatched, which reads as missing.
    """
    pairs: dict[str, Any] = {}
    claimed: set[int] = set()
    for tier_index, matches in enumerate(tiers):
        wanted = {
            op.operation_id: [
                index
                for index, item in enumerate(observed)
                if index not in claimed and matches(op.expected_readback, item)
            ]
            for op in operations
            if op.operation_id not in pairs
        }
        demand = Counter(index for hits in wanted.values() for index in hits)
        for operation_id, hits in wanted.items():
            if len(hits) == 1 and demand[hits[0]] == 1:
                pairs[operation_id] = observed[hits[0]]
                claimed.add(hits[0])
            elif tier_index == 0 and hits:
                pairs[operation_id] = _Ambiguous(
                    max(len(hits), max(demand[index] for index in hits))
                )
    for operation in operations:
        pairs.setdefault(operation.operation_id, None)
    return pairs


def _compare_paired(
    operation: LiveMutationOperation,
    paired: Any,
    unreported_field: str,
    fields: tuple[str, ...],
) -> list[LiveReadbackMismatch]:
    if paired is _UNREPORTED:
        return [
            LiveReadbackMismatch(operation.operation_id, unreported_field, True, False)
        ]
    if isinstance(paired, _Ambiguous):
        return [
            LiveReadbackMismatch(
                operation.operation_id, AMBIGUOUS_MATCH_FIELD, 1, paired.count
            )
        ]
    expected = operation.expected_readback
    mismatches: list[LiveReadbackMismatch] = []
    for field_name in fields:
        if field_name not in expected:
            continue
        observed = getattr(paired, field_name) if paired is not None else None
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
