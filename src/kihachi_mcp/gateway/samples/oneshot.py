"""Place one unwarped audio one-shot on the Arrangement.

Only ``placement_mode=audio_one_shot`` may ask Live to duplicate an unwarped
session clip. The normal copy path still refuses that source. This module does
not enable warping, does not map velocity to gain, and does not delete a clip
if the read-back is wrong. The Remote Script has no arrangement-clip delete.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from kihachi_mcp.gateway.calls import invoke
from kihachi_mcp.gateway.catalog import RISK_CONFIRM
from kihachi_mcp.gateway.samples.index import index_path, resolve_import_target

_READ_TIMEOUT = 10.0
_WRITE_TIMEOUT = 30.0
_START_TOLERANCE = 0.000001
ROLLBACK_SUPPORTED = False


def place_audio_one_shot(
    sample_id: str,
    *,
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
    """Copy one existing session clip. ``sample_id`` is the only file key."""
    event = _event(
        sample_id=sample_id,
        target_track=target_track,
        beat=beat,
        source_midi_track=source_midi_track,
        source_note_index=source_note_index,
        source_pitch=source_pitch,
        source_velocity=source_velocity,
        source_duration_seconds=None,
        selection_mode=selection_mode,
    )
    if selection_mode != "technical_probe":
        return _stopped(event, "rejected", "selection_mode")
    if dry_run or not confirm:
        status = "dry_run" if dry_run else "confirmation_required"
        body = _stopped(event, status, "confirm=true が無いので Live は呼んでいません")
        body["risk"] = RISK_CONFIRM
        return body
    try:
        destination = float(beat)
    except (TypeError, ValueError):
        return _stopped(event, "rejected", "beat")
    if destination < 0.0:
        return _stopped(event, "rejected", "beat")
    resolved = resolve_import_target(sample_id)
    if not resolved.get("ok"):
        return _stopped(event, "rejected", str(resolved.get("error") or "rejected"))
    record = resolved.get("record")
    if isinstance(record, dict):
        event["source_duration_seconds"] = record.get("duration_seconds")
    audio = resolved.get("path")
    if not isinstance(audio, Path):
        return _stopped(event, "rejected", "rejected")
    state, error = invoke("get_state", timeout=_READ_TIMEOUT)
    if error or not isinstance(state, dict):
        return _stopped(event, "FAILED", error or "ableton_error")
    if state.get("is_playing"):
        body = _stopped(event, "blocked", "Live が再生中です。再生は始めません")
        return body
    tracks = [track for track in state.get("tracks") or [] if isinstance(track, dict)]
    matches = [track for track in tracks if track.get("name") == target_track]
    if len(matches) != 1 or isinstance(matches[0].get("index"), bool):
        return _stopped(event, "FAILED", "target_track")
    track_index = matches[0].get("index")
    if not isinstance(track_index, int):
        return _stopped(event, "FAILED", "target_track")
    session, session_error = invoke(
        "get_audio_clip_paths",
        timeout=_READ_TIMEOUT,
        track_index=track_index,
        view="session",
    )
    source = _clip_at(session, "session", 0)
    if session_error or source is None:
        return _stopped(event, "FAILED", session_error or "session_source_missing")
    if not _same_file(source.get("file_path"), audio):
        return _stopped(event, "rejected", "sample_mismatch")
    before, before_error = invoke(
        "get_audio_clip_paths",
        timeout=_READ_TIMEOUT,
        track_index=track_index,
        view="arrangement",
    )
    if before_error or not isinstance(before, dict):
        return _stopped(event, "FAILED", before_error or "arrangement_unreadable")
    existing = _clip_at_beat(before.get("clips"), destination)
    if existing is not None:
        if not _same_file(existing.get("file_path"), audio):
            return _stopped(event, "rejected", "arrangement_sample_mismatch")
        return _skip(event, existing, state)
    copied, copy_error = invoke(
        "copy_session_clip_to_arrangement",
        timeout=_WRITE_TIMEOUT,
        track_index=track_index,
        clip_index=0,
        destination_time_beats=destination,
        placement_mode="audio_one_shot",
        file_path=str(audio),
    )
    after, after_error = invoke(
        "get_audio_clip_paths",
        timeout=_READ_TIMEOUT,
        track_index=track_index,
        view="arrangement",
    )
    if _old_rejection(copy_error):
        return {
            **_stopped(event, "validation_rejected", copy_error or "unwarped"),
            "error_class": "unwarped",
            "live_accepted_unwarped": False,
            "script_loaded": False,
            "arrangement_event_count": _count(before),
        }
    added = _added(before.get("clips"), after.get("clips") if isinstance(after, dict) else None)
    if copy_error or after_error or len(added) != 1:
        return {
            "ok": False,
            "status": "FAILED",
            "partial": True,
            "live_changed": _count(after) != _count(before),
            "rollback_supported": ROLLBACK_SUPPORTED,
            "error": copy_error or after_error or "unconfirmed",
            "event": event,
            "arrangement_event_count": _count(after),
            "placement_mode": "audio_one_shot",
        }
    return _accept(
        event,
        added[0],
        state,
        copied if isinstance(copied, dict) else None,
        _count(after),
    )


def _accept(
    event: dict[str, Any],
    placed: dict[str, Any],
    state: dict[str, Any],
    copied: dict[str, Any] | None,
    arrangement_event_count: int,
) -> dict[str, Any]:
    try:
        actual_start = float(placed["start_time"])
        actual_end = float(placed["end_time"])
    except (KeyError, TypeError, ValueError):
        return _failed_read(event, "end_unreadable")
    requested = float(event["beat"])
    if abs(actual_start - requested) > _START_TOLERANCE:
        return _failed_read(event, "start_mismatch", actual_start, actual_end)
    if actual_end <= actual_start:
        return _failed_read(event, "non_positive_duration", actual_start, actual_end)
    duration = actual_end - actual_start
    diagnostic = _diagnostic(placed, state)
    difference = None if diagnostic is None else duration - diagnostic
    if isinstance(copied, dict) and copied.get("warping") not in (None, placed.get("warping")):
        return _failed_read(event, "warping_mismatch", actual_start, actual_end)
    event["source_duration_seconds"] = event.get("source_duration_seconds")
    _remember(event)
    return {
        "ok": True,
        "status": "SUCCESS",
        "live_changed": True,
        "rollback_supported": ROLLBACK_SUPPORTED,
        "live_accepted_unwarped": placed.get("warping") is False,
        "script_loaded": True,
        "event": event,
        "actual_start": actual_start,
        "actual_end": actual_end,
        "actual_duration": duration,
        "diagnostic_duration_beats": diagnostic,
        "duration_difference": difference,
        "warping": placed.get("warping"),
        "arrangement_clip": placed.get("name"),
        "arrangement_event_count": arrangement_event_count,
        "idempotency": "verified",
        "sample_length": placed.get("sample_length"),
        "sample_rate": placed.get("sample_rate"),
        "placement_mode": "audio_one_shot",
    }


def _skip(event: dict[str, Any], existing: dict[str, Any] | None, state: dict[str, Any]) -> dict[str, Any]:
    start = end = duration = None
    if existing is not None:
        try:
            start = float(existing["start_time"])
            end = float(existing["end_time"])
            duration = end - start
        except (KeyError, TypeError, ValueError):
            start = end = duration = None
    return {
        "ok": True,
        "status": "skipped",
        "skipped_existing": True,
        "live_changed": False,
        "rollback_supported": ROLLBACK_SUPPORTED,
        "event": event,
        "actual_start": start,
        "actual_end": end,
        "actual_duration": duration,
        "diagnostic_duration_beats": _diagnostic(existing or {}, state),
        "warping": None if existing is None else existing.get("warping"),
        "placement_mode": "audio_one_shot",
        "idempotency": "verified",
    }


def _event(**fields: Any) -> dict[str, Any]:
    identity = "|".join(
        [
            str(fields["sample_id"]),
            str(fields["target_track"]),
            str(fields["source_midi_track"]),
            str(fields["source_note_index"]),
            str(fields["beat"]),
        ]
    )
    body = {
        "event_id": hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16],
        "sample_id": fields["sample_id"],
        "source_midi_track": fields["source_midi_track"],
        "source_note_index": fields["source_note_index"],
        "source_pitch": fields["source_pitch"],
        "source_velocity": fields["source_velocity"],
        "target_track": fields["target_track"],
        "beat": fields["beat"],
        "source_duration_seconds": fields["source_duration_seconds"],
        "selection_mode": fields["selection_mode"],
        "placement_mode": "audio_one_shot",
    }
    return body


def _diagnostic(clip: dict[str, Any], state: dict[str, Any]) -> float | None:
    try:
        frames = float(clip["sample_length"])
        rate = float(clip["sample_rate"])
        tempo = float(state["tempo"])
    except (KeyError, TypeError, ValueError):
        return None
    if rate <= 0.0 or tempo <= 0.0:
        return None
    return (frames / rate) * (tempo / 60.0)


def _same_file(reported: Any, audio: Path) -> bool:
    if not isinstance(reported, str) or not reported:
        return False
    try:
        return Path(reported).resolve() == audio.resolve()
    except OSError:
        return False


def _clip_at(payload: dict[str, Any] | None, location: str, index: int) -> dict[str, Any] | None:
    if not isinstance(payload, dict) or not isinstance(payload.get("clips"), list):
        return None
    for clip in payload["clips"]:
        if isinstance(clip, dict) and clip.get("location") == location and clip.get("index") == index:
            return clip
    return None


def _added(before: Any, after: Any) -> list[dict[str, Any]]:
    leftover: list[float] = []
    if isinstance(before, list):
        for clip in before:
            if isinstance(clip, dict):
                try:
                    leftover.append(float(clip["start_time"]))
                except (KeyError, TypeError, ValueError):
                    continue
    added: list[dict[str, Any]] = []
    if not isinstance(after, list):
        return added
    for clip in after:
        if not isinstance(clip, dict):
            continue
        try:
            start = float(clip["start_time"])
        except (KeyError, TypeError, ValueError):
            added.append(clip)
            continue
        match = next((index for index, old in enumerate(leftover) if abs(old - start) <= _START_TOLERANCE), None)
        if match is None:
            added.append(clip)
        else:
            leftover.pop(match)
    return added


def _clip_at_beat(clips: Any, beat: float) -> dict[str, Any] | None:
    if not isinstance(clips, list):
        return None
    for clip in clips:
        if not isinstance(clip, dict):
            continue
        try:
            start = float(clip["start_time"])
        except (KeyError, TypeError, ValueError):
            continue
        if abs(start - beat) <= _START_TOLERANCE:
            return clip
    return None


def _count(payload: dict[str, Any] | None) -> int:
    if not isinstance(payload, dict) or not isinstance(payload.get("clips"), list):
        return 0
    return len(payload["clips"])


def _old_rejection(error: str | None) -> bool:
    text = error or ""
    return "warped audio" in text or "collision-safe" in text


def _ledger_path() -> Path:
    return index_path().with_name("audio-one-shot-events.json")


def _ledger_has(event: dict[str, Any]) -> bool:
    for saved in _read_ledger():
        if saved.get("event_id") == event.get("event_id"):
            return True
    return False


def _remember(event: dict[str, Any]) -> None:
    if _ledger_has(event):
        return
    rows = _read_ledger()
    rows.append({key: event.get(key) for key in event})
    path = _ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"events": rows}, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_ledger() -> list[dict[str, Any]]:
    path = _ledger_path()
    if not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    rows = payload.get("events") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def _stopped(event: dict[str, Any], status: str, detail: str) -> dict[str, Any]:
    return {
        "ok": False,
        "status": status,
        "live_changed": False,
        "rollback_supported": ROLLBACK_SUPPORTED,
        "error": detail,
        "event": event,
        "placement_mode": "audio_one_shot",
    }


def _failed_read(
    event: dict[str, Any],
    detail: str,
    actual_start: float | None = None,
    actual_end: float | None = None,
) -> dict[str, Any]:
    return {
        "ok": False,
        "status": "FAILED",
        "partial": True,
        "live_changed": True,
        "rollback_supported": ROLLBACK_SUPPORTED,
        "error": detail,
        "event": event,
        "actual_start": actual_start,
        "actual_end": actual_end,
        "placement_mode": "audio_one_shot",
    }
