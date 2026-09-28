"""MCP tools over the same services the Studio and the `kihachi` CLI use.

Candidates are shared through the same folder on disk, so a song created from
Cursor appears in the Studio's project list. Live writes go through the
running Studio and still require the human approval recorded there.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from kihachi_mcp.services.diagnostics import DiagnosticsService
from kihachi_mcp.services.live_paths import candidate_store_dir
from kihachi_mcp.services.studio_client import SEND_TIMEOUT_SECONDS, studio_post
from kihachi_mcp.services.studio_runtime import StudioRuntime

_runtime: StudioRuntime | None = None


def _studio() -> StudioRuntime:
    global _runtime
    if _runtime is None:
        _runtime = StudioRuntime(candidate_dir=Path(candidate_store_dir()))
    return _runtime


def create_song(prompt: str, seed: int | None = None, use_ai: bool = True) -> dict[str, Any]:
    """Create a SongSpec, arrangement and MIDI candidate from a natural-language brief."""
    runtime = _studio()
    if not use_ai:
        runtime.update_settings({"ai_provider": "deterministic"}, persist=False)
    try:
        result = runtime.generate(prompt, seed=seed)
    finally:
        if not use_ai:
            runtime.update_settings({"ai_provider": "ollama"}, persist=False)
    if not result.get("ok"):
        return result
    return runtime.project(result["candidate"]["candidate_id"])


def list_projects() -> dict[str, Any]:
    """List saved song candidates, newest first."""
    return {"ok": True, "projects": _studio().list_projects()}


def get_project(candidate_id: str) -> dict[str, Any]:
    """Return SongSpec, arrangement, tracks, review and status for one candidate."""
    return _studio().project(candidate_id)


def review_song(candidate_id: str) -> dict[str, Any]:
    """Review the notes of one candidate; issues name bars and a recommendation."""
    return _studio().review(candidate_id)


def revise_song(
    candidate_id: str,
    issue_ids: list[str] | None = None,
    scopes: list[str] | None = None,
    start_bar: int | None = None,
    end_bar: int | None = None,
    accept: bool = False,
) -> dict[str, Any]:
    """Locally revise only the chosen issues or scopes. Adopt only when the human said so."""
    runtime = _studio()
    bars = (start_bar, end_bar) if start_bar and end_bar else None
    proposal = runtime.propose_revision(candidate_id, issue_ids=issue_ids, scopes=scopes, bars=bars)
    if not proposal.get("ok") or not accept:
        return proposal
    decided = runtime.decide_revision(proposal["revision"]["revision_id"], accept=True)
    return {**proposal, "decision": decided}


def approve_song(candidate_id: str) -> dict[str, Any]:
    """Record the human's approval of a candidate. Call only when the user approved it."""
    return _studio().approve(candidate_id)


def dry_run_ableton_plan(candidate_id: str, change_tempo: bool = False) -> dict[str, Any]:
    """Show what Send to Ableton would do. Uses the Studio's Live connection; changes nothing."""
    result = studio_post(
        "/api/ableton/dry-run", {"candidate_id": candidate_id, "change_tempo": change_tempo}
    )
    if result.get("studio_running") is False:
        return {**_studio().ableton_plan(candidate_id), "live_checked": False, "note": result["error"]}
    return result


def execute_ableton_plan(
    candidate_id: str, confirmed: bool = False, change_tempo: bool = False
) -> dict[str, Any]:
    """Send an approved candidate to Live once, then read Live back. Needs confirmed=True."""
    if not confirmed:
        return {"ok": False, "error": "ユーザーの確認が必要です。dry_run_ableton_plan を見せてから confirmed=True で呼んでください"}
    return studio_post(
        "/api/ableton/send",
        {"candidate_id": candidate_id, "confirmed": True, "change_tempo": change_tempo},
        timeout=SEND_TIMEOUT_SECONDS,
    )


def verify_ableton_project(candidate_id: str) -> dict[str, Any]:
    """Read tempo, tracks, clips, notes and arrangement back from Live and compare."""
    return studio_post("/api/ableton/verify", {"candidate_id": candidate_id})


def ollama_status() -> dict[str, Any]:
    """Local Ollama health, installed models and the selected model. Never downloads."""
    return _studio().ollama_status()


def doctor() -> dict[str, Any]:
    """The same diagnostics as `kihachi doctor` and the Studio's diagnostics panel."""
    return DiagnosticsService().run()
