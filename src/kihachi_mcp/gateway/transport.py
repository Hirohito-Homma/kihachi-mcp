"""Song position in beats.

The Remote Script reads ``current_song_time`` and moves it only with
``song.jump_by``. Its locator code records that assigning ``current_song_time``
does not move the transport, and that assigning ``start_time`` moves the
arrangement start marker. Absolute seek is therefore not sent. Relative
movement stays on ``jump_transport``. ``back_to_arranger`` is only read.
"""

from __future__ import annotations

import math
from typing import Any

from kihachi_mcp.gateway.calls import invoke
from kihachi_mcp.gateway.catalog import RISK_CONFIRM

_READ_TIMEOUT = 10.0
_WRITE_TIMEOUT = 20.0
# Same ceiling ``_prepare_arrangement_copy`` uses for a Live beat position.
_MAX_BEAT = 1_576_800.0
_TOLERANCE = 0.001


def ableton_get_song_time() -> dict[str, Any]:
    """Read the song position in beats. Does not move the transport."""
    state, error = invoke("get_transport_state", timeout=_READ_TIMEOUT)
    if error or state is None:
        return _failed(error or "ableton_error")
    return {
        "ok": True,
        "status": "SUCCESS",
        "live_changed": False,
        "unit": "beats",
        "song_time": state.get("current_song_time"),
        "start_time": state.get("start_time"),
        "is_playing": state.get("is_playing"),
        "error": None,
    }


def ableton_set_song_time(beat: object, confirm: bool = False) -> dict[str, Any]:
    """Refuse an absolute seek. This does not call ``jump_by`` or assign a time.

    ``confirm=true`` is required. A bool, NaN, infinity, or a negative beat is
    rejected. A valid beat is still not written: the running Remote Script has
    no assignment command, and its own note says assigning ``current_song_time``
    does not move the transport.
    """
    if not confirm:
        return {
            "ok": False,
            "status": "confirmation_required",
            "risk": RISK_CONFIRM,
            "command": "set_song_time",
            "live_changed": False,
            "message": "confirm=true が無いので Live は変更していません",
            "beat": beat,
        }
    target = _beat(beat)
    if target is None:
        return _rejected("beat は 0 以上の有限な数です")
    return {
        "ok": False,
        "status": "not_supported",
        "live_changed": False,
        "unit": "beats",
        "requested": target,
        "song_time": None,
        "start_time": None,
        "seek_method": None,
        "error": "not_supported",
        "message": "絶対位置の代入コマンドは無く、jump_by では置きません",
    }


def ableton_set_start_time(
    beat: object,
    confirm: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Set the Arrangement start marker. Does not play and does not use jump_by.

    ``start_playing`` was observed to begin at ``start_time``. Assigning
    ``current_song_time`` did not move that marker. ``dry_run`` and a missing
    ``confirm=true`` both leave Live unchanged.
    """
    if dry_run or not confirm:
        status = "dry_run" if dry_run else "confirmation_required"
        return {
            "ok": False,
            "status": status,
            "risk": RISK_CONFIRM,
            "command": "set_start_time",
            "live_changed": False,
            "property": "start_time",
            "message": "Live は変更していません",
            "beat": beat,
        }
    target = _beat(beat)
    if target is None:
        return _rejected("beat は 0 以上の有限な数です")
    ready, ready_error = invoke("get_transport_state", timeout=_READ_TIMEOUT)
    if ready_error or ready is None:
        return _failed(ready_error or "ableton_error")
    if ready.get("is_playing") is not False:
        return _rejected("停止中だけ start_time を書きます")
    written, write_error = invoke("set_start_time", timeout=_WRITE_TIMEOUT, time=target)
    if write_error or written is None:
        return _failed(write_error or "unconfirmed", live_changed=True)
    if written.get("is_playing") is not False:
        return _failed("playback_started", live_changed=True)
    observed, observe_error = invoke("get_transport_state", timeout=_READ_TIMEOUT)
    if observe_error or observed is None:
        return _failed(observe_error or "unconfirmed", live_changed=True)
    start_time = observed.get("start_time")
    matched = _near(start_time, target) and observed.get("is_playing") is False
    return {
        "ok": matched,
        "status": "SUCCESS" if matched else "FAILED",
        "live_changed": True,
        "unit": "beats",
        "requested": target,
        "song_time": observed.get("current_song_time"),
        "start_time": start_time,
        "is_playing": observed.get("is_playing"),
        "property": "start_time",
        "error": None if matched else "unconfirmed",
    }


def ableton_jump_song_time(delta_beats: object, confirm: bool = False) -> dict[str, Any]:
    """Move by ``delta_beats`` with the existing ``jump_transport`` command.

    This is relative. It is not an absolute seek. Transport must already be
    stopped, or this stops once. A failed stop does not jump.
    """
    if not confirm:
        return {
            "ok": False,
            "status": "confirmation_required",
            "risk": RISK_CONFIRM,
            "command": "jump_song_time",
            "live_changed": False,
            "message": "confirm=true が無いので Live は変更していません",
            "delta_beats": delta_beats,
        }
    delta = _delta(delta_beats)
    if delta is None:
        return _rejected("delta_beats は有限な数です")
    ready, ready_error, stopped_first = _ensure_stopped()
    if ready_error or ready is None:
        return _failed(ready_error or "stop_not_guaranteed", live_changed=stopped_first)
    current = ready.get("current_song_time")
    if isinstance(current, bool) or not isinstance(current, (int, float)):
        return _failed("unconfirmed", live_changed=stopped_first)
    target = float(current) + delta
    if target < 0 or target > _MAX_BEAT:
        return _rejected("移動先が範囲外です")
    jumped, jump_error = invoke("jump_transport", timeout=_WRITE_TIMEOUT, time=target)
    if jump_error or jumped is None:
        return _failed(jump_error or "unconfirmed", live_changed=True)
    observed, observe_error = invoke("get_transport_state", timeout=_READ_TIMEOUT)
    if observe_error or observed is None:
        return _failed(observe_error or "unconfirmed", live_changed=True)
    song_time = observed.get("current_song_time")
    matched = _near(song_time, target)
    return {
        "ok": matched and observed.get("is_playing") is False,
        "status": "SUCCESS" if matched and observed.get("is_playing") is False else "FAILED",
        "live_changed": True,
        "unit": "beats",
        "delta_beats": delta,
        "requested": target,
        "song_time": song_time,
        "start_time": observed.get("start_time"),
        "is_playing": observed.get("is_playing"),
        "seek_method": "jump_by",
        "error": None if matched and observed.get("is_playing") is False else "unconfirmed",
    }


def ableton_get_playback_state() -> dict[str, Any]:
    """Read transport flags. Session clip indexes are not in this Remote Script."""
    state, error = invoke("get_transport_state", timeout=_READ_TIMEOUT)
    if error or state is None:
        return {
            "ok": False,
            "status": "FAILED",
            "live_changed": False,
            "song_time": None,
            "is_playing": None,
            "back_to_arranger": None,
            "playing_session_clips": None,
            "fired_clips": None,
            "error": error or "ableton_error",
        }
    playing = state.get("is_playing")
    flag = state.get("back_to_arranger")
    return {
        "ok": True,
        "status": "SUCCESS",
        "live_changed": False,
        "song_time": state.get("current_song_time"),
        "is_playing": playing if isinstance(playing, bool) else None,
        "back_to_arranger": flag if isinstance(flag, bool) else None,
        "playing_session_clips": None,
        "fired_clips": None,
        "start_time": state.get("start_time"),
        "error": None,
    }


def _ensure_stopped() -> tuple[dict[str, Any] | None, str | None, bool]:
    """One stop, then one read. Returns whether a stop command was sent."""
    state, error = invoke("get_transport_state", timeout=_READ_TIMEOUT)
    if error or state is None:
        return None, error or "ableton_error", False
    if state.get("is_playing") is False:
        return state, None, False
    if state.get("is_playing") is not True:
        return None, "stop_not_guaranteed", False
    stopped, stop_error = invoke("set_transport", timeout=_WRITE_TIMEOUT, action="stop")
    if stop_error or stopped is None:
        return None, stop_error or "stop_not_guaranteed", True
    again, again_error = invoke("get_transport_state", timeout=_READ_TIMEOUT)
    if again_error or again is None or again.get("is_playing") is not False:
        return None, again_error or "stop_not_guaranteed", True
    return again, None, True


def _delta(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def _beat(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if math.isnan(number) or math.isinf(number) or number < 0 or number > _MAX_BEAT:
        return None
    return number


def _near(actual: object, expected: float) -> bool:
    if isinstance(actual, bool) or not isinstance(actual, (int, float)):
        return False
    return abs(float(actual) - expected) <= _TOLERANCE


def _rejected(message: str) -> dict[str, Any]:
    return {
        "ok": False,
        "status": "rejected",
        "live_changed": False,
        "error": "rejected",
        "message": message,
    }


def _failed(detail: str, *, live_changed: bool = False) -> dict[str, Any]:
    return {
        "ok": False,
        "status": "FAILED",
        "live_changed": live_changed,
        "error": detail,
    }
