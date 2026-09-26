"""MCP tools that translate a ProjectPlan into Ableton-shaped plans.

These three tools never contact Ableton Live. Everything that can change a Set
lives in :mod:`kihachi_mcp.tools.live` behind the approval gate.
"""

from typing import Any

from kihachi_mcp.services import AbletonService

_ableton = AbletonService()


def create_ableton_plan(project_plan: dict[str, Any]) -> dict[str, Any]:
    """Translate a ProjectPlan JSON value into an Ableton plan."""
    return _ableton.create_plan(project_plan).to_dict()


def create_midi_plan(project_plan: dict[str, Any]) -> dict[str, Any]:
    """Create a deterministic MIDI clip plan without contacting Ableton Live."""
    return _ableton.create_midi_plan(project_plan).to_dict()


def prepare_ableton_handoff(project_plan: dict[str, Any]) -> dict[str, Any]:
    """Validate an Ableton plan statically before any Live mutation is planned."""
    return _ableton.prepare_handoff(project_plan).to_dict()
