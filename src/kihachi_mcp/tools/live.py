"""MCP tools for the Ableton Live automation boundary.

These functions are thin JSON adapters. Every planning, safety, and
verification decision lives in ``kihachi_mcp.services``; nothing here talks to
Live directly and nothing here decides whether a change is safe.

Approval is deliberately out of band. ``request_live_execution`` never returns
the approval token: it writes the token to an owner-only file and logs where to
find it, so a person has to read it and hand it to ``execute_live_request``. An
assistant holding only the tool output cannot approve its own plan.

The process-local session below starts with no transport, so a freshly launched
server cannot mutate a Set until one is configured.
"""

import json
import logging
import os
from pathlib import Path
from typing import Any

from kihachi_mcp.models.live_contract import (
    SCHEMA_VERSION,
    STATUS_BLOCKED,
    STATUS_UNAVAILABLE,
    LiveContractError,
)
from kihachi_mcp.models.live_mutation import LiveMutationPlan
from kihachi_mcp.models.live_receipt import LiveExecutionReceipt
from kihachi_mcp.models.live_state import LiveStateSnapshot
from kihachi_mcp.services.arrangement_expander import ArrangementExpander
from kihachi_mcp.services.live_approval_gate import ApprovalError, ApprovalGate
from kihachi_mcp.services.live_capabilities import discover_capabilities
from kihachi_mcp.services.live_device_catalog import catalogue
from kihachi_mcp.services.live_execution_service import LiveExecutionService
from kihachi_mcp.services.live_mutation_planner import LiveMutationPlanner
from kihachi_mcp.services.live_paths import bridge_state_dir
from kihachi_mcp.services.live_state_inspector import (
    LiveStateInspector,
    LiveVersionUnsupportedError,
)
from kihachi_mcp.services.live_transport import LiveTransport, LiveTransportError

APPROVAL_FILENAME = "pending-approval.json"

_logger = logging.getLogger("kihachi.live.tools")


class _LiveSession:
    """Process-local wiring for the Live tools."""

    def __init__(self) -> None:
        self.transport: LiveTransport | None = None
        self.gate = ApprovalGate()
        self.inspector = LiveStateInspector()
        self.planner = LiveMutationPlanner()
        self.expander = ArrangementExpander()
        self.executor = LiveExecutionService(approval_gate=self.gate)

    def configure(self, transport: LiveTransport | None) -> None:
        """Install or clear the transport used by every Live tool."""
        self.transport = transport
        self.inspector = LiveStateInspector(transport)
        self.executor = LiveExecutionService(
            transport=transport,
            inspector=self.inspector,
            approval_gate=self.gate,
        )


_session = _LiveSession()


def configure_live_transport(transport: LiveTransport | None) -> None:
    """Install the transport used by every Live tool in this process."""
    _session.configure(transport)


def live_device_catalogue() -> dict[str, Any]:
    """Return the Live stock devices KIHACHI is allowed to load."""
    return {
        "schema_version": SCHEMA_VERSION,
        "devices": catalogue(),
        "external_plugins_supported": False,
        "note": (
            "Availability differs by Live edition and version. The authoritative "
            "check is the device list read back from the running Live instance."
        ),
    }


def discover_live_capabilities() -> dict[str, Any]:
    """Return the read-only Live capability matrix exposed to AI clients."""
    return discover_capabilities(_session.inspector.health())


def inspect_live_state() -> dict[str, Any]:
    """Read Ableton Live state without changing anything.

    Returns the connection health report always, and a full state snapshot when
    the bridge is reachable.
    """
    health = _session.inspector.health()
    if not health.get("connected") or not health.get("protocol_supported", True):
        return {
            "schema_version": SCHEMA_VERSION,
            "health": health,
            "snapshot": None,
        }
    try:
        snapshot = _session.inspector.snapshot()
    except LiveTransportError as exc:
        return {
            "schema_version": SCHEMA_VERSION,
            "health": health,
            "snapshot": None,
            "error_code": exc.code,
            "error": exc.message,
        }
    except (LiveVersionUnsupportedError, LiveContractError) as exc:
        return {
            "schema_version": SCHEMA_VERSION,
            "health": health,
            "snapshot": None,
            "error_code": "unsupported",
            "error": str(exc),
        }
    return {
        "schema_version": SCHEMA_VERSION,
        "health": health,
        "snapshot": snapshot.to_dict(),
    }


def create_live_mutation_plan(
    project_plan: dict[str, Any],
    live_state: dict[str, Any] | None = None,
    clip_bars: int = 4,
) -> dict[str, Any]:
    """Plan Session View patterns for a ProjectPlan without changing Live.

    Pass ``live_state`` to plan against a snapshot you already hold; omit it to
    read the running Set. The result is inert: it applies nothing.
    """
    snapshot, failure = _resolve_snapshot(live_state)
    if snapshot is None:
        return failure or {}
    try:
        plan = _session.planner.create_session_plan(
            project_plan, snapshot, clip_bars=clip_bars
        )
    except (LiveContractError, ValueError, TypeError) as exc:
        return _plan_error(str(exc))
    return plan.to_dict()


def request_live_execution(
    project_plan: dict[str, Any],
    live_state: dict[str, Any] | None = None,
    clip_bars: int = 4,
) -> dict[str, Any]:
    """Create one approval-gated Live request without executing anything.

    On success an approval token is written to an owner-only file and its
    location is returned. The token itself is never included in this result: a
    person must open that file and pass the token to ``execute_live_request``.
    """
    planned = create_live_mutation_plan(project_plan, live_state, clip_bars)
    if planned.get("status") in {STATUS_BLOCKED, STATUS_UNAVAILABLE}:
        return {
            "schema_version": SCHEMA_VERSION,
            "status": planned.get("status"),
            "approval_required": False,
            "plan": planned,
            "error": planned.get("error", ""),
        }
    try:
        plan = LiveMutationPlan.from_dict(planned)
        token = _session.gate.approve(plan)
    except (LiveContractError, ApprovalError) as exc:
        return {
            "schema_version": SCHEMA_VERSION,
            "status": STATUS_BLOCKED,
            "approval_required": False,
            "plan": planned,
            "error": str(exc),
        }
    approval_path = _write_approval_token(plan, token)
    return {
        "schema_version": SCHEMA_VERSION,
        "status": plan.status,
        "approval_required": True,
        "plan": plan.to_dict(),
        "operation_count": len(plan.operations),
        "destructive_operation_count": plan.destructive_operation_count,
        "warnings": list(plan.warnings),
        "approval_token_path": str(approval_path),
        "approval_instructions": (
            "A human must open approval_token_path, read the token, and call "
            "execute_live_request with approved=true and that token. KIHACHI "
            "will not change the Set without it."
        ),
    }


def execute_live_request(
    request: dict[str, Any],
    approved: bool = False,
    approval_token: str = "",
) -> dict[str, Any]:
    """Attempt one approved Live request and return a truthful receipt.

    Accepts either the wrapper returned by ``request_live_execution`` or a bare
    mutation plan. Reports ``verified`` only when every operation was applied
    and read back correctly.
    """
    payload = request.get("plan") if isinstance(request.get("plan"), dict) else request
    try:
        plan = LiveMutationPlan.from_dict(payload)
    except LiveContractError as exc:
        return _receipt_error(str(exc))
    receipt = _session.executor.execute(
        plan, approved=approved, approval_token=approval_token
    )
    if receipt.status != "approval_required":
        _clear_approval_token(plan)
    return receipt.to_dict()


def verify_live_execution(
    request: dict[str, Any], receipt: dict[str, Any]
) -> dict[str, Any]:
    """Re-read Live and confirm a receipt's claims still hold.

    Live updates some properties asynchronously, so this second, independent
    readback is what turns a fresh receipt into a durable one. It applies no
    operations.
    """
    payload = request.get("plan") if isinstance(request.get("plan"), dict) else request
    try:
        plan = LiveMutationPlan.from_dict(payload)
        parsed = LiveExecutionReceipt.from_dict(receipt)
    except LiveContractError as exc:
        return _receipt_error(str(exc))
    return _session.executor.verify(plan, parsed).to_dict()


def expand_session_to_arrangement(
    project_plan: dict[str, Any],
    session_receipt: dict[str, Any],
    live_state: dict[str, Any] | None = None,
    clip_bars: int = 4,
) -> dict[str, Any]:
    """Plan Arrangement View placement for a verified Session View layout.

    Refuses unless ``session_receipt`` is ``verified`` and every target time
    range is empty on its track.
    """
    snapshot, failure = _resolve_snapshot(live_state)
    if snapshot is None:
        return failure or {}
    try:
        parsed_receipt = LiveExecutionReceipt.from_dict(session_receipt)
        plan = _session.expander.create_arrangement_plan(
            project_plan, snapshot, parsed_receipt, clip_bars=clip_bars
        )
    except (LiveContractError, ValueError, TypeError) as exc:
        return _plan_error(str(exc))
    return plan.to_dict()


def _resolve_snapshot(
    live_state: dict[str, Any] | None,
) -> tuple[LiveStateSnapshot | None, dict[str, Any] | None]:
    """Return a snapshot from the caller or from Live, plus a failure payload."""
    if live_state is not None:
        try:
            return LiveStateSnapshot.from_dict(live_state), None
        except LiveContractError as exc:
            return None, _plan_error(str(exc))
    try:
        return _session.inspector.snapshot(), None
    except LiveTransportError as exc:
        return None, {
            "schema_version": SCHEMA_VERSION,
            "status": STATUS_UNAVAILABLE,
            "operations": [],
            "conflicts": [],
            "warnings": [],
            "error_code": exc.code,
            "error": exc.message,
        }
    except (LiveVersionUnsupportedError, LiveContractError) as exc:
        return None, _plan_error(str(exc))


def _plan_error(message: str) -> dict[str, Any]:
    """Return a blocked plan-shaped payload describing why planning failed."""
    return {
        "schema_version": SCHEMA_VERSION,
        "status": STATUS_BLOCKED,
        "operations": [],
        "conflicts": [],
        "warnings": [],
        "error": message,
    }


def _receipt_error(message: str) -> dict[str, Any]:
    """Return a blocked receipt-shaped payload describing why execution failed."""
    return {
        "schema_version": SCHEMA_VERSION,
        "request_id": "",
        "status": STATUS_BLOCKED,
        "attempted_operations": [],
        "completed_operations": [],
        "error": message,
    }


def _approval_token_path() -> Path:
    """Return the owner-only file a human reads the approval token from."""
    override = os.getenv("KIHACHI_LIVE_APPROVAL_TOKEN_PATH")
    if override:
        return Path(override).expanduser()
    return Path(str(bridge_state_dir())).expanduser() / APPROVAL_FILENAME


def _write_approval_token(plan: LiveMutationPlan, token: str) -> Path:
    """Write the approval token where only the current user can read it."""
    path = _approval_token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(
            {
                "request_id": plan.request_id,
                "plan_hash": plan.plan_hash,
                "expires_at": plan.expires_at,
                "operation_count": len(plan.operations),
                "destructive_operation_count": plan.destructive_operation_count,
                "approval_token": token,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    os.chmod(temporary, 0o600)
    temporary.replace(path)
    _logger.warning(
        "live.approval.pending",
        extra={
            "request_id": plan.request_id,
            "operation_count": len(plan.operations),
            "approval_token_path": str(path),
        },
    )
    return path


def _clear_approval_token(plan: LiveMutationPlan) -> None:
    """Remove the pending approval file once the plan has been attempted."""
    path = _approval_token_path()
    if not path.exists():
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if isinstance(data, dict) and data.get("request_id") == plan.request_id:
        path.unlink(missing_ok=True)
