"""MW10 gateway: POST /api/automation-log (FastAPI wiring).

This is the server half of the automation-log feature. It is NOT a
running service yet: the com.kihachi.mw10gateway daemon is target spec
(v2.0), so this app is the ready-to-run wiring for when the daemon is
stood up, kept next to its tested core (automation_log.py).

Run:
    OBSIDIAN_VAULT_PATH=/path/to/vault uvicorn app:app --host 127.0.0.1 --port 8000

Notes:
- Binds conceptually to loopback only (see the run line). If
  MW10_GATEWAY_TOKEN is set, requests must carry it as the X-Token
  header -- the same shared-token idea the Ableton bridge uses.
- The read-modify-write on the note is serialized with a lock; two
  simultaneous posts cannot interleave and lose a row.
"""

from __future__ import annotations

import json
import os
import threading
from collections.abc import AsyncGenerator
from pathlib import Path

from automation_log import (
    AutomationStats,
    MidiStats,
    update_automation_log,
    update_midi_log,
)
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import StreamingResponse
from music_pipeline import composer_node, mixer_node, supervisor_node
from pydantic import BaseModel, Field, model_validator

app = FastAPI(title="MW10 Gateway")

_lock = threading.Lock()


def _check_token(x_token: str | None) -> None:
    expected_token = os.environ.get("MW10_GATEWAY_TOKEN")
    if expected_token and x_token != expected_token:
        raise HTTPException(status_code=401, detail="invalid or missing X-Token")


def _vault_dir() -> Path | None:
    vault_path = os.environ.get("OBSIDIAN_VAULT_PATH")
    return Path(vault_path) if vault_path else None


class AutomationLogPayload(BaseModel):
    session_id: str
    track_name: str
    parameter_name: str
    total_events: int = Field(ge=0)
    duration_beats: float = Field(ge=0)
    min_value: float
    max_value: float

    @model_validator(mode="after")
    def _min_not_above_max(self) -> AutomationLogPayload:
        if self.min_value > self.max_value:
            raise ValueError("min_value must be <= max_value")
        return self


@app.post("/api/automation-log")
async def update_automation_log_endpoint(
    payload: AutomationLogPayload,
    x_token: str | None = Header(default=None),
) -> dict:
    _check_token(x_token)
    vault_dir = _vault_dir()
    if vault_dir is None:
        return {
            "success": False,
            "error": "OBSIDIAN_VAULT_PATH is not configured on the gateway.",
        }

    stats = AutomationStats(
        session_id=payload.session_id,
        track_name=payload.track_name,
        parameter_name=payload.parameter_name,
        total_events=payload.total_events,
        duration_beats=payload.duration_beats,
        min_value=payload.min_value,
        max_value=payload.max_value,
    )
    print(f"\n[FastAPI] Received APC40 live automation stats for session: {payload.session_id}")
    with _lock:
        result = update_automation_log(vault_dir, stats)
    if result["success"]:
        print(f"[Obsidian Sync] {result['message']}")
    else:
        print(f"[FastAPI Error] {result['error']}")
    return result


class MidiLogPayload(BaseModel):
    session_id: str
    track_name: str
    total_notes: int = Field(ge=0)
    avg_velocity: float
    max_velocity: int = Field(ge=0, le=127)
    min_velocity: int = Field(ge=0, le=127)
    # Pitch histogram, note name -> hits (e.g. {"C3": 12, "G#3": 5}).
    # Optional: older clients that send no distribution still file their
    # base stats, and the note's existing distribution is left as it is.
    note_distribution: dict[str, int] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _velocities_consistent(self) -> MidiLogPayload:
        if self.min_velocity > self.max_velocity:
            raise ValueError("min_velocity must be <= max_velocity")
        if not self.min_velocity <= self.avg_velocity <= self.max_velocity:
            raise ValueError("avg_velocity must lie between min_velocity and max_velocity")
        for name, count in self.note_distribution.items():
            if not name or count < 1:
                raise ValueError("note_distribution must map note names to positive counts")
        return self


@app.post("/api/midi-log")
async def update_midi_log_endpoint(
    payload: MidiLogPayload,
    x_token: str | None = Header(default=None),
) -> dict:
    _check_token(x_token)
    vault_dir = _vault_dir()
    if vault_dir is None:
        return {
            "success": False,
            "error": "OBSIDIAN_VAULT_PATH is not configured on the gateway.",
        }

    stats = MidiStats(
        session_id=payload.session_id,
        track_name=payload.track_name,
        total_notes=payload.total_notes,
        avg_velocity=payload.avg_velocity,
        max_velocity=payload.max_velocity,
        min_velocity=payload.min_velocity,
        note_distribution=payload.note_distribution or None,
    )
    print(f"\n[FastAPI] Received MIDI performance stats for session: {payload.session_id}")
    with _lock:
        result = update_midi_log(vault_dir, stats)
    if result["success"]:
        print(f"[Obsidian Sync] {result['message']}")
    else:
        print(f"[FastAPI Error] {result['error']}")
    return result


# ---------------------------------------------------------------------------
# GET /api/stream-mw10 -- the pipeline progress stream.
#
# This is the endpoint the TS bridge (mw10PipelineBridge.ts) listens to;
# until now it existed only in drafts. The event contract is the one the
# bridge's extractAutomationCue implements: per node,
# {"status": "running", "node": <name>, "step": ..., "progress": ...,
#  "data": <full state>}, then one {"status": "completed", ...} event.
# The nodes are music_pipeline's -- no LangGraph, no simulated sleeps:
# the composer reads the real session note in the configured vault.
# Receiving a cue never writes anything; the bridge holds it for
# approval, per the Gateway rule.
# ---------------------------------------------------------------------------

_PIPELINE_SEQUENCE = (
    ("supervisor", supervisor_node),
    ("composer", composer_node),
    ("mixer", mixer_node),
)


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


async def pipeline_event_generator(
    session_id: str, vault_dir: Path
) -> AsyncGenerator[str, None]:
    state: dict = {
        "session_id": session_id,
        "obsidian_vault_path": str(vault_dir),
        "step": "queued",
        "progress": 0,
        "midi_generation_range": {},
        "mixer_instructions": {},
    }
    print(f"[SSE Stream] Starting pipeline execution stream for session: {session_id}")
    try:
        for node_name, node in _PIPELINE_SEQUENCE:
            state = node(state)
            yield _sse(
                {
                    "status": "running",
                    "node": node_name,
                    "step": state.get("step"),
                    "progress": state.get("progress"),
                    "data": state,
                }
            )
        yield _sse(
            {
                "status": "completed",
                "progress": 100,
                "message": "MW10 pipeline execution successfully completed.",
            }
        )
    except Exception as exc:  # noqa: BLE001 - any pipeline failure must reach
        # the listener as an error event instead of dropping the stream
        yield _sse({"status": "error", "error": str(exc)})


@app.get("/api/stream-mw10")
async def stream_mw10_pipeline(
    session_id: str = "session_live_001",
    x_token: str | None = Header(default=None),
) -> StreamingResponse:
    _check_token(x_token)
    vault_dir = _vault_dir()
    if vault_dir is None:
        raise HTTPException(
            status_code=503,
            detail="OBSIDIAN_VAULT_PATH is not configured on the gateway.",
        )
    return StreamingResponse(
        pipeline_event_generator(session_id, vault_dir),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache"},
    )
