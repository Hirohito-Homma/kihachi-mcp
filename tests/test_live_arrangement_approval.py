"""Approval registration for plans built by another tool.

``expand_session_to_arrangement`` returns an approval_required plan,
but ``request_live_execution`` originally only planned Session layouts
from a ProjectPlan, so no tool could register approval for an
Arrangement plan's hash and executing one was impossible. These tests
cover the plan form of the request tool end to end.
"""

import json

import pytest
from live_fixtures import empty_live_set, project_plan

from kihachi_mcp.services.live_transport_fake import FakeLiveTransport
from kihachi_mcp.tools import live as live_tools
from kihachi_mcp.tools.live import (
    configure_live_transport,
    execute_live_request,
    expand_session_to_arrangement,
    request_live_execution,
)


@pytest.fixture
def live(tmp_path, monkeypatch):
    """Wire the tools to a simulated Set with isolated on-disk state."""
    monkeypatch.setenv("KIHACHI_LIVE_STATE_DIR", str(tmp_path))
    monkeypatch.setenv(
        "KIHACHI_LIVE_APPROVAL_TOKEN_PATH", str(tmp_path / "pending-approval.json")
    )
    transport = FakeLiveTransport(empty_live_set(tempo=120.0))
    monkeypatch.setattr(live_tools, "_session", live_tools._LiveSession())
    configure_live_transport(transport)
    yield transport
    configure_live_transport(None)


def _read_token(tmp_path) -> str:
    payload = json.loads(
        (tmp_path / "pending-approval.json").read_text(encoding="utf-8")
    )
    return payload["approval_token"]


def _verified_session_receipt(tmp_path):
    source = project_plan().to_dict(include_arrangement=True)
    request = request_live_execution(source)
    receipt = execute_live_request(request, True, _read_token(tmp_path))
    assert receipt["status"] == "verified"
    return source, receipt


def test_arrangement_plan_can_be_requested_approved_and_executed(
    live, tmp_path
) -> None:
    source, session_receipt = _verified_session_receipt(tmp_path)
    arrangement_plan = expand_session_to_arrangement(source, session_receipt)
    assert arrangement_plan["status"] == "approval_required"

    arrangement_request = request_live_execution(plan=arrangement_plan)

    assert arrangement_request["status"] == "approval_required"
    assert arrangement_request["approval_required"] is True
    receipt = execute_live_request(
        arrangement_request, True, _read_token(tmp_path)
    )
    assert receipt["status"] == "verified"
    assert live.live_set.arrangement_clips

    # Deliberately not asserted here: verify_live_execution on an
    # Arrangement receipt. The generic re-verifier has no arrangement
    # branch, so place_arrangement_clip operations fall into its
    # track-name check and false-fail. That is a separate follow-up;
    # the execution receipt above is verified by per-operation readback.


def test_request_live_execution_requires_exactly_one_input(live) -> None:
    neither = request_live_execution()
    both = request_live_execution(
        project_plan().to_dict(include_arrangement=True), plan={"schema_version": 1}
    )

    assert neither["status"] == "blocked"
    assert neither["approval_required"] is False
    assert both["status"] == "blocked"
    assert both["approval_required"] is False


def test_request_live_execution_refuses_a_plan_with_conflicts(
    live, tmp_path
) -> None:
    source, session_receipt = _verified_session_receipt(tmp_path)
    refused_plan = expand_session_to_arrangement(
        source, dict(session_receipt, status="partially_applied")
    )
    assert refused_plan["status"] == "blocked"

    result = request_live_execution(plan=refused_plan)

    assert result["status"] == "blocked"
    assert result["approval_required"] is False
