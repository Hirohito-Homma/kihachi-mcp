from typing import Any

from kihachi_mcp.services import AbletonExecutionAdapter, AbletonService

_ableton = AbletonService()
_execution = AbletonExecutionAdapter()


def create_ableton_plan(project_plan: dict[str, Any]) -> dict[str, Any]:
    """Translate a ProjectPlan JSON value into an Ableton plan."""
    return _ableton.create_plan(project_plan).to_dict()


def create_midi_plan(project_plan: dict[str, Any]) -> dict[str, Any]:
    """Create a deterministic MIDI clip plan without contacting Ableton Live."""
    return _ableton.create_midi_plan(project_plan).to_dict()


def prepare_ableton_handoff(project_plan: dict[str, Any]) -> dict[str, Any]:
    """Validate an Ableton plan before a future Live handoff."""
    return _ableton.prepare_handoff(project_plan).to_dict()


def request_live_execution(project_plan: dict[str, Any]) -> dict[str, Any]:
    """Prepare one approval-gated Live mutation request without executing it."""
    return _ableton.request_live_execution(project_plan).to_dict()


def execute_live_request(
    request: dict[str, Any], approved: bool = False
) -> dict[str, Any]:
    """Attempt an approved Live request through the configured adapter boundary."""
    return _execution.execute(request, approved=approved).to_dict()
