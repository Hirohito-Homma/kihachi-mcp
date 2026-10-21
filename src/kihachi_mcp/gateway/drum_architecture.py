"""MIDI instrument architecture on top of documented Live 12 calls.

Writes require ``confirm=true``. A result is verified only when a later read
matches the request. ``Clip.start_time`` is get-only, so a move that would
overlap the source clip is refused.
"""

from __future__ import annotations

import os
from typing import Any

from kihachi_mcp.gateway.calls import invoke
from kihachi_mcp.gateway.song_time import NoteError, live_note

_READ_TIMEOUT = 15.0
_WRITE_TIMEOUT = 45.0
_AUDIO_SUFFIXES = {".wav", ".aif", ".aiff", ".flac"}
_SIMPLER_CLASSES = {"OriginalSimpler", "Simpler"}


def load_sample_to_simpler(
    track_index: int,
    sample_path: str,
    *,
    confirm: bool = False,
    dry_run: bool = False,
    insert_if_missing: bool = True,
) -> dict[str, Any]:
    """Put one sample on one Simpler. Reads the sample path back."""
    path, error = _audio_path(sample_path)
    if error:
        return _rejected(error)
    refused = _track_index(track_index)
    if refused:
        return refused
    if dry_run or not confirm:
        return _held("load_simpler_sample", dry_run, track_index=track_index, sample_name=os.path.basename(path or ""))
    result, call_error = invoke(
        "load_simpler_sample",
        timeout=_WRITE_TIMEOUT,
        track_index=track_index,
        file_path=path,
        insert_if_missing=insert_if_missing,
    )
    return _sample_result(result, call_error, path)


def list_drum_pads(track_index: int) -> dict[str, Any]:
    """Read Drum Rack pads. Does not change Live."""
    refused = _track_index(track_index)
    if refused:
        return refused
    result, error = invoke("list_drum_pads", timeout=_READ_TIMEOUT, track_index=track_index)
    if error or result is None:
        return _failed(error or "ableton_error")
    pads = result.get("pads")
    if not isinstance(pads, list):
        return _unverified(result, "pad list missing")
    return {"ok": True, "status": "SUCCESS", "live_changed": False, **result}


def load_sample_to_pad(
    track_index: int,
    note: int,
    sample_path: str,
    *,
    confirm: bool = False,
    dry_run: bool = False,
    insert_if_missing: bool = True,
) -> dict[str, Any]:
    """Load one sample onto the Drum Rack pad for one MIDI note."""
    path, error = _audio_path(sample_path)
    if error:
        return _rejected(error)
    refused = _track_index(track_index)
    if refused:
        return refused
    if isinstance(note, bool) or not isinstance(note, int) or not 0 <= note <= 127:
        return _rejected("note は 0 から 127 の整数です")
    if dry_run or not confirm:
        return _held("load_drum_pad_sample", dry_run, track_index=track_index, note=note)
    result, call_error = invoke(
        "load_drum_pad_sample",
        timeout=_WRITE_TIMEOUT,
        track_index=track_index,
        note=note,
        file_path=path,
        insert_if_missing=insert_if_missing,
    )
    verified = _sample_result(result, call_error, path)
    if verified.get("ok") and result is not None and int(result.get("note", -1)) != note:
        return _unverified(result, "pad note readback mismatch")
    return verified


def get_arrangement_midi_notes(
    track_index: int,
    start_time: float,
    end_time: float,
    name: str | None = None,
) -> dict[str, Any]:
    """Read notes from the one Arrangement MIDI clip at this span."""
    target = _clip_target(track_index, start_time, end_time, name)
    if target.get("ok") is False:
        return target
    result, error = invoke("get_arrangement_midi_notes", timeout=_READ_TIMEOUT, **target)
    if error or result is None:
        return _failed(error or "ableton_error")
    if not isinstance(result.get("notes"), list):
        return _unverified(result, "notes missing")
    return {"ok": True, "status": "SUCCESS", "live_changed": False, **result}


def replace_arrangement_midi_notes(
    track_index: int,
    start_time: float,
    end_time: float,
    notes: list[dict[str, Any]],
    *,
    confirm: bool = False,
    dry_run: bool = False,
    name: str | None = None,
    allow_clear: bool = False,
) -> dict[str, Any]:
    """Replace every note in one Arrangement MIDI clip."""
    return _write_notes(
        track_index,
        start_time,
        end_time,
        notes,
        confirm=confirm,
        dry_run=dry_run,
        name=name,
        allow_clear=allow_clear,
    )


def add_arrangement_midi_notes(
    track_index: int,
    start_time: float,
    end_time: float,
    notes: list[dict[str, Any]],
    *,
    confirm: bool = False,
    dry_run: bool = False,
    name: str | None = None,
) -> dict[str, Any]:
    """Append notes. The existing notes are read and written back with them."""
    if dry_run or not confirm:
        target = _clip_target(track_index, start_time, end_time, name)
        if target.get("ok") is False:
            return target
        return _held("add_arrangement_midi_notes", dry_run, **target)
    current = get_arrangement_midi_notes(track_index, start_time, end_time, name)
    if not current.get("ok"):
        return current
    existing = [_public_note(note) for note in current.get("notes") or []]
    return _write_notes(
        track_index,
        start_time,
        end_time,
        existing + list(notes),
        confirm=True,
        name=name,
        expected=current.get("note_count"),
    )


def delete_arrangement_midi_notes(
    track_index: int,
    start_time: float,
    end_time: float,
    notes: list[dict[str, Any]],
    *,
    confirm: bool = False,
    dry_run: bool = False,
    name: str | None = None,
) -> dict[str, Any]:
    """Delete notes identified by pitch and start_time. Each selector must match one note."""
    if dry_run or not confirm:
        target = _clip_target(track_index, start_time, end_time, name)
        if target.get("ok") is False:
            return target
        return _held("delete_arrangement_midi_notes", dry_run, **target)
    if not isinstance(notes, list) or not notes:
        return _rejected("notes は 1 件以上です")
    current = get_arrangement_midi_notes(track_index, start_time, end_time, name)
    if not current.get("ok"):
        return current
    existing = list(current.get("notes") or [])
    remove_at: set[int] = set()
    for selector in notes:
        if not isinstance(selector, dict):
            return _rejected("note はオブジェクトです")
        try:
            pitch = int(selector["pitch"])
            start = float(selector["start_time"] if "start_time" in selector else selector["start"])
        except (KeyError, TypeError, ValueError):
            return _rejected("削除対象は pitch と start_time です")
        hits = [
            index
            for index, note in enumerate(existing)
            if int(note["pitch"]) == pitch and abs(float(note["start_time"]) - start) <= 0.000001
        ]
        if len(hits) != 1:
            return _rejected("削除対象が一つに定まりません")
        remove_at.add(hits[0])
    kept = [_public_note(note) for index, note in enumerate(existing) if index not in remove_at]
    return _write_notes(
        track_index,
        start_time,
        end_time,
        kept,
        confirm=True,
        name=name,
        allow_clear=True,
        expected=current.get("note_count"),
    )


def move_arrangement_clip(
    track_index: int,
    start_time: float,
    end_time: float,
    destination_time: float,
    *,
    confirm: bool = False,
    dry_run: bool = False,
    name: str | None = None,
) -> dict[str, Any]:
    """Move one clip to a span that does not overlap any current clip."""
    return _relocate(
        "move_arrangement_clip",
        track_index,
        start_time,
        end_time,
        destination_time,
        confirm=confirm,
        dry_run=dry_run,
        name=name,
    )


def duplicate_arrangement_clip(
    track_index: int,
    start_time: float,
    end_time: float,
    destination_time: float,
    *,
    confirm: bool = False,
    dry_run: bool = False,
    name: str | None = None,
) -> dict[str, Any]:
    """Copy one clip to a free span. The source stays."""
    return _relocate(
        "duplicate_arrangement_clip",
        track_index,
        start_time,
        end_time,
        destination_time,
        confirm=confirm,
        dry_run=dry_run,
        name=name,
    )


def delete_arrangement_clip(
    track_index: int,
    start_time: float,
    end_time: float,
    *,
    confirm: bool = False,
    dry_run: bool = False,
    name: str | None = None,
) -> dict[str, Any]:
    """Delete the one clip with this start and end. Several matches are refused."""
    target = _clip_target(track_index, start_time, end_time, name)
    if target.get("ok") is False:
        return target
    if dry_run or not confirm:
        return _held("delete_arrangement_clip", dry_run, **target)
    result, error = invoke("delete_arrangement_clip", timeout=_WRITE_TIMEOUT, **target)
    if error or result is None:
        return _failed(error or "ableton_error", live_changed=False)
    observed, read_error = invoke("get_arrangement_clips", timeout=_READ_TIMEOUT, track_index=track_index)
    if read_error or observed is None:
        return _unverified(result, read_error or "readback failed", live_changed=True)
    still_there = _matching_clips(observed.get("clips") or [], start_time, end_time, name)
    verified = result.get("verified") is True and not still_there and result.get("remaining") == 0
    if not verified:
        return _unverified(result, "clip still present", live_changed=True)
    return {
        "ok": True,
        "status": "VERIFIED",
        "live_changed": True,
        "start_time": start_time,
        "end_time": end_time,
        "clip_count_after": result.get("clip_count_after"),
    }


def _write_notes(
    track_index: int,
    start_time: float,
    end_time: float,
    notes: list[dict[str, Any]],
    *,
    confirm: bool,
    dry_run: bool = False,
    name: str | None = None,
    allow_clear: bool = False,
    expected: int | None = None,
) -> dict[str, Any]:
    target = _clip_target(track_index, start_time, end_time, name)
    if target.get("ok") is False:
        return target
    try:
        payload = [_remote_note(note) for note in notes]
    except NoteError as exc:
        return _rejected(str(exc))
    if not payload and not allow_clear:
        return _rejected("notes が空です")
    if dry_run or not confirm:
        return _held("replace_arrangement_midi_notes", dry_run, **target)
    if expected is None:
        current = get_arrangement_midi_notes(track_index, start_time, end_time, name)
        if not current.get("ok"):
            return current
        expected = current.get("note_count")
    if not isinstance(expected, int):
        return _failed("note count missing")
    result, error = invoke(
        "replace_arrangement_midi_notes",
        timeout=_WRITE_TIMEOUT,
        notes=payload,
        expected_source_note_count=expected,
        allow_clear=allow_clear,
        **target,
    )
    if error or result is None:
        return _failed(error or "ableton_error")
    readback = result.get("notes")
    verified = result.get("verified") is True and isinstance(readback, list) and len(readback) == len(payload)
    if not verified:
        return _unverified(result, "note count readback mismatch", live_changed=True)
    return {
        "ok": True,
        "status": "VERIFIED",
        "live_changed": True,
        "note_count": len(readback),
        "notes": readback,
        "start_time": result.get("start_time"),
        "end_time": result.get("end_time"),
    }


def _relocate(
    command: str,
    track_index: int,
    start_time: float,
    end_time: float,
    destination_time: float,
    *,
    confirm: bool,
    dry_run: bool,
    name: str | None,
) -> dict[str, Any]:
    target = _clip_target(track_index, start_time, end_time, name)
    if target.get("ok") is False:
        return target
    if isinstance(destination_time, bool) or not isinstance(destination_time, (int, float)):
        return _rejected("destination_time は数値です")
    destination = float(destination_time)
    if destination < 0:
        return _rejected("destination_time は 0 以上です")
    if dry_run or not confirm:
        return _held(command, dry_run, destination_time=destination, **target)
    result, error = invoke(command, timeout=_WRITE_TIMEOUT, destination_time=destination, **target)
    if error or result is None:
        return _failed(error or "ableton_error")
    observed, read_error = invoke("get_arrangement_clips", timeout=_READ_TIMEOUT, track_index=track_index)
    if read_error or observed is None:
        return _unverified(result, read_error or "readback failed", live_changed=True)
    length = float(end_time) - float(start_time)
    moved = _matching_clips(observed.get("clips") or [], destination, destination + length, None)
    source = _matching_clips(observed.get("clips") or [], start_time, end_time, name)
    source_ok = len(source) == (0 if command == "move_arrangement_clip" else 1)
    verified = result.get("verified") is True and len(moved) == 1 and source_ok
    if not verified:
        return _unverified(result, "arrangement position readback mismatch", live_changed=True)
    return {
        "ok": True,
        "status": "VERIFIED",
        "live_changed": True,
        "start_time": moved[0].get("start_time"),
        "end_time": moved[0].get("end_time"),
        "source_remaining": len(source),
    }


def _sample_result(result: dict[str, Any] | None, error: str | None, path: str) -> dict[str, Any]:
    if error or result is None:
        return _failed(error or "ableton_error")
    actual = result.get("sample_path")
    class_name = str(result.get("device_class") or "")
    same = isinstance(actual, str) and actual and os.path.realpath(actual) == os.path.realpath(path)
    if result.get("verified") is True and class_name in _SIMPLER_CLASSES and same:
        return {
            "ok": True,
            "status": "VERIFIED",
            "live_changed": True,
            "device": "Simpler",
            "device_class": class_name,
            "sample_name": result.get("sample_name") or os.path.basename(actual),
            "sample_path": actual,
            "note": result.get("note"),
            "playback_mode": result.get("playback_mode"),
        }
    return _unverified(result, "sample readback mismatch", live_changed=True)


def _audio_path(sample_path: object) -> tuple[str | None, str | None]:
    if not isinstance(sample_path, str) or not sample_path:
        return None, "sample_path は空でない文字列です"
    if not os.path.isabs(sample_path):
        return None, "sample_path は絶対パスです"
    if os.path.splitext(sample_path)[1].lower() not in _AUDIO_SUFFIXES:
        return None, "sample_path は wav, aif, aiff, flac です"
    if not os.path.isfile(sample_path):
        return None, "sample_path のファイルがありません"
    return os.path.realpath(sample_path), None


def _track_index(track_index: object) -> dict[str, Any] | None:
    if isinstance(track_index, bool) or not isinstance(track_index, int) or track_index < 0:
        return _rejected("track_index は 0 以上の整数です")
    return None


def _clip_target(track_index: int, start_time: object, end_time: object, name: str | None) -> dict[str, Any]:
    refused = _track_index(track_index)
    if refused:
        return refused
    if isinstance(start_time, bool) or isinstance(end_time, bool):
        return _rejected("start_time と end_time は数値です")
    if not isinstance(start_time, (int, float)) or not isinstance(end_time, (int, float)):
        return _rejected("start_time と end_time は数値です")
    if float(end_time) <= float(start_time):
        return _rejected("end_time は start_time より大きいです")
    if name is not None and not isinstance(name, str):
        return _rejected("name は文字列です")
    target: dict[str, Any] = {
        "track_index": track_index,
        "start_time": float(start_time),
        "end_time": float(end_time),
    }
    if name is not None:
        target["name"] = name
    return target


def _remote_note(note: dict[str, Any]) -> dict[str, Any]:
    checked = live_note(note)
    mute = note.get("mute", False)
    if not isinstance(mute, bool):
        raise NoteError("mute は true か false です")
    checked["mute"] = mute
    return checked


def _public_note(note: dict[str, Any]) -> dict[str, Any]:
    return {
        "pitch": int(note["pitch"]),
        "start_time": float(note["start_time"]),
        "duration": float(note["duration"]),
        "velocity": int(note["velocity"]),
        "mute": bool(note.get("mute", False)),
    }


def _matching_clips(clips: list[Any], start: float, end: float, name: str | None) -> list[dict[str, Any]]:
    found = []
    for clip in clips:
        if not isinstance(clip, dict):
            continue
        if abs(float(clip.get("start_time", -1)) - float(start)) > 0.000001:
            continue
        if abs(float(clip.get("end_time", -1)) - float(end)) > 0.000001:
            continue
        if name is not None and clip.get("name") != name:
            continue
        found.append(clip)
    return found


def _held(command: str, dry_run: bool = False, **fields: Any) -> dict[str, Any]:
    status = "dry_run" if dry_run else "confirmation_required"
    return {"ok": False, "status": status, "live_changed": False, "command": command, **fields}


def _rejected(message: str) -> dict[str, Any]:
    return {"ok": False, "status": "rejected", "live_changed": False, "error": message}


def _failed(message: str, live_changed: bool = False) -> dict[str, Any]:
    return {"ok": False, "status": "FAILED", "live_changed": live_changed, "error": message}


def _unverified(result: dict[str, Any], message: str, live_changed: bool = False) -> dict[str, Any]:
    return {
        "ok": False,
        "status": "UNVERIFIED",
        "live_changed": live_changed,
        "error": message,
        "readback": result,
    }
