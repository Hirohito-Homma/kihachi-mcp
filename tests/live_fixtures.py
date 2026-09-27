"""Shared builders for the Ableton Live automation tests.

Every test here runs against :class:`FakeLiveTransport`. None of them start
Ableton Live, open a socket, or read the developer's real Live Sets.
"""

from datetime import UTC, datetime
from pathlib import Path

from kihachi_mcp.models import Arrangement, ProjectPlan, TrackSpec
from kihachi_mcp.models.live_state import LiveStateSnapshot
from kihachi_mcp.services.live_approval_gate import ApprovalGate
from kihachi_mcp.services.live_execution_service import LiveExecutionService
from kihachi_mcp.services.live_mutation_planner import LiveMutationPlanner
from kihachi_mcp.services.live_state_inspector import LiveStateInspector
from kihachi_mcp.services.live_transport_fake import FakeLiveSet, FakeLiveTransport

FIXED_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def fixed_clock():
    """Return a clock that never advances, so expiry is explicit in tests."""
    return lambda: FIXED_NOW


def project_plan(
    tempo: int = 110,
    key: str = "D#m",
    tracks: list[TrackSpec] | None = None,
    sections: list[Arrangement] | None = None,
) -> ProjectPlan:
    """Return a small, deterministic ProjectPlan."""
    return ProjectPlan(
        project_name="Untitled",
        genre="dub techno",
        tempo=tempo,
        key=key,
        length_minutes=2,
        bars=64,
        tracks=tracks
        if tracks is not None
        else [
            TrackSpec(name="Kick", type="MIDI", color="Red"),
            TrackSpec(name="Bass", type="MIDI", color="Blue"),
        ],
        arrangement=sections
        if sections is not None
        else [
            Arrangement(name="Intro", start_bar=1, length_bars=16),
            Arrangement(name="Drop", start_bar=17, length_bars=32),
        ],
    )


def empty_live_set(**overrides) -> FakeLiveSet:
    """Return a simulated Live Set with no tracks, scenes, or clips."""
    return FakeLiveSet(**overrides)


def snapshot_of(transport: FakeLiveTransport) -> LiveStateSnapshot:
    """Return a snapshot of the simulated Set without going through a socket."""
    return LiveStateSnapshot.from_dict(transport.snapshot_payload())


def planner() -> LiveMutationPlanner:
    """Return a planner with a deterministic request id and clock."""
    counter = {"value": 0}

    def next_request_id() -> str:
        counter["value"] += 1
        return f"req-{counter['value']:04d}"

    return LiveMutationPlanner(
        request_id_factory=next_request_id, clock=fixed_clock()
    )


def gate(tmp_path: Path | None = None) -> ApprovalGate:
    """Return an approval gate isolated from the developer's real state."""
    storage = str(tmp_path / "approvals.json") if tmp_path is not None else None
    return ApprovalGate(storage_path=storage, clock=fixed_clock())


def executor(
    transport: FakeLiveTransport, approval_gate: ApprovalGate
) -> LiveExecutionService:
    """Return an execution service wired to a simulated Live Set."""
    return LiveExecutionService(
        transport=transport,
        inspector=LiveStateInspector(transport),
        approval_gate=approval_gate,
    )


def approved_run(
    transport: FakeLiveTransport,
    plan_source: ProjectPlan | None = None,
    approval_gate: ApprovalGate | None = None,
):
    """Plan, approve, and execute one Session plan against a simulated Set.

    Returns the plan, the receipt, and the gate so a test can assert on any
    stage of the pipeline.
    """
    source = plan_source if plan_source is not None else project_plan()
    current_gate = approval_gate if approval_gate is not None else gate()
    plan = planner().create_session_plan(source, snapshot_of(transport))
    token = current_gate.approve(plan)
    receipt = executor(transport, current_gate).execute(
        plan, approved=True, approval_token=token
    )
    return plan, receipt, current_gate, token
