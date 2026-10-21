"""Put one indexed sample on an audio track. The source file is not edited.

Arrangement copy reuses copy_session_clip_to_arrangement. That command refuses
unwarped audio, and this gateway has no command that turns warping on.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from kihachi_mcp.gateway.calls import invoke
from kihachi_mcp.gateway.catalog import RISK_CONFIRM
from kihachi_mcp.gateway.samples.index import find_sample, resolve_inside_library
from kihachi_mcp.gateway.samples.meta import public_sample

_READ_TIMEOUT = 10.0
_WRITE_TIMEOUT = 30.0


def place_indexed_sample(
    sample_id: str,
    *,
    title: str,
    role: str,
    confirm: bool = False,
) -> dict[str, Any]:
    if not confirm:
        return {
            "ok": False,
            "status": "confirmation_required",
            "risk": RISK_CONFIRM,
            "live_changed": False,
            "message": "confirm=true が無いのでサンプルは Live に置いていません",
        }
    record = find_sample(sample_id)
    audio = resolve_inside_library(record) if record else None
    if record is None or audio is None:
        return _failed("サンプルが見つからないか、設定したライブラリの外にあります")
    state, error = invoke("get_state", timeout=_READ_TIMEOUT)
    if error or state is None:
        return _failed(error or "ableton_error")
    if state.get("is_playing"):
        return {
            "ok": False,
            "status": "blocked",
            "live_changed": False,
            "message": "Live が再生中です。stop は呼びません",
        }
    track_name = f"{title} - SAMPLE {role.upper()}"
    tracks = [track for track in state.get("tracks") or [] if isinstance(track, dict)]
    matches = [track for track in tracks if track.get("name") == track_name]
    if len(matches) > 1:
        return _failed("同名のサンプルトラックが複数あります")
    if matches:
        track_index = matches[0].get("index")
        created = False
    else:
        made, create_error = invoke(
            "create_track",
            timeout=_WRITE_TIMEOUT,
            track_type="audio",
            index=-1,
            name=track_name,
        )
        if create_error or made is None or made.get("name") != track_name:
            return _failed(create_error or "unconfirmed")
        track_index = made.get("index")
        created = True
    if isinstance(track_index, bool) or not isinstance(track_index, int):
        return _failed("トラック番号を読めません")
    imported, import_status = _ensure_session_clip(track_index, audio, track_name)
    if import_status != "ok":
        return {
            "ok": False,
            "status": "FAILED",
            "partial": created,
            "live_changed": created,
            "error": imported or import_status,
            "arrangement": "SKIPPED",
        }
    arranged, arrange_status = _ensure_arrangement(track_index, track_name, audio)
    body = {
        "ok": arrange_status == "ok",
        "status": "SUCCESS" if arrange_status == "ok" else "FAILED",
        "partial": arrange_status != "ok",
        "live_changed": True,
        "sample": public_sample(record),
        "track_name": track_name,
        "track_index": track_index,
        "session": "SUCCESS",
        "arrangement": "SUCCESS" if arrange_status == "ok" else "FAILED",
        "detail": arranged,
    }
    return body


def _ensure_session_clip(track_index: int, audio: Path, name: str) -> tuple[str | None, str]:
    current, error = invoke(
        "get_audio_clip_paths",
        timeout=_READ_TIMEOUT,
        track_index=track_index,
        view="session",
    )
    if error:
        return error, "failed"
    existing = _clip_at(current, "session", 0)
    if existing:
        if not _same_file(existing.get("file_path"), audio):
            return "Session スロット 0 には別のサンプルがあります", "failed"
        return None, "ok"
    imported, import_error = invoke(
        "import_audio_clip",
        timeout=_WRITE_TIMEOUT,
        track_index=track_index,
        clip_index=0,
        file_path=str(audio),
        name=name,
    )
    if import_error or imported is None:
        return import_error or "unconfirmed", "failed"
    read_back, read_error = invoke(
        "get_audio_clip_paths",
        timeout=_READ_TIMEOUT,
        track_index=track_index,
        view="session",
    )
    clip = _clip_at(read_back, "session", 0)
    if read_error or clip is None or not _same_file(clip.get("file_path"), audio):
        return read_error or "Session スロット 0 のサンプルを読み戻せません", "failed"
    return None, "ok"


def _ensure_arrangement(track_index: int, name: str, audio: Path) -> tuple[str | None, str]:
    session, error = invoke(
        "get_audio_clip_paths",
        timeout=_READ_TIMEOUT,
        track_index=track_index,
        view="session",
    )
    clip = _clip_at(session, "session", 0)
    if error or clip is None:
        return error or "session clip を読めません", "failed"
    if clip.get("warping") is not True:
        return "ワープされていない音声は Arrangement へコピーできません。ワープを付けるコマンドはありません", "failed"
    arranged, arrange_error = invoke(
        "get_audio_clip_paths",
        timeout=_READ_TIMEOUT,
        track_index=track_index,
        view="arrangement",
    )
    if arrange_error:
        return arrange_error, "failed"
    if not isinstance(arranged, dict) or not isinstance(arranged.get("clips"), list):
        return "Arrangement の既存クリップを読めません", "failed"
    existing = _arranged_clip(arranged)
    if existing is not None:
        if _same_file(existing.get("file_path"), audio):
            return None, "ok"
        return "Arrangement に別サンプルのクリップが既にあります", "failed"
    if arranged and arranged.get("clips"):
        return "Arrangement の既存クリップを識別できません", "failed"
    copied, copy_error = invoke(
        "copy_session_clip_to_arrangement",
        timeout=_WRITE_TIMEOUT,
        track_index=track_index,
        clip_index=0,
        destination_time_beats=0.0,
        name=name,
    )
    if copy_error or copied is None or not copied.get("is_audio_clip"):
        return copy_error or "unconfirmed", "failed"
    read_back, read_error = invoke(
        "get_audio_clip_paths",
        timeout=_READ_TIMEOUT,
        track_index=track_index,
        view="arrangement",
    )
    clip = _arranged_clip(read_back)
    if read_error or clip is None or not _same_file(clip.get("file_path"), audio):
        return read_error or "Arrangement のサンプルを読み戻せません", "failed"
    return None, "ok"


def _clip_at(payload: dict[str, Any] | None, location: str, index: int) -> dict[str, Any] | None:
    if not payload:
        return None
    clips = payload.get("clips")
    if not isinstance(clips, list):
        return None
    for clip in clips:
        if isinstance(clip, dict) and clip.get("location") == location and clip.get("index") == index:
            return clip
    return None


def _arranged_clip(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if not payload or not isinstance(payload.get("clips"), list):
        return None
    return next(
        (
            clip
            for clip in payload["clips"]
            if isinstance(clip, dict) and clip.get("location") == "arrangement"
        ),
        None,
    )


def _same_file(reported: Any, audio: Path) -> bool:
    if not isinstance(reported, str) or not reported:
        return False
    try:
        return Path(reported).resolve() == audio.resolve()
    except OSError:
        return False


def _failed(detail: str) -> dict[str, Any]:
    return {"ok": False, "status": "FAILED", "live_changed": False, "error": detail, "arrangement": "SKIPPED"}
