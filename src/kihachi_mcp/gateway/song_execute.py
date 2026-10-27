"""Write one planned song onto Live. Reads back before it reports success.

There is no delete and no second attempt. A failed step leaves what Live
already accepted, and later steps are marked skipped.
"""

from __future__ import annotations

import logging
from typing import Any

from kihachi_mcp.gateway.calls import invoke
from kihachi_mcp.gateway.song_patterns import ROLES
from kihachi_mcp.gateway.song_plan import PAN, VOLUME, public_view, track_name
from kihachi_mcp.gateway.song_time import live_note

_READ_TIMEOUT = 10.0
_WRITE_TIMEOUT = 20.0
_CLIP_TIMEOUT = 45.0
logger = logging.getLogger("kihachi.mcp.gateway")


def execute_song_plan(plan: dict[str, Any]) -> dict[str, Any]:
    """Append the planned tracks, clips, notes, and arrangement."""
    try:
        return _execute_song_plan(plan)
    except (AttributeError, IndexError, KeyError, RuntimeError, TypeError, ValueError):
        logger.info("song_execute", extra={"event": "song_execute", "outcome": "unexpected"})
        body = public_view(plan)
        body.update(
            {
                "ok": False,
                "status": "failed",
                "live_changed": False,
                "partial": True,
                "operations": [{"op": "execute", "status": "FAILED", "detail": "unexpected"}],
                "verification": None,
                "read_back": None,
                "owned_tracks": {},
                "message": "途中で中断しました。作られたトラックは削除していません",
            }
        )
        return body


def _execute_song_plan(plan: dict[str, Any]) -> dict[str, Any]:
    song = _dict(plan.get("song"))
    title = str(song.get("title") or "")
    tempo = int(song["tempo"])
    length = float(plan["length_beats"])
    notes_by_role = _dict(plan.get("notes_by_role"))
    operations: list[dict[str, Any]] = []
    owned: dict[str, int] = {}
    stop = False

    def record(op: str, status: str, **detail: object) -> None:
        operations.append({"op": op, "status": status, **detail})

    def fail(op: str, detail: str, **extra: object) -> None:
        nonlocal stop
        record(op, "FAILED", detail=detail, **extra)
        stop = True

    state, error = invoke("get_state", timeout=_READ_TIMEOUT)
    if error or state is None:
        record("read_state", "FAILED", detail=error or "ableton_error")
        return _finish(plan, operations, owned, verified=None, read_back=None, blocked=False)

    playing = bool(state.get("is_playing"))
    signature = state.get("signature")
    scene_count = state.get("scene_count")
    if playing:
        record("preflight", "FAILED", detail="Live が再生中です。stop は呼びません")
        return _finish(plan, operations, owned, verified=None, read_back=None, blocked=True)
    if signature != [4, 4]:
        record("preflight", "FAILED", detail="拍子が 4/4 ではないので書き込みません")
        return _finish(plan, operations, owned, verified=None, read_back=None, blocked=True)
    if isinstance(scene_count, bool) or not isinstance(scene_count, int) or scene_count < 1:
        record("preflight", "FAILED", detail="Session のシーンが無いのでクリップを置けません")
        return _finish(plan, operations, owned, verified=None, read_back=None, blocked=True)

    tracks = _tracks(state)
    current_tempo = state.get("tempo")
    if not isinstance(current_tempo, (int, float)) or isinstance(current_tempo, bool):
        fail("set_tempo", "テンポを読めません")
    elif abs(float(current_tempo) - tempo) < 0.05:
        record("set_tempo", "SKIPPED", detail="すでに目的のテンポです")
    else:
        updated, tempo_error = invoke("set_tempo", timeout=_WRITE_TIMEOUT, bpm=float(tempo))
        observed = updated.get("tempo") if updated else None
        if tempo_error or not isinstance(observed, (int, float)) or abs(float(observed) - tempo) >= 0.05:
            fail("set_tempo", tempo_error or "unconfirmed")
        else:
            record("set_tempo", "SUCCESS", detail=float(observed))

    for role in ROLES:
        if stop:
            record("create_track", "SKIPPED", role=role, detail="前の操作が失敗したため止めた")
            continue
        name = track_name(title, role)
        matches = [track for track in tracks if track.get("name") == name]
        if len(matches) > 1:
            fail("create_track", "同名トラックが複数あるので増やしません", role=role)
            continue
        if len(matches) == 1:
            found = _index(matches[0])
            if found is None:
                fail("create_track", "トラック番号を読めません", role=role)
                continue
            owned[role] = found
            record("create_track", "SKIPPED", role=role, detail="すでにあります")
            continue
        created, create_error = invoke(
            "create_track",
            timeout=_WRITE_TIMEOUT,
            track_type="midi",
            index=-1,
            name=name,
        )
        created_index = _index(created) if created else None
        if create_error or created is None or created.get("name") != name or created_index is None:
            fail("create_track", create_error or "unconfirmed", role=role)
            continue
        owned[role] = created_index
        tracks.append({"index": owned[role], "name": name, "volume": None})
        record("create_track", "SUCCESS", role=role, detail=name)

    for role in ROLES:
        if stop:
            record("create_clip", "SKIPPED", role=role, detail="前の操作が失敗したため止めた")
            continue
        if role not in owned:
            record("create_clip", "SKIPPED", role=role, detail="トラックが無い")
            continue
        placed, clip_error = _ensure_clip(
            owned[role],
            track_name(title, role),
            length,
            _role_notes(notes_by_role, role),
        )
        if clip_error:
            fail("create_clip", clip_error, role=role)
        else:
            record("create_clip", placed or "SUCCESS", role=role, detail=track_name(title, role))

    for role in ROLES:
        if stop:
            record("arrangement", "SKIPPED", role=role, detail="前の操作が失敗したため止めた")
            continue
        if role not in owned:
            record("arrangement", "SKIPPED", role=role, detail="トラックが無い")
            continue
        placed, arrangement_error = _ensure_arrangement(owned[role], track_name(title, role), length)
        if arrangement_error:
            fail("arrangement", arrangement_error, role=role)
        else:
            record("arrangement", placed or "SUCCESS", role=role, detail="beat 0")

    if stop:
        for role in ROLES:
            record("mixer", "SKIPPED", role=role, detail="前の操作が失敗したため止めた")
    else:
        staged, stage_error = invoke("get_state", timeout=_READ_TIMEOUT)
        if stage_error or staged is None:
            fail("mixer", stage_error or "ableton_error")
        staged_tracks = _tracks(staged) if staged else []
        for role in ROLES:
            if stop:
                record("mixer", "SKIPPED", role=role, detail="前の操作が失敗したため止めた")
                continue
            current = next((track for track in staged_tracks if track.get("name") == track_name(title, role)), None)
            volume_now = current.get("volume") if current else None
            if isinstance(volume_now, (int, float)) and abs(float(volume_now) - VOLUME[role]) < 0.02:
                record("mixer", "SKIPPED", role=role, detail="音量はすでに目的の値です")
                continue
            mixed, mix_error = _stage_mixer(owned[role], role)
            if mix_error:
                fail("mixer", mix_error, role=role)
            else:
                record("mixer", "SUCCESS", role=role, detail=mixed)

    verified, read_back = _verify(title, tempo, length, notes_by_role, owned)
    if verified is None:
        record("verify", "FAILED", detail=read_back or "read-back に失敗しました")
        verified = {
            "tempo": False,
            "tracks": False,
            "clips": False,
            "notes": False,
            "arrangement": False,
            "mixer_volume": False,
        }
        read_back = None
    elif all(verified.values()):
        record("verify", "SUCCESS", detail="read-back が計画と一致しました")
    else:
        record("verify", "FAILED", detail="read-back が計画と一致しません")
    return _finish(plan, operations, owned, verified=verified, read_back=read_back, blocked=False)


def _ensure_clip(
    track_index: int,
    name: str,
    length: float,
    notes: list[dict[str, Any]],
) -> tuple[str | None, str | None]:
    existing, error = invoke(
        "get_midi_clip_notes",
        timeout=_READ_TIMEOUT,
        track_index=track_index,
        clip_index=0,
    )
    expected = _checksum(notes, "start")
    if existing and not error:
        if _clip_matches(existing, name, notes, expected):
            return "SKIPPED", None
        return None, "既存クリップと一致しないので上書きしません"
    if error and "does not contain" not in error:
        return None, error
    created, create_error = invoke(
        "create_midi_clip",
        timeout=_CLIP_TIMEOUT,
        track_index=track_index,
        clip_index=0,
        length_beats=length,
        name=name,
        notes=[live_note(note) for note in notes],
    )
    if create_error or created is None:
        return None, create_error or "unconfirmed"
    if created.get("note_count") != len(notes) or created.get("clip") != name:
        return None, "unconfirmed"
    return "SUCCESS", None


def _ensure_arrangement(track_index: int, name: str, length: float) -> tuple[str | None, str | None]:
    current, error = invoke(
        "get_arrangement_clips",
        timeout=_READ_TIMEOUT,
        track_index=track_index,
    )
    if error or current is None:
        return None, error or "ableton_error"
    clips = current.get("clips")
    if not isinstance(clips, list):
        return None, "ableton_error"
    exact = [clip for clip in clips if _spans_song(clip, length)]
    if exact:
        if current.get("clip_count") == 1:
            return "SKIPPED", None
        return None, "Arrangement に想定外のクリップがあるので置きません"
    if any(_overlaps_song(clip, length) for clip in clips):
        return None, "既存の Arrangement と重なるので置きません"
    copied, copy_error = invoke(
        "copy_session_clip_to_arrangement",
        timeout=_CLIP_TIMEOUT,
        track_index=track_index,
        clip_index=0,
        destination_time_beats=0.0,
        name=name,
    )
    if copy_error or copied is None:
        return None, copy_error or "unconfirmed"
    if not _spans_song(copied, length) or not copied.get("is_midi_clip"):
        return None, "unconfirmed"
    return "SUCCESS", None


def _stage_mixer(track_index: int, role: str) -> tuple[str | None, str | None]:
    panned, pan_error = invoke(
        "set_track_pan",
        timeout=_WRITE_TIMEOUT,
        track_index=track_index,
        pan=PAN[role],
    )
    if pan_error or panned is None:
        return None, pan_error or "unconfirmed"
    observed_pan = panned.get("pan")
    if not isinstance(observed_pan, (int, float)) or abs(float(observed_pan) - PAN[role]) > 0.001:
        return None, "unconfirmed"
    leveled, volume_error = invoke(
        "set_track_volume",
        timeout=_WRITE_TIMEOUT,
        track_index=track_index,
        volume=VOLUME[role],
    )
    if volume_error or leveled is None:
        return None, volume_error or "unconfirmed"
    observed = leveled.get("volume")
    if not isinstance(observed, (int, float)) or abs(float(observed) - VOLUME[role]) > 0.02:
        return None, "unconfirmed"
    return f"volume={float(observed):.2f}", None


def _verify(
    title: str,
    tempo: int,
    length: float,
    notes_by_role: dict[str, Any],
    owned: dict[str, int],
) -> tuple[dict[str, bool] | None, dict[str, Any] | str | None]:
    state, error = invoke("get_state", timeout=_READ_TIMEOUT)
    if error or state is None:
        return None, error or "ableton_error"
    tracks = _tracks(state)
    by_name = {str(track.get("name")): track for track in tracks}
    names = [track_name(title, role) for role in ROLES]
    tempo_value = state.get("tempo")
    tempo_ok = _close_number(tempo_value, float(tempo), 0.05)
    tracks_ok = all(
        name in by_name and _index(by_name[name]) == owned.get(role) for role, name in zip(ROLES, names, strict=True)
    )
    clips_ok = True
    notes_ok = True
    arrangement_ok = True
    mixer_ok = True
    read_notes: dict[str, int] = {}
    read_arrangement: dict[str, float] = {}
    for role, name in zip(ROLES, names, strict=True):
        track = by_name.get(name)
        if track is None or role not in owned:
            clips_ok = False
            notes_ok = False
            arrangement_ok = False
            mixer_ok = False
            continue
        if not _close_number(track.get("volume"), VOLUME[role], 0.02):
            mixer_ok = False
        index = _index(track)
        if index is None:
            clips_ok = False
            notes_ok = False
            arrangement_ok = False
            continue
        clip, clip_error = invoke(
            "get_midi_clip_notes",
            timeout=_READ_TIMEOUT,
            track_index=index,
            clip_index=0,
        )
        expected_notes = _role_notes(notes_by_role, role)
        if clip_error or clip is None or not _clip_matches(clip, name, expected_notes, _checksum(expected_notes, "start")):
            clips_ok = False
            notes_ok = False
        else:
            read_notes[role] = int(clip["note_count"])
        arrangement, arrangement_error = invoke(
            "get_arrangement_clips",
            timeout=_READ_TIMEOUT,
            track_index=index,
        )
        clips = arrangement.get("clips") if arrangement else None
        if (
            arrangement_error
            or not isinstance(clips, list)
            or arrangement.get("clip_count") != 1
            or not any(_spans_song(item, length) for item in clips)
        ):
            arrangement_ok = False
        elif clips:
            read_arrangement[role] = float(clips[0]["end_time"]) - float(clips[0]["start_time"])
    verification = {
        "tempo": tempo_ok,
        "tracks": tracks_ok and len(owned) == len(ROLES),
        "clips": clips_ok and len(read_notes) == len(ROLES),
        "notes": notes_ok and len(read_notes) == len(ROLES),
        "arrangement": arrangement_ok and len(read_arrangement) == len(ROLES),
        "mixer_volume": mixer_ok and len(owned) == len(ROLES),
    }
    read_back = {
        "tempo": state.get("tempo"),
        "track_count": len(tracks),
        "track_names": [track.get("name") for track in tracks],
        "note_counts": read_notes,
        "arrangement_beats": read_arrangement,
    }
    return verification, read_back


def _clip_matches(payload: dict[str, Any], name: str, notes: list[dict[str, Any]], expected: int) -> bool:
    if payload.get("truncated") or payload.get("clip") != name:
        return False
    if payload.get("note_count") != len(notes):
        return False
    source = payload.get("notes")
    if not isinstance(source, list) or len(source) != len(notes):
        return False
    return _checksum(source, "start_time") == expected


def _checksum(notes: list[dict[str, Any]], start_key: str) -> int:
    total = 0
    for note in notes:
        start = float(note[start_key])
        total = (
            total
            + int(note["pitch"]) * 131
            + round(start * 1000.0)
            + round(float(note["velocity"]))
        ) % 1_000_003
    return total


def _spans_song(clip: object, length: float) -> bool:
    if not isinstance(clip, dict):
        return False
    start = clip.get("start_time")
    end = clip.get("end_time")
    if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
        return False
    return abs(float(start)) < 0.001 and abs((float(end) - float(start)) - length) < 0.05


def _overlaps_song(clip: object, length: float) -> bool:
    if not isinstance(clip, dict):
        return False
    start = clip.get("start_time")
    end = clip.get("end_time")
    if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
        return False
    return float(start) < length - 1e-6 and float(end) > 1e-6


def _finish(
    plan: dict[str, Any],
    operations: list[dict[str, Any]],
    owned: dict[str, int],
    *,
    verified: dict[str, bool] | None,
    read_back: dict[str, Any] | None,
    blocked: bool,
) -> dict[str, Any]:
    body = public_view(plan)
    live_changed = any(op["status"] == "SUCCESS" and op["op"] != "verify" for op in operations)
    failed = any(op["status"] == "FAILED" for op in operations)
    ok = verified is not None and not failed and all(verified.values())
    if blocked:
        status = "blocked"
    elif ok and live_changed:
        status = "applied"
    elif ok:
        status = "already_applied"
    else:
        status = "failed"
    body.update(
        {
            "ok": ok,
            "status": status,
            "live_changed": live_changed,
            "partial": (not ok) and live_changed,
            "operations": operations,
            "verification": verified,
            "read_back": read_back,
            "owned_tracks": owned,
            "message": _message(status),
        }
    )
    return body


def _message(status: str) -> str:
    if status == "applied":
        return "Arrangement まで書き、Live から読み戻して一致しました"
    if status == "already_applied":
        return "同じ曲はすでに Live 上にあります。追加も上書きもしていません"
    if status == "blocked":
        return "安全条件を満たさないので Live は変更していません"
    return "一部でも失敗した操作があります。成功したとは扱いません"


def _tracks(state: dict[str, Any]) -> list[dict[str, Any]]:
    tracks = state.get("tracks")
    if not isinstance(tracks, list):
        return []
    return [track for track in tracks if isinstance(track, dict)]


def _role_notes(notes_by_role: dict[str, Any], role: str) -> list[dict[str, Any]]:
    notes = notes_by_role.get(role)
    if not isinstance(notes, list):
        return []
    return [note for note in notes if isinstance(note, dict)]


def _close_number(value: object, target: float, tolerance: float) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return abs(float(value) - target) < tolerance


def _index(track: dict[str, Any] | None) -> int | None:
    if not track:
        return None
    index = track.get("index")
    if isinstance(index, bool) or not isinstance(index, int):
        return None
    return index


def _dict(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    return {}
