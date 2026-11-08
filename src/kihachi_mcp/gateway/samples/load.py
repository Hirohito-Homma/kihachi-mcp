"""Import one indexed sample as a Session audio clip.

``import_audio_clip`` already calls ``slot.create_audio_clip``. This module
does not invent a Simpler sample setter and does not copy unwarped audio to
the Arrangement.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from kihachi_mcp.gateway.calls import invoke
from kihachi_mcp.gateway.catalog import RISK_CONFIRM
from kihachi_mcp.gateway.samples.index import resolve_import_target, write_manifest
from kihachi_mcp.gateway.samples.meta import public_sample
from kihachi_mcp.gateway.sound import ensure_stopped, stop_once

PROBE_TRACK = "KIHACHI SPLICE AUDIO PROBE"
_READ_TIMEOUT = 10.0
_WRITE_TIMEOUT = 30.0
_MODEST_VOLUME = 0.70
_SECRET_KEYS = frozenset({"path", "file_path", "filepath", "internal_path"})


def sample_loading_capabilities() -> dict[str, Any]:
    """What the running Remote Script can do. This does not call Live."""
    return {
        "ok": True,
        "live_changed": False,
        "audio_clip_import": "SUPPORTED",
        "browser_sample_access": "PARTIAL",
        "simpler_insert": "IMPLEMENTED",
        "simpler_sample_load": "IMPLEMENTED",
        "sample_read_back": "IMPLEMENTED",
        "live_acceptance": "NOT RUN",
        "notes": {
            "audio_clip_import": "空の Session スロットへ slot.create_audio_clip。公開応答からパスは落とす",
            "browser_sample_access": "browse_presets は name, is_loadable, uri を返す。ユーザフォルダの一覧と設定は無い",
            "simpler_insert": "load_simpler_sample が空の MIDI トラックへ insert_device('Simpler') する",
            "simpler_sample_load": "Simpler.replace_sample(file_path)。sample.file_path の読み戻しが一致したときだけ VERIFIED",
            "sample_read_back": "Simpler.sample.file_path。Live 上の受入はまだ",
        },
    }


def probe_sample_audio_clip(
    sample_id: str,
    *,
    confirm: bool = False,
    selection_mode: str = "technical_probe",
) -> dict[str, Any]:
    """Append one probe audio track and import one sample. Arrangement is not used."""
    if selection_mode != "technical_probe":
        return _rejected("selection_mode は technical_probe だけです")
    if not confirm:
        return {
            "ok": False,
            "status": "confirmation_required",
            "risk": RISK_CONFIRM,
            "live_changed": False,
            "selection_mode": selection_mode,
            "message": "confirm=true が無いので Live には置いていません",
        }
    resolved = resolve_import_target(sample_id)
    if not resolved.get("ok"):
        return _rejected(str(resolved.get("error") or "rejected"))
    record = resolved["record"]
    audio = resolved["path"]
    ready = ensure_stopped()
    if not ready.get("stopped"):
        return {
            "ok": False,
            "status": "blocked",
            "live_changed": bool(ready.get("live_changed")),
            "stopped": False,
            "message": "停止を確認できないので再生もしません",
        }
    state, error = invoke("get_state", timeout=_READ_TIMEOUT)
    if error or state is None:
        return _failed(error or "ableton_error")
    tracks = [track for track in state.get("tracks") or [] if isinstance(track, dict)]
    matches = [track for track in tracks if track.get("name") == PROBE_TRACK]
    if len(matches) > 1:
        return _failed("同名のプローブが複数あります")
    created = False
    if matches:
        track_index = matches[0].get("index")
    else:
        made, create_error = invoke(
            "create_track",
            timeout=_WRITE_TIMEOUT,
            track_type="audio",
            index=-1,
            name=PROBE_TRACK,
        )
        if create_error or made is None or made.get("name") != PROBE_TRACK:
            return _failed(create_error or "unconfirmed", live_changed=False)
        track_index = made.get("index")
        created = True
    if isinstance(track_index, bool) or not isinstance(track_index, int):
        return _failed("トラック番号を読めません", live_changed=created)
    imported = _import_once(track_index, audio, record)
    if not imported.get("ok"):
        imported["live_changed"] = created or bool(imported.get("live_changed"))
        return _public(imported)
    if not imported.get("imported"):
        return _public(
            {
                "ok": True,
                "status": "already_present",
                "live_changed": created,
                "probe_created": created,
                "selection_mode": "technical_probe",
                "song_adoption": False,
                "duplicate": True,
                "playback": "NOT_REPEATED",
                "sample": _identity(record, imported.get("clip")),
                "arrangement": "NOT_ATTEMPTED",
            }
        )
    level = _modest_volume(track_index)
    played = _play_and_stop(track_index)
    body = {
        "ok": bool(played.get("stopped")) and bool(played.get("meter_activity")),
        "status": "verified_by_meter" if played.get("meter_activity") and played.get("stopped") else "pending",
        "live_changed": True,
        "probe_created": created,
        "selection_mode": "technical_probe",
        "song_adoption": False,
        "track_name": PROBE_TRACK,
        "track_index": track_index,
        "sample": _identity(record, imported.get("clip")),
        "volume": level,
        "playback": played,
        "arrangement": "NOT_ATTEMPTED",
        "human": "HUMAN_NOT_VERIFIED",
        "assignment": _assignment(record, played),
    }
    if played.get("meter_activity") and played.get("stopped"):
        write_manifest("technical-probe-audio", body["assignment"])
    return _public(body)


def _import_once(track_index: int, audio: Path, record: dict[str, Any]) -> dict[str, Any]:
    current, error = invoke(
        "get_audio_clip_paths",
        timeout=_READ_TIMEOUT,
        track_index=track_index,
        view="session",
    )
    if error:
        return _failed(error)
    existing = _clip_at(current, 0)
    if existing:
        if not _same_file(existing.get("file_path"), audio):
            return _failed("スロット 0 には別のクリップがあります")
        return {"ok": True, "imported": False, "clip": existing}
    imported, import_error = invoke(
        "import_audio_clip",
        timeout=_WRITE_TIMEOUT,
        track_index=track_index,
        clip_index=0,
        file_path=str(audio),
        name=str(record.get("filename") or PROBE_TRACK),
    )
    if import_error or imported is None:
        return _failed(import_error or "unconfirmed", live_changed=True)
    read_back, read_error = invoke(
        "get_audio_clip_paths",
        timeout=_READ_TIMEOUT,
        track_index=track_index,
        view="session",
    )
    clip = _clip_at(read_back, 0)
    if read_error or clip is None or not _same_file(clip.get("file_path"), audio):
        return _failed(read_error or "read_back_mismatch", live_changed=True)
    clip["length"] = imported.get("length")
    return {"ok": True, "imported": True, "live_changed": True, "clip": clip}


def _modest_volume(track_index: int) -> dict[str, Any]:
    snapshot, error = invoke("get_mix_snapshot", timeout=_READ_TIMEOUT)
    current = None
    if not error and isinstance(snapshot, dict):
        for track in snapshot.get("tracks") or []:
            if isinstance(track, dict) and track.get("index") == track_index:
                current = track.get("volume")
    if current == _MODEST_VOLUME:
        return {"volume": current, "changed": False}
    written, write_error = invoke(
        "set_track_volume",
        timeout=_WRITE_TIMEOUT,
        track_index=track_index,
        volume=_MODEST_VOLUME,
    )
    if write_error or written is None:
        return {"volume": current, "changed": False, "error": _safe(write_error)}
    return {"volume": written.get("volume"), "changed": True}


def _play_and_stop(track_index: int) -> dict[str, Any]:
    ready = ensure_stopped()
    if not ready.get("stopped"):
        return {"playback_started": False, "meter_activity": False, "stopped": False, "samples": []}
    before = _meters(track_index)
    fired, error = invoke("fire_clip", timeout=_WRITE_TIMEOUT, track_index=track_index, clip_index=0)
    samples = []
    if error or fired is None:
        ended = stop_once()
        return {
            "playback_started": False,
            "meter_activity": False,
            "stopped": bool(ended.get("stopped")),
            "samples": samples,
            "error": _safe(error),
        }
    peak_track = _level(before.get("track"))
    peak_master = _level(before.get("master"))
    for _ in range(5):
        time.sleep(0.05)
        reading = _meters(track_index)
        samples.append(reading)
        peak_track = _higher(peak_track, reading.get("track"))
        peak_master = _higher(peak_master, reading.get("master"))
    ended = stop_once()
    invoke("stop_track_clips", timeout=_WRITE_TIMEOUT, track_index=track_index)
    state, state_error = invoke("get_state", timeout=_READ_TIMEOUT)
    playing = True if state is None else state.get("is_playing") is not False
    rose = _rose(before.get("track"), peak_track)
    return {
        "playback_started": True,
        "meter_activity": rose,
        "stopped": (not playing) and bool(ended.get("stopped")) and not state_error,
        "before": before,
        "samples": samples,
        "peak_track": peak_track,
        "peak_master": peak_master,
        "human": "HUMAN_NOT_VERIFIED",
    }


def _meters(track_index: int) -> dict[str, Any]:
    snapshot, error = invoke("get_mix_snapshot", timeout=_READ_TIMEOUT)
    if error or not isinstance(snapshot, dict):
        return {"track": None, "master": None}
    track_level = None
    for track in snapshot.get("tracks") or []:
        if isinstance(track, dict) and track.get("index") == track_index:
            track_level = track.get("output_meter_level")
    master = snapshot.get("master") if isinstance(snapshot.get("master"), dict) else {}
    return {"track": track_level, "master": master.get("output_meter_level")}


def _assignment(record: dict[str, Any], played: dict[str, Any]) -> dict[str, Any]:
    verified = bool(played.get("meter_activity")) and bool(played.get("stopped"))
    view = public_sample(record)
    return {
        "track_role": "kick",
        "selection_mode": "technical_probe",
        "song_adoption": False,
        "preferred_source_type": "sample_instrument",
        "actual_source_type": "audio",
        "source_type": "audio",
        "sample_id": view.get("sample_id"),
        "filename": view.get("filename"),
        "source": view.get("source"),
        "role": "kick",
        "device": None,
        "preferred_device": "Simpler",
        "trigger_note": None,
        "original_bpm": view.get("bpm"),
        "original_key": view.get("key"),
        "verification": "verified_by_meter" if verified else "pending",
        "reason": "Simpler へのサンプル代入はできない。ワンショットをオーディオクリップとして再生した",
    }


def _identity(record: dict[str, Any], clip: dict[str, Any] | None) -> dict[str, Any]:
    view = public_sample(record)
    clip = clip or {}
    return {
        "sample_id": view.get("sample_id"),
        "filename": view.get("filename"),
        "clip_name": clip.get("name"),
        "length": clip.get("length"),
        "sample_length": clip.get("sample_length"),
        "sample_rate": clip.get("sample_rate"),
        "channels": None,
        "warping": clip.get("warping"),
        "identity_match": True,
    }


def _same_file(reported: object, audio: Path) -> bool:
    if not isinstance(reported, str) or not reported:
        return False
    try:
        return Path(reported).resolve() == audio.resolve()
    except OSError:
        return False


def _clip_at(payload: dict[str, Any] | None, index: int) -> dict[str, Any] | None:
    if not isinstance(payload, dict) or not isinstance(payload.get("clips"), list):
        return None
    for clip in payload["clips"]:
        if isinstance(clip, dict) and clip.get("location") == "session" and clip.get("index") == index:
            return clip
    return None


def _level(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _higher(current: float | None, value: object) -> float | None:
    reading = _level(value)
    if reading is None:
        return current
    if current is None or reading > current:
        return reading
    return current


def _rose(before: object, peak: float | None) -> bool:
    start = _level(before)
    if start is None or peak is None:
        return False
    return peak > start and peak > 0


def _rejected(message: str) -> dict[str, Any]:
    return {"ok": False, "status": "rejected", "live_changed": False, "error": _safe(message)}


def _failed(detail: str | None, *, live_changed: bool = False) -> dict[str, Any]:
    return {
        "ok": False,
        "status": "FAILED",
        "live_changed": live_changed,
        "error": _safe(detail),
        "arrangement": "NOT_ATTEMPTED",
    }


def _safe(detail: str | None) -> str | None:
    if detail is None:
        return None
    text = str(detail)
    if "/" in text or "\\" in text or len(text) > 200:
        return "ableton_error"
    return text


def _public(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _public(item) for key, item in value.items() if key not in _SECRET_KEYS}
    if isinstance(value, list):
        return [_public(item) for item in value]
    if isinstance(value, Path):
        return None
    return value
