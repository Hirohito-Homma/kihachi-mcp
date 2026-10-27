"""Action plans. Phase 1 plans are never executable."""

from __future__ import annotations

from typing import Any

_RISKS = frozenset({"low", "medium", "high"})


def build_plan(intent: dict[str, Any]) -> dict[str, Any]:
    """Describe reads and proposed writes. This function does not perform them."""
    if not intent.get("recognized"):
        plan = {
            "goal": "unknown",
            "reads": [],
            "proposed_writes": [],
            "verification": [],
            "requires_human_approval": False,
            "risk_level": "low",
            "executable": False,
        }
        validate_plan(plan)
        return plan
    writes: list[dict[str, Any]] = []
    if intent["write_requested"]:
        write_item: dict[str, Any] = {
            "action": intent["direction"],
            "target": intent["target"],
            "scope": intent["scope"],
            "range": intent["range"],
        }
        if isinstance(intent.get("clip_target"), dict):
            write_item["clip_target"] = dict(intent["clip_target"])
        writes.append(write_item)
    if intent["type"] == "inspect_project":
        goal = "report current project"
        reads = ["project", "live_health", "ollama"]
    elif intent["type"] == "musical_judgment":
        goal = "musical judgment"
        reads = ["project", "brain"]
    elif intent["type"] == "evaluate_midi_clip":
        goal = f"evaluate midi clip {intent['target']}"
        reads = ["get_midi_clip_notes"]
    elif intent["type"] == "modify_midi_clip":
        goal = f"preview {intent['direction']} {intent['target']}"
        reads = ["get_midi_clip_notes"]
    elif intent["type"] == "evaluate_arrangement":
        goal = f"evaluate {intent['target']}"
        reads = ["project", "arrangement", "live_health"]
    else:
        goal = f"{intent['direction']} {intent['target']}"
        reads = ["project", "arrangement"]
    risk = "low"
    if writes:
        risk = "high" if intent["direction"] == "delete" else "medium"
    plan = {
        "goal": goal,
        "reads": reads,
        "proposed_writes": writes,
        "verification": ["read_back_target_range"] if writes else ["report_observed_state"],
        "requires_human_approval": bool(writes),
        "risk_level": risk,
        "executable": False,
    }
    validate_plan(plan)
    return plan


def validate_plan(plan: dict[str, Any]) -> None:
    """A proposed write without approval, or an executable phase-1 plan, is invalid."""
    if plan["proposed_writes"] and not plan["requires_human_approval"]:
        raise ValueError("a write plan requires human approval")
    if plan["risk_level"] not in _RISKS:
        raise ValueError("risk_level must be low, medium, or high")
    if plan["executable"]:
        raise ValueError("phase 1 plans are not executable")
    if not isinstance(plan["reads"], list) or not isinstance(plan["proposed_writes"], list):
        raise TypeError("reads and proposed_writes must be lists")
