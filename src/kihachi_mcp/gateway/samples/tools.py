"""MCP-facing sample tools. Responses never include a filesystem path."""

from __future__ import annotations

from typing import Any

from kihachi_mcp.gateway.catalog import RISK_CONFIRM, RISK_SAFE
from kihachi_mcp.gateway.samples.index import (
    find_sample,
    index_library,
    library_status,
    remember_selection,
)
from kihachi_mcp.gateway.samples.load import (
    probe_sample_audio_clip,
    sample_loading_capabilities,
)
from kihachi_mcp.gateway.samples.meta import public_sample
from kihachi_mcp.gateway.samples.oneshot import place_audio_one_shot
from kihachi_mcp.gateway.samples.place import place_indexed_sample
from kihachi_mcp.gateway.samples.query import sample_plan, search_indexed
from kihachi_mcp.gateway.song_plan import build_song_plan

_SECRET_KEYS = frozenset({"path", "file_path", "filepath", "internal_path"})


def sample_library_status() -> dict[str, Any]:
    """Say whether a library is configured and how many samples are indexed."""
    return _public(library_status())


def index_sample_library() -> dict[str, Any]:
    """Rebuild the index from KIHACHI_SAMPLE_LIBRARY_PATHS. Audio files stay untouched."""
    return _public(index_library())


def search_samples(
    role: str | None = None,
    bpm: float | None = None,
    key: str | None = None,
    tags: list[str] | None = None,
    kind: str | None = None,
    limit: int = 10,
) -> dict[str, Any]:
    """Rank indexed samples. The result uses sample_id, not a file path."""
    rows = search_indexed(role=role, bpm=bpm, key=key, tags=tags, kind=kind, limit=limit)
    return {"ok": True, "count": len(rows), "samples": _public(rows)}


def get_sample(sample_id: str) -> dict[str, Any]:
    """Return one indexed sample without its path."""
    return get_sample_metadata(sample_id)


def get_sample_metadata(sample_id: str) -> dict[str, Any]:
    """Return one indexed sample without an absolute filesystem path."""
    record = find_sample(sample_id)
    if record is None:
        return {"ok": False, "error": "not_found", "sample_id": sample_id}
    return {"ok": True, "sample": public_sample(record)}


def find_samples_for_song(
    tempo: float = 110,
    key: str = "D# minor",
    bars: int = 32,
    style: str = "mutation-funk-dub-tech-house",
    title: str = "KIHACHI MANUS TEST 01",
    sample_selection: str = "assisted",
) -> dict[str, Any]:
    """Dry-run the sample half of the test song. This does not call Live."""
    try:
        plan = build_song_plan(tempo=tempo, key=key, bars=bars, style=style, title=title)
    except (TypeError, ValueError) as exc:
        return {"ok": False, "status": "rejected", "live_changed": False, "message": str(exc)}
    return {"ok": True, "live_changed": False, "sample_plan": _public(sample_plan(plan, sample_selection))}


def select_sample(sample_id: str, role: str, title: str = "KIHACHI MANUS TEST 01") -> dict[str, Any]:
    """Remember a human choice. Assisted mode places only remembered samples."""
    record = find_sample(sample_id)
    if record is None:
        return {"ok": False, "error": "not_found", "live_changed": False}
    remember_selection(title, role, sample_id)
    return {
        "ok": True,
        "live_changed": False,
        "title": title,
        "role": role,
        "sample_id": sample_id,
    }


def place_sample(
    sample_id: str,
    role: str,
    title: str = "KIHACHI MANUS TEST 01",
    confirm: bool = False,
) -> dict[str, Any]:
    """Import one sample. Unwarped audio stays in Session and is not forced onto Arrangement."""
    return _public(place_indexed_sample(sample_id, title=title, role=role, confirm=confirm))


def attach_sample_plan(view: dict[str, Any], plan: dict[str, Any], selection: str) -> dict[str, Any]:
    try:
        view["sample_plan"] = _public(sample_plan(plan, selection))
    except (OSError, TypeError, ValueError):
        view["sample_plan"] = {"ok": False, "error": "sample_plan_failed", "live_changed": False}
    return view


def place_audio_one_shot_sample(
    sample_id: str,
    target_track: str,
    beat: float,
    source_midi_track: str,
    source_note_index: int,
    source_pitch: int,
    source_velocity: int,
    confirm: bool = False,
    dry_run: bool = False,
    selection_mode: str = "technical_probe",
) -> dict[str, Any]:
    """Place one unwarped one-shot. The public argument is sample_id, not a path."""
    return _public(
        place_audio_one_shot(
            sample_id,
            target_track=target_track,
            beat=beat,
            source_midi_track=source_midi_track,
            source_note_index=source_note_index,
            source_pitch=source_pitch,
            source_velocity=source_velocity,
            confirm=confirm,
            dry_run=dry_run,
            selection_mode=selection_mode,
        )
    )


def probe_indexed_sample_audio(
    sample_id: str,
    confirm: bool = False,
    selection_mode: str = "technical_probe",
) -> dict[str, Any]:
    """Import one sample_id onto a new audio probe. Paths are not returned."""
    return probe_sample_audio_clip(sample_id, confirm=confirm, selection_mode=selection_mode)


def register_sample_tools(mcp: Any) -> None:
    read_only = {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": True}
    write = {
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    }
    for tool in (
        sample_library_status,
        search_samples,
        get_sample,
        get_sample_metadata,
        find_samples_for_song,
        sample_loading_capabilities,
    ):
        mcp.tool(tool, annotations=read_only, meta={"kihachiRisk": RISK_SAFE})
    for tool in (
        index_sample_library,
        select_sample,
        place_sample,
        probe_indexed_sample_audio,
        place_audio_one_shot_sample,
    ):
        mcp.tool(tool, annotations=write, meta={"kihachiRisk": RISK_CONFIRM})


def _public(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _public(item) for key, item in value.items() if key not in _SECRET_KEYS}
    if isinstance(value, list):
        return [_public(item) for item in value]
    return value
