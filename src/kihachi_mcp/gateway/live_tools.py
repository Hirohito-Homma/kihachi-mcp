"""AbletonGPT tools for transport, tracks, clips, notes, mixer, and one song.

Writes need confirm=true. The song tool also accepts dry_run=true, which
returns the plan and does not call Live. Track creation only appends.
"""

from __future__ import annotations

import math
from typing import Any

from kihachi_mcp.gateway.calls import invoke
from kihachi_mcp.gateway.catalog import RISK_CONFIRM, RISK_DESTRUCTIVE, RISK_SAFE
from kihachi_mcp.gateway.drum_architecture import (
    add_arrangement_midi_notes,
    delete_arrangement_clip,
    delete_arrangement_midi_notes,
    duplicate_arrangement_clip,
    get_arrangement_midi_notes,
    list_drum_pads,
    load_sample_to_pad,
    load_sample_to_simpler,
    move_arrangement_clip,
    replace_arrangement_midi_notes,
)
from kihachi_mcp.gateway.samples.tools import attach_sample_plan, register_sample_tools
from kihachi_mcp.gateway.song_execute import execute_song_plan
from kihachi_mcp.gateway.song_plan import build_song_plan, public_view
from kihachi_mcp.gateway.song_time import (
    BEATS_PER_BAR,
    MAX_NOTES_PER_CLIP,
    NoteError,
    bar_start_beat,
    validate_note,
)
from kihachi_mcp.gateway.sound import register_sound_tools
from kihachi_mcp.gateway.transport import (
    ableton_get_playback_state,
    ableton_get_song_time,
    ableton_jump_song_time,
    ableton_set_song_time,
    ableton_set_start_time,
)

_READ_TIMEOUT = 10.0
_WRITE_TIMEOUT = 20.0


def ableton_play(confirm: bool = False) -> dict[str, Any]:
    """Start Live transport. confirm=true is required, and tests should not loop this."""
    return _transport("play", confirm)


def ableton_stop(confirm: bool = False) -> dict[str, Any]:
    """Stop Live transport. confirm=true is required."""
    return _transport("stop", confirm)


def ableton_list_tracks() -> dict[str, Any]:
    """Read track names and mixer flags. Does not change the Set."""
    state, error = invoke("get_state", timeout=_READ_TIMEOUT)
    if error or state is None:
        return {"ok": False, "error": error or "ableton_error", "tracks": []}
    tracks = state.get("tracks")
    if not isinstance(tracks, list):
        return {"ok": False, "error": "ableton_error", "tracks": []}
    return {"ok": True, "track_count": len(tracks), "tracks": tracks, "error": None}


def ableton_create_midi_track(name: str, confirm: bool = False) -> dict[str, Any]:
    """Append a MIDI track. An existing exact name is left as it is."""
    return _create_track(name, "midi", confirm)


def ableton_create_audio_track(name: str, confirm: bool = False) -> dict[str, Any]:
    """Append an audio track. An existing exact name is left as it is."""
    return _create_track(name, "audio", confirm)


def ableton_create_midi_clip(
    length_bars: int,
    name: str,
    track_index: int | None = None,
    track_name: str | None = None,
    clip_index: int = 0,
    notes: list[dict[str, Any]] | None = None,
    arrangement_start_bar: int | None = None,
    confirm: bool = False,
) -> dict[str, Any]:
    """Create one Session MIDI clip. Note times are beats from the clip start.

    ``length_bars`` uses the 4/4 bar of 4 beats. When ``arrangement_start_bar``
    is set, the clip is also copied to the Arrangement at that bar.
    """
    if not confirm:
        return _need_confirm("create_midi_clip", name=name, length_bars=length_bars)
    if _track_name(name):
        return _rejected("クリップ名が不正です")
    if isinstance(clip_index, bool) or not isinstance(clip_index, int) or clip_index < 0:
        return _rejected("clip_index が不正です")
    try:
        length_beats, live_notes, destination = _clip_request(
            length_bars, notes, arrangement_start_bar
        )
    except (NoteError, ValueError) as exc:
        return _rejected(str(exc))
    resolved, error = _track_index(track_index, track_name)
    if error or resolved is None:
        return _failed(error or "rejected")
    created, create_error = invoke(
        "create_midi_clip",
        timeout=45.0,
        track_index=resolved,
        clip_index=clip_index,
        length_beats=length_beats,
        name=name,
        notes=live_notes,
    )
    if create_error or created is None:
        return _failed(create_error or "unconfirmed")
    if live_notes and created.get("note_count") != len(live_notes):
        return _failed("unconfirmed")
    body: dict[str, Any] = {
        "ok": True,
        "status": "SUCCESS",
        "live_changed": True,
        "track_index": resolved,
        "clip_index": clip_index,
        "name": created.get("clip"),
        "length_beats": length_beats,
        "note_count": created.get("note_count"),
    }
    if destination is None:
        return body
    copied, copy_error = invoke(
        "copy_session_clip_to_arrangement",
        timeout=45.0,
        track_index=resolved,
        clip_index=clip_index,
        destination_time_beats=destination,
        name=name,
    )
    if copy_error or copied is None:
        body.update(
            {
                "ok": False,
                "status": "FAILED",
                "partial": True,
                "arrangement": "FAILED",
                "error": copy_error,
            }
        )
        return body
    body["arrangement"] = {
        "status": "SUCCESS",
        "start_time": copied.get("start_time"),
        "end_time": copied.get("end_time"),
    }
    return body


def ableton_add_notes(
    notes: list[dict[str, Any]],
    track_index: int | None = None,
    track_name: str | None = None,
    clip_index: int = 0,
    confirm: bool = False,
) -> dict[str, Any]:
    """Add a batch of notes to one MIDI clip. Times are beats.

    AbletonGPT replaces the clip contents, so this sends the existing notes
    plus the new ones and requires the count read a moment earlier. It does
    not call Live again if that count changed.
    """
    if not confirm:
        return _need_confirm("add_notes", requested=len(notes) if isinstance(notes, list) else None)
    if not isinstance(notes, list) or not notes:
        return _rejected("notes は 1 件以上の配列です")
    if len(notes) > MAX_NOTES_PER_CLIP:
        return _rejected("notes は 4096 件までです")
    try:
        incoming = [validate_note(note) for note in notes]
    except NoteError as exc:
        return _rejected(str(exc))
    resolved, error = _track_index(track_index, track_name)
    if error or resolved is None:
        return _failed(error or "rejected")
    current, read_error = invoke(
        "get_midi_clip_notes",
        timeout=_READ_TIMEOUT,
        track_index=resolved,
        clip_index=clip_index,
    )
    if read_error or current is None:
        return _failed(read_error or "ableton_error")
    if current.get("truncated"):
        return _rejected("クリップのノートを全部読めないので追加しません")
    existing = current.get("notes")
    length = current.get("length_beats")
    source_count = current.get("note_count")
    if not isinstance(existing, list) or not isinstance(length, (int, float)) or isinstance(length, bool):
        return _failed("ableton_error")
    if not isinstance(source_count, int) or isinstance(source_count, bool) or source_count != len(existing):
        return _rejected("ノート数を確認できないので追加しません")
    notes_fingerprint = current.get("notes_fingerprint")
    if (
        not isinstance(notes_fingerprint, str)
        or len(notes_fingerprint) != 67
        or not notes_fingerprint.startswith("v1:")
        or any(char not in "0123456789abcdef" for char in notes_fingerprint[3:])
    ):
        return _failed("AbletonGPT が有効なノート指紋を返さないので追加しません")
    if any(note["start"] >= float(length) for note in incoming):
        return _rejected("start がクリップの長さを超えています")
    if len(existing) + len(incoming) > MAX_NOTES_PER_CLIP:
        return _rejected("追加後のノートが 4096 件を超えます")
    try:
        merged = [_remote_note(note) for note in existing]
    except (KeyError, TypeError, ValueError):
        return _rejected("既存ノートを書き戻せません")
    merged.extend(
        {
            "pitch": note["pitch"],
            "start_time": note["start"],
            "duration": note["duration"],
            "velocity": note["velocity"],
            "probability": note["probability"],
            "velocity_deviation": 0.0,
            "release_velocity": 64.0,
            "mute": False,
        }
        for note in incoming
    )
    applied, apply_error = invoke(
        "apply_expression_to_clip",
        timeout=45.0,
        track_index=resolved,
        clip_index=clip_index,
        notes=merged,
        allow_note_count_change=True,
        expected_source_note_count=source_count,
        expected_source_notes_fingerprint=notes_fingerprint,
    )
    if apply_error or applied is None:
        return _failed(apply_error or "unconfirmed")
    if applied.get("note_count") != len(merged):
        return _failed("unconfirmed")
    return {
        "ok": True,
        "status": "SUCCESS",
        "live_changed": True,
        "track_index": resolved,
        "clip_index": clip_index,
        "source_note_count": source_count,
        "note_count": applied.get("note_count"),
    }


def ableton_set_volume(volume: float, track_index: int | None = None, track_name: str | None = None, confirm: bool = False) -> dict[str, Any]:
    """Set Live's mixer volume. The parameter is 0.0–1.0, not decibels."""
    if not confirm:
        return _need_confirm("set_volume", volume=volume)
    if isinstance(volume, bool) or not isinstance(volume, (int, float)) or not 0.0 <= float(volume) <= 1.0:
        return _rejected("volume は 0.0 から 1.0 です")
    return _mixer_call("set_track_volume", {"volume": float(volume)}, "volume", float(volume), track_index, track_name)


def ableton_set_pan(pan: float, track_index: int | None = None, track_name: str | None = None, confirm: bool = False) -> dict[str, Any]:
    """Set Live's pan. The parameter is -1.0 (left) to 1.0 (right)."""
    if not confirm:
        return _need_confirm("set_pan", pan=pan)
    if isinstance(pan, bool) or not isinstance(pan, (int, float)) or not -1.0 <= float(pan) <= 1.0:
        return _rejected("pan は -1.0 から 1.0 です")
    return _mixer_call("set_track_pan", {"pan": float(pan)}, "pan", float(pan), track_index, track_name)


def ableton_mute(muted: bool, track_index: int | None = None, track_name: str | None = None, confirm: bool = False) -> dict[str, Any]:
    """Mute or unmute one track."""
    if not confirm:
        return _need_confirm("mute", muted=muted)
    if not isinstance(muted, bool):
        return _rejected("muted は true か false です")
    return _mixer_call("set_track_mute", {"muted": muted}, "muted", muted, track_index, track_name)


def ableton_solo(soloed: bool, track_index: int | None = None, track_name: str | None = None, confirm: bool = False) -> dict[str, Any]:
    """Solo or unsolo one track."""
    if not confirm:
        return _need_confirm("solo", soloed=soloed)
    if not isinstance(soloed, bool):
        return _rejected("soloed は true か false です")
    return _mixer_call("set_track_solo", {"soloed": soloed}, "soloed", soloed, track_index, track_name)


def ableton_list_devices(track_index: int | None = None, track_name: str | None = None) -> dict[str, Any]:
    """Read devices and parameter values on one track. Does not load anything."""
    resolved, error = _track_index(track_index, track_name)
    if error or resolved is None:
        return {"ok": False, "error": error or "rejected", "devices": []}
    payload, read_error = invoke("get_track_devices", timeout=_READ_TIMEOUT, track_index=resolved)
    if read_error or payload is None:
        return {"ok": False, "error": read_error or "ableton_error", "devices": []}
    devices = []
    for device in payload.get("devices") or []:
        if not isinstance(device, dict):
            continue
        parameters = []
        for parameter in device.get("parameters") or []:
            if isinstance(parameter, dict):
                parameters.append(
                    {
                        "index": parameter.get("index"),
                        "name": parameter.get("name"),
                        "min": parameter.get("min"),
                        "max": parameter.get("max"),
                        "value": parameter.get("value"),
                    }
                )
        devices.append(
            {
                "index": device.get("index"),
                "name": device.get("name"),
                "class_name": device.get("class_name"),
                "parameters": parameters,
            }
        )
    return {"ok": True, "track_index": resolved, "devices": devices, "error": None}


def ableton_get_arrangement(track_index: int | None = None, track_name: str | None = None) -> dict[str, Any]:
    """Read Arrangement clips on one track. Times are beats."""
    resolved, error = _track_index(track_index, track_name)
    if error or resolved is None:
        return {"ok": False, "error": error or "rejected", "clips": []}
    payload, read_error = invoke("get_arrangement_clips", timeout=_READ_TIMEOUT, track_index=resolved)
    if read_error or payload is None:
        return {"ok": False, "error": read_error or "ableton_error", "clips": []}
    return {"ok": True, "error": None, **payload}


def get_midi_clip_notes(
    track_index: int | None = None,
    track_name: str | None = None,
    clip_index: int = 0,
) -> dict[str, Any]:
    """Read one Session MIDI clip and report whether its full note set was returned."""
    if isinstance(clip_index, bool) or not isinstance(clip_index, int) or clip_index < 0:
        return {**_rejected("clip_index は 0 以上の整数です"), "complete": False}
    resolved, error = _track_index(track_index, track_name)
    if error or resolved is None:
        return {**_failed(error or "rejected"), "complete": False}
    result, read_error = invoke(
        "get_midi_clip_notes",
        timeout=_READ_TIMEOUT,
        track_index=resolved,
        clip_index=clip_index,
    )
    if read_error or result is None:
        return {**_failed(read_error or "ableton_error"), "complete": False}
    raw_track_index = result.get("track_index")
    if (
        raw_track_index is None
        or isinstance(raw_track_index, bool)
        or not isinstance(raw_track_index, int)
        or raw_track_index != resolved
    ):
        return {**_failed("MIDI clip の track_index が要求と一致しません"), "complete": False}
    raw_clip_index = result.get("clip_index")
    if (
        raw_clip_index is None
        or isinstance(raw_clip_index, bool)
        or not isinstance(raw_clip_index, int)
        or raw_clip_index != clip_index
    ):
        return {**_failed("MIDI clip の clip_index が要求と一致しません"), "complete": False}
    raw_track_name = result.get("track") if "track" in result else result.get("track_name")
    if raw_track_name is not None:
        if not isinstance(raw_track_name, str) or not raw_track_name.strip():
            return {**_failed("MIDI clip のトラック識別情報が不正です"), "complete": False}
        if track_name is not None and raw_track_name != track_name:
            return {**_failed("MIDI clip のトラック名が要求と一致しません"), "complete": False}
    if "clip" not in result or not isinstance(result.get("clip"), str):
        return {**_failed("MIDI clip の識別情報が欠落しています"), "complete": False}
    notes = result.get("notes")
    note_count = result.get("note_count")
    length_beats = result.get("length_beats")
    truncated = result.get("truncated")
    if (
        not isinstance(notes, list)
        or len(notes) > MAX_NOTES_PER_CLIP
        or isinstance(note_count, bool)
        or not isinstance(note_count, int)
        or note_count < 0
        or isinstance(length_beats, bool)
        or not isinstance(length_beats, (int, float))
        or not math.isfinite(length_beats)
        or length_beats <= 0
        or not isinstance(truncated, bool)
        or note_count < len(notes)
        or truncated != (note_count > len(notes))
        or (note_count > MAX_NOTES_PER_CLIP and not truncated)
    ):
        return {**_failed("MIDI clip の読み取り結果が不完全です"), "complete": False}
    clip_length = float(length_beats)
    for note in notes:
        if not isinstance(note, dict):
            return {**_failed("MIDI clip に不正なノートがあります"), "complete": False}
        pitch = note.get("pitch")
        start = note.get("start_time")
        duration = note.get("duration")
        velocity = note.get("velocity")
        prob = note.get("probability")
        if (
            isinstance(pitch, bool)
            or not isinstance(pitch, int)
            or not 0 <= pitch <= 127
            or isinstance(start, bool)
            or not isinstance(start, (int, float))
            or not math.isfinite(start)
            or start < 0
            or start >= clip_length
            or isinstance(duration, bool)
            or not isinstance(duration, (int, float))
            or not math.isfinite(duration)
            or duration <= 0
            or isinstance(velocity, bool)
            or not isinstance(velocity, int)
            or not 1 <= velocity <= 127
            or (
                prob is not None
                and (
                    isinstance(prob, bool)
                    or not isinstance(prob, (int, float))
                    or not math.isfinite(prob)
                    or not 0.0 <= float(prob) <= 1.0
                )
            )
        ):
            return {**_failed("MIDI clip に不正なノートがあります"), "complete": False}
    complete = not truncated and note_count == len(notes)
    body: dict[str, Any] = {
        **result,
        "track_index": resolved,
        "clip_index": clip_index,
        "ok": complete,
        "status": "SUCCESS" if complete else "PARTIAL",
        "complete": complete,
        "live_changed": False,
    }
    if track_name is not None and "track" not in body:
        body["track"] = track_name
    return body


def produce_ableton_song(
    tempo: float = 110,
    key: str = "D# minor",
    bars: int = 32,
    style: str = "mutation-funk-dub-tech-house",
    title: str = "KIHACHI MANUS TEST 01",
    swing: float = 0.54,
    seed: int = 1,
    confirm: bool = False,
    dry_run: bool = False,
    sample_selection: str = "assisted",
) -> dict[str, Any]:
    """Plan or build the 32/64/128-bar test song on the local Arrangement.

    dry_run=true returns SongSpec, patterns, tracks, clips, and note counts
    and does not change Live. confirm=true is required for the one write.
    A repeated call with the same title does not create another KICK track
    and does not overwrite clips that already match.
    """
    try:
        plan = build_song_plan(
            tempo=tempo,
            key=key,
            bars=bars,
            style=style,
            title=title,
            swing=swing,
            seed=seed,
        )
    except (ValueError, TypeError) as exc:
        return {
            "ok": False,
            "status": "rejected",
            "live_changed": False,
            "partial": False,
            "error": "rejected",
            "message": str(exc),
        }
    if dry_run or not confirm:
        view = attach_sample_plan(public_view(plan), plan, sample_selection)
        preview = dry_run
        view.update(
            {
                "ok": preview,
                "status": "dry_run" if preview else "confirmation_required",
                "risk": RISK_CONFIRM,
                "live_changed": False,
                "partial": False,
                "operations": [
                    {
                        "op": "produce_ableton_song",
                        "status": "SKIPPED",
                        "detail": "dry_run" if preview else "confirm=true が無いので Live は変更していません",
                    }
                ],
                "verification": None,
                "read_back": None,
                "message": (
                    "制作計画だけを返しました。Live は変更していません"
                    if preview
                    else "Live はまだ変更していません。確認したあと dry_run=false と confirm=true で1回だけ実行してください"
                ),
            }
        )
        return view
    result = execute_song_plan(plan)
    result["sample_plan"] = attach_sample_plan({}, plan, sample_selection)["sample_plan"]
    return result


def register_live_tools(mcp: Any) -> None:
    """Register the song tools once, beside the existing gateway tools."""
    read_only = {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": True}
    write = {
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    }
    for tool in (
        ableton_list_tracks,
        ableton_list_devices,
        ableton_get_arrangement,
        get_midi_clip_notes,
        ableton_get_song_time,
        ableton_get_playback_state,
        list_drum_pads,
        get_arrangement_midi_notes,
    ):
        mcp.tool(tool, annotations=read_only, meta={"kihachiRisk": RISK_SAFE})
    for tool in (
        ableton_play,
        ableton_stop,
        ableton_set_song_time,
        ableton_set_start_time,
        ableton_jump_song_time,
        ableton_create_midi_track,
        ableton_create_audio_track,
        ableton_create_midi_clip,
        ableton_add_notes,
        ableton_set_volume,
        ableton_set_pan,
        ableton_mute,
        ableton_solo,
        produce_ableton_song,
        load_sample_to_simpler,
        load_sample_to_pad,
        add_arrangement_midi_notes,
        replace_arrangement_midi_notes,
        duplicate_arrangement_clip,
    ):
        mcp.tool(tool, annotations=write, meta={"kihachiRisk": RISK_CONFIRM})
    destructive = {
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": False,
        "openWorldHint": True,
    }
    for tool in (
        delete_arrangement_midi_notes,
        delete_arrangement_clip,
        move_arrangement_clip,
    ):
        mcp.tool(tool, annotations=destructive, meta={"kihachiRisk": RISK_DESTRUCTIVE})
    register_sample_tools(mcp)
    register_sound_tools(mcp)


def _transport(action: str, confirm: bool) -> dict[str, Any]:
    if not confirm:
        return _need_confirm(action)
    result, error = invoke("set_transport", timeout=_WRITE_TIMEOUT, action=action)
    if error or result is None:
        return _failed(error or "unconfirmed")
    return {"ok": True, "status": "SUCCESS", "live_changed": True, "action": action, "is_playing": result.get("is_playing")}


def _create_track(name: str, track_type: str, confirm: bool) -> dict[str, Any]:
    if not confirm:
        return _need_confirm("create_track", name=name, track_type=track_type)
    if not isinstance(name, str):
        return _rejected("name は文字列です")
    name = name.strip()
    rejected = _track_name(name)
    if rejected:
        return _rejected(rejected)
    state, error = invoke("get_state", timeout=_READ_TIMEOUT)
    if error or state is None:
        return _failed(error or "ableton_error")
    tracks = [track for track in state.get("tracks") or [] if isinstance(track, dict)]
    matches = [track for track in tracks if track.get("name") == name]
    if len(matches) > 1:
        return _failed("同名トラックが複数あるので増やしません")
    if len(matches) == 1:
        return {
            "ok": True,
            "status": "SKIPPED",
            "live_changed": False,
            "track_index": matches[0].get("index"),
            "name": name,
        }
    created, create_error = invoke(
        "create_track",
        timeout=_WRITE_TIMEOUT,
        track_type=track_type,
        index=-1,
        name=name,
    )
    if create_error or created is None or created.get("name") != name:
        return _failed(create_error or "unconfirmed")
    return {
        "ok": True,
        "status": "SUCCESS",
        "live_changed": True,
        "track_index": created.get("index"),
        "name": created.get("name"),
        "track_type": track_type,
    }


def _clip_request(
    length_bars: object,
    notes: list[dict[str, Any]] | None,
    arrangement_start_bar: int | None,
) -> tuple[float, list[dict[str, Any]], float | None]:
    if isinstance(length_bars, bool) or not isinstance(length_bars, int) or not 1 <= length_bars <= 128:
        raise ValueError("length_bars は 1 から 128 の整数です")
    length_beats = float(length_bars * BEATS_PER_BAR)
    live_notes: list[dict[str, Any]] = []
    if notes is not None:
        if len(notes) > MAX_NOTES_PER_CLIP:
            raise ValueError("notes は 4096 件までです")
        for note in notes:
            checked = validate_note(note, length_beats)
            live_notes.append(
                {
                    "pitch": checked["pitch"],
                    "start_time": checked["start"],
                    "duration": checked["duration"],
                    "velocity": checked["velocity"],
                }
            )
    destination = None
    if arrangement_start_bar is not None:
        destination = bar_start_beat(arrangement_start_bar)
    return length_beats, live_notes, destination


def _track_index(track_index: int | None, track_name: str | None) -> tuple[int | None, str | None]:
    if track_index is not None and track_name is not None:
        return None, "track_index と track_name は片方だけ指定してください"
    if track_index is None and track_name is None:
        return None, "track_index か track_name が必要です"
    if track_name is None:
        if isinstance(track_index, bool) or not isinstance(track_index, int) or track_index < 0:
            return None, "track_index が不正です"
        return track_index, None
    if not isinstance(track_name, str) or not track_name.strip():
        return None, "track_name が不正です"
    state, error = invoke("get_state", timeout=_READ_TIMEOUT)
    if error or state is None:
        return None, error or "ableton_error"
    matches = [
        track
        for track in state.get("tracks") or []
        if isinstance(track, dict) and track.get("name") == track_name
    ]
    if len(matches) != 1 or not isinstance(matches[0].get("index"), int):
        return None, "トラック名が一意に見つかりません"
    return int(matches[0]["index"]), None


def _mixer_call(
    command: str,
    params: dict[str, Any],
    field: str,
    expected: object,
    track_index: int | None,
    track_name: str | None,
) -> dict[str, Any]:
    resolved, error = _track_index(track_index, track_name)
    if error or resolved is None:
        return _failed(error or "rejected")
    result, call_error = invoke(command, timeout=_WRITE_TIMEOUT, track_index=resolved, **params)
    if call_error or result is None:
        return _failed(call_error or "unconfirmed")
    if result.get(field) != expected and not _near(result.get(field), expected):
        return _failed("unconfirmed")
    return {"ok": True, "status": "SUCCESS", "live_changed": True, "track_index": resolved, field: result.get(field)}


def _remote_note(note: dict[str, Any]) -> dict[str, Any]:
    return {
        "pitch": int(note["pitch"]),
        "start_time": float(note["start_time"]),
        "duration": float(note["duration"]),
        "velocity": round(float(note["velocity"])),
        "probability": float(note.get("probability", 1.0)),
        "velocity_deviation": float(note.get("velocity_deviation", 0.0)),
        "release_velocity": float(note.get("release_velocity", 64.0)),
        "mute": bool(note.get("mute", False)),
    }


def _near(actual: object, expected: object) -> bool:
    if isinstance(actual, bool) or isinstance(expected, bool):
        return False
    if not isinstance(actual, (int, float)) or not isinstance(expected, (int, float)):
        return False
    return abs(float(actual) - float(expected)) < 0.001


def _track_name(name: object) -> str | None:
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 80:
        return "name は 1 から 80 文字です"
    if "/" in name or "\\" in name:
        return "name にパス区切りは使えません"
    return None


def _need_confirm(command: str, **echo: object) -> dict[str, Any]:
    return {
        "ok": False,
        "status": "confirmation_required",
        "risk": RISK_CONFIRM,
        "command": command,
        "live_changed": False,
        "message": "confirm=true が無いので Live は変更していません",
        **echo,
    }


def _rejected(message: str) -> dict[str, Any]:
    return {"ok": False, "status": "rejected", "live_changed": False, "error": "rejected", "message": message}


def _failed(detail: str) -> dict[str, Any]:
    return {"ok": False, "status": "FAILED", "live_changed": False, "error": detail}
