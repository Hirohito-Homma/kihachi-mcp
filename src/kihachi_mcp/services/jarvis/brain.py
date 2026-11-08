"""Musical judgment stays with Brain. Live is not called."""

from __future__ import annotations

from typing import Any


def songspec_for_brain(songspec: dict[str, Any], arrangement: dict[str, Any] | None = None) -> dict[str, Any]:
    """Fill the fields Brain expects from the SongSpec Studio actually stores."""
    data = dict(songspec)
    if not data.get("length_minutes") and data.get("duration_minutes"):
        data["length_minutes"] = data["duration_minutes"]
    if not data.get("tracks"):
        names: list[str] = []
        sections = arrangement.get("sections") if isinstance(arrangement, dict) else None
        for section in sections or []:
            if not isinstance(section, dict):
                continue
            for name in section.get("active_tracks") or []:
                text = str(name)
                if text and text not in names:
                    names.append(text)
        if names:
            data["tracks"] = names
    return data


def review_songspec_for_jarvis(songspec: dict[str, Any]) -> dict[str, Any]:
    """Pass one SongSpec to Brain.review_song and return a non-writing summary."""
    from kihachi_mcp.api.brain import Brain
    from kihachi_mcp.models.songspec import SongSpec
    from kihachi_mcp.services.review_service import ReviewService

    reviewed = Brain().review_song(songspec).to_dict()
    spec = songspec if isinstance(songspec, SongSpec) else SongSpec.from_dict(songspec)
    reviewer = ReviewService()
    return {
        "ok": True,
        "approved": bool(reviewed.get("approved")),
        "score": reviewed.get("score"),
        "comments": list(reviewed.get("comments") or []),
        "suggestions": reviewer.suggestions(spec),
        "warnings": reviewer.warnings(spec),
        "live_changed": False,
        "musical_quality_claimed": False,
    }


def plan_from_brain(plan: dict[str, Any], review: dict[str, Any]) -> dict[str, Any]:
    """Turn Brain's notes into a Jarvis plan. The plan does not write Live."""
    from kihachi_mcp.services.jarvis.plan import validate_plan

    comments = [str(item) for item in review.get("comments") or []]
    suggestions = [str(item) for item in review.get("suggestions") or []]
    proposal = comments + [item for item in suggestions if item not in comments]
    updated = {
        **plan,
        "goal": "musical judgment",
        "reads": ["project", "brain"],
        "proposed_writes": [],
        "verification": ["report_observed_state"],
        "requires_human_approval": False,
        "risk_level": "low",
        "executable": False,
        "brain_review": review,
        "proposal": proposal,
    }
    validate_plan(updated)
    return updated
