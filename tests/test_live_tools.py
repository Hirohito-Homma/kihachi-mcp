"""End-to-end checks through the MCP tool surface, with a fake transport."""

import json

import pytest
from live_fixtures import empty_live_set, project_plan

from kihachi_mcp.services.live_transport_fake import FakeLiveTransport
from kihachi_mcp.tools import live as live_tools
from kihachi_mcp.tools.live import (
    configure_live_transport,
    create_live_mutation_plan,
    execute_live_request,
    expand_session_to_arrangement,
    inspect_live_state,
    request_live_execution,
    verify_live_execution,
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


def test_inspect_live_state_is_read_only(live) -> None:
    result = inspect_live_state()

    assert result["health"]["connected"] is True
    assert result["snapshot"]["tempo"] == 120.0
    assert result["snapshot"]["tracks"] == []
    assert live.live_set.tracks == []


def test_create_live_mutation_plan_changes_nothing(live) -> None:
    plan = create_live_mutation_plan(project_plan().to_dict(include_arrangement=True))

    assert plan["status"] == "approval_required"
    assert plan["operations"]
    assert live.applied_operation_ids == []
    assert live.live_set.tracks == []


def test_a_plan_can_be_built_against_a_supplied_snapshot_without_live(live) -> None:
    snapshot = inspect_live_state()["snapshot"]
    configure_live_transport(None)

    plan = create_live_mutation_plan(
        project_plan().to_dict(include_arrangement=True), live_state=snapshot
    )

    assert plan["status"] == "approval_required"


def test_request_live_execution_withholds_the_token_from_its_output(
    live, tmp_path
) -> None:
    result = request_live_execution(
        project_plan().to_dict(include_arrangement=True)
    )

    token = _read_token(tmp_path)
    assert result["approval_required"] is True
    assert result["status"] == "approval_required"
    assert token not in json.dumps(result)
    assert result["approval_token_path"].endswith("pending-approval.json")
    assert "A human must open" in result["approval_instructions"]
    assert live.applied_operation_ids == []


def test_the_pending_approval_file_is_owner_only(live, tmp_path) -> None:
    request_live_execution(project_plan().to_dict(include_arrangement=True))

    path = tmp_path / "pending-approval.json"

    assert oct(path.stat().st_mode)[-3:] == "600"


def test_full_approved_round_trip_reaches_verified(live, tmp_path) -> None:
    source = project_plan().to_dict(include_arrangement=True)
    request = request_live_execution(source)
    token = _read_token(tmp_path)

    receipt = execute_live_request(request, approved=True, approval_token=token)
    confirmed = verify_live_execution(request, receipt)

    assert receipt["status"] == "verified"
    assert receipt["mismatches"] == []
    assert confirmed["status"] == "verified"
    assert len(live.live_set.session_clips) == 4


def test_execute_without_approval_is_refused(live, tmp_path) -> None:
    request = request_live_execution(
        project_plan().to_dict(include_arrangement=True)
    )

    receipt = execute_live_request(request, approved=False)

    assert receipt["status"] == "approval_required"
    assert live.applied_operation_ids == []


def test_execute_twice_is_refused(live, tmp_path) -> None:
    request = request_live_execution(
        project_plan().to_dict(include_arrangement=True)
    )
    token = _read_token(tmp_path)
    first = execute_live_request(request, approved=True, approval_token=token)
    applied = list(live.applied_operation_ids)

    second = execute_live_request(request, approved=True, approval_token=token)

    assert first["status"] == "verified"
    assert second["status"] == "blocked"
    assert live.applied_operation_ids == applied


def test_arrangement_expansion_requires_a_verified_session(live, tmp_path) -> None:
    source = project_plan().to_dict(include_arrangement=True)
    request = request_live_execution(source)
    token = _read_token(tmp_path)
    receipt = execute_live_request(request, approved=True, approval_token=token)

    refused = expand_session_to_arrangement(
        source, dict(receipt, status="partially_applied")
    )
    allowed = expand_session_to_arrangement(source, receipt)

    assert refused["status"] == "blocked"
    assert refused["conflicts"][0]["kind"] == "session_not_verified"
    assert allowed["status"] == "approval_required"


def test_arrangement_plan_can_be_approved_and_verified(live, tmp_path) -> None:
    source = project_plan().to_dict(include_arrangement=True)
    session_request = request_live_execution(source)
    session_receipt = execute_live_request(
        session_request, approved=True, approval_token=_read_token(tmp_path)
    )
    arrangement_plan = expand_session_to_arrangement(source, session_receipt)
    arrangement_request = {"plan": arrangement_plan}

    unapproved = execute_live_request(arrangement_request, approved=False)

    assert unapproved["status"] == "approval_required"
    assert live.live_set.arrangement_clips == []


def test_a_schema_mismatch_is_refused_at_the_tool_boundary(live) -> None:
    result = create_live_mutation_plan(
        project_plan().to_dict(include_arrangement=True),
        live_state={"schema_version": 99},
    )

    assert result["status"] == "blocked"
    assert "schema_version" in result["error"]


def test_execute_refuses_a_payload_that_is_not_a_plan(live) -> None:
    result = execute_live_request({"schema_version": 42})

    assert result["status"] == "blocked"
    assert "schema_version" in result["error"]


def test_tools_report_unavailable_when_no_transport_is_configured(live) -> None:
    configure_live_transport(None)

    inspected = inspect_live_state()
    planned = create_live_mutation_plan(
        project_plan().to_dict(include_arrangement=True)
    )

    assert inspected["health"]["connected"] is False
    assert inspected["snapshot"] is None
    assert planned["status"] == "unavailable"
