"""Assemble project facts. Missing values stay UNKNOWN. Nothing is written."""

from __future__ import annotations

from typing import Any

UNKNOWN = "UNKNOWN"


def build_context(
    *,
    project: dict[str, Any] | None,
    live: dict[str, Any] | None,
    ollama: dict[str, Any] | None,
    project_error: str | None = None,
) -> dict[str, Any]:
    """Copy facts that were actually supplied. Absent facts are UNKNOWN."""
    song = (project or {}).get("songspec") if isinstance(project, dict) else None
    arrangement = (project or {}).get("arrangement") if isinstance(project, dict) else None
    sections = arrangement.get("sections") if isinstance(arrangement, dict) else None
    tracks = project.get("tracks") if isinstance(project, dict) else None
    status = project.get("status") if isinstance(project, dict) else None
    return {
        "project_present": isinstance(project, dict),
        "project_error": project_error if not isinstance(project, dict) else None,
        "candidate_id": _pick(project, "candidate_id"),
        "tempo": _pick(song, "tempo"),
        "key": _pick(song, "key"),
        "genre": _pick(song, "genre"),
        "bars": _pick(song, "bars"),
        "sections": sections if isinstance(sections, list) else UNKNOWN,
        "tracks": tracks if isinstance(tracks, list) else UNKNOWN,
        "approved": status.get("approved") if isinstance(status, dict) and "approved" in status else UNKNOWN,
        "applied": status.get("applied") if isinstance(status, dict) and "applied" in status else UNKNOWN,
        "verification": status.get("verification") if isinstance(status, dict) else UNKNOWN,
        "live_connected": live.get("connected") if isinstance(live, dict) and "connected" in live else UNKNOWN,
        "live_state": _pick(live, "state"),
        "ollama_state": _pick(ollama, "state"),
        "musical_quality_claimed": False,
    }


def context_from_runtime(runtime: Any) -> dict[str, Any]:
    """Read the open Studio candidate and connection health. Live is not changed."""
    try:
        selected = runtime.selected_id()
        project = runtime.project(selected) if selected else {"ok": False, "error": "候補がありません"}
        health = runtime.health()
    except Exception:  # noqa: BLE001 - a failed read is UNKNOWN, not a crash
        return {"ok": False, "error": "context_unavailable"}
    loaded = project if isinstance(project, dict) and project.get("ok") else None
    return {
        "ok": True,
        "project": loaded,
        "project_error": None if loaded else str((project or {}).get("error") or "候補がありません"),
        "live": health.get("live") if isinstance(health, dict) else None,
        "ollama": health.get("ollama") if isinstance(health, dict) else None,
    }


def _pick(data: dict[str, Any] | None, key: str) -> Any:
    if not isinstance(data, dict) or key not in data or data[key] in (None, ""):
        return UNKNOWN
    return data[key]
