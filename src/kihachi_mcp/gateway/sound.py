"""Sound assignment for a song plan, and one confirm-gated instrument probe.

Dry-run planning does not call Live. A device name appears only after this
machine has read that device back from a track. Splice files are not placed.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from kihachi_mcp.gateway.calls import invoke
from kihachi_mcp.gateway.catalog import RISK_CONFIRM, RISK_SAFE
from kihachi_mcp.gateway.song_patterns import ROLES

# Names the Remote Script will insert. Presence in this tuple is not proof
# that this Live has the device; browse_presets has to list it.
STOCK_INSTRUMENTS = (
    "Drift",
    "Wavetable",
    "Operator",
    "Analog",
    "Meld",
    "Electric",
    "Tension",
    "Collision",
    "Drum Rack",
    "Impulse",
)
# A synth makes MIDI audible without Drum Rack pads. Drum Rack is excluded
# here because an empty pad stays silent, and pitch 36 is not a pad map.
PROBE_PREFERENCE = (
    "Operator",
    "Analog",
    "Wavetable",
    "Drift",
    "Electric",
    "Tension",
    "Collision",
    "Meld",
    "Impulse",
)
PROBE_TRACK = "KIHACHI DEVICE PROBE"
BASS_PROBE_TRACK = "KIHACHI BASS SOUND PROBE"
OPERATOR_URI = "query:Synths#Operator"
# MIDI note numbers already used by the song generator: sub 27 is D#1, slap 39 is D#2.
BASS_PROBE_PITCHES = (27, 30, 34, 39, 32, 37)
# Mixer is 0.0–1.0 and about 0.85 is unity. This matches the song's SUB BASS level.
BASS_PROBE_VOLUME = 0.70
_MUSICAL_ROLE = {
    "KICK": "kick",
    "DRUMS": "drums",
    "PERCUSSION": "percussion",
    "SLAP BASS": "funky_slap_bass",
    "SUB BASS": "sub_bass",
    "DUB CHORDS": "dub_chord",
    "SYNTH STABS": "synth_stab",
    "ARP": "arp",
    "VOCODER CARRIER": "vocoder_carrier",
    "VOCAL": "vocal",
    "FX": "fx",
}
_READ_TIMEOUT = 10.0
_WRITE_TIMEOUT = 30.0


def sound_plan() -> dict[str, Any]:
    """Role assignments on the production plan. This does not load a device."""
    assignments = [_assignment(role) for role in ROLES]
    return {
        "order": [
            "songspec",
            "tracks",
            "sound_assignment",
            "devices",
            "clips",
            "notes",
            "mixer",
            "arrangement",
            "read_back",
        ],
        "assignments": assignments,
        "splice_seam": {
            "oneshot": "Simpler にサンプルを入れ、MIDI で鳴らす。未実装",
            "loop": "オーディオクリップを Arrangement に置く。未実装",
        },
        "architecture": {
            "oneshot": "Simpler + MIDI。ルート音が分かっているサンプルだけ。Simpler はブラウザにあるが insert 許可名には無い。後のプローブはコマンドを先に確認する",
            "loop": "オーディオクリップ",
            "melodic": "メーターで確認済みの Operator。この計画では載せ直さない",
            "later": {
                "KICK": "sample_instrument。Simpler は insert 許可リストに無いので、プローブ前にコマンドを確認する",
                "DRUMS": "sample pending。Drum Rack は使わない",
                "PERCUSSION": "audio pending",
                "SLAP BASS": "Operator",
                "SUB BASS": "Operator",
                "DUB CHORDS": "読み戻したシンセ",
                "SYNTH STABS": "読み戻したシンセ",
                "ARP": "読み戻したシンセ",
                "VOCODER CARRIER": "読み戻したシンセ",
                "VOCAL": "splice audio",
                "FX": "splice audio",
            },
        },
        "drum_pitch_check": "Drum Rack は使わない。生成ピッチ 36 をパッド対応とは扱わない",
        "simpler_insert": "Simpler は STOCK_INSTRUMENTS に無い。この回ではロードしない",
    }


_METERED_OPERATOR = frozenset(
    {"SLAP BASS", "SUB BASS", "DUB CHORDS", "SYNTH STABS", "ARP", "VOCODER CARRIER"}
)


def _assignment(role: str) -> dict[str, Any]:
    base = {
        "role": role,
        "musical_role": _MUSICAL_ROLE[role],
        "preset": None,
        "browser_uri": None,
        "sample": None,
        "verified": False,
    }
    if role in _METERED_OPERATOR:
        return {
            **base,
            "kind": "INSTRUMENT",
            "source": "instrument",
            "source_type": "instrument",
            "device": "Operator",
            "browser_uri": OPERATOR_URI,
            "verified": True,
            "verification": "meter",
            "reason": "セッション再生のメーターで確認済み。人が聞いた確認はしていない。この回では触らない",
        }
    if role == "KICK":
        return {
            **base,
            "kind": "AUDIO",
            "source": "audio",
            "source_type": "audio",
            "preferred_source_type": "sample_instrument",
            "actual_source_type": "audio",
            "preferred_device": "Simpler",
            "device": None,
            "sample": "pending",
            "verification": "pending",
            "reason": "Simpler へのサンプル代入は Remote Script に無い。曲トラックには適用しない",
        }
    if role == "DRUMS":
        return {
            **base,
            "kind": "SAMPLE",
            "source": "sample",
            "source_type": "sample",
            "device": None,
            "sample": "pending",
            "verification": "pending",
            "reason": "snare / clap / hat のサンプル候補だけ。Drum Rack は使わない。この回では置かない",
        }
    if role in {"PERCUSSION", "VOCAL", "FX"}:
        return {
            **base,
            "kind": "AUDIO",
            "source": "audio",
            "source_type": "audio",
            "device": None,
            "sample": "pending",
            "verification": "pending",
            "reason": "オーディオ候補だけ。この回では置かない",
        }
    return {
        **base,
        "kind": "PLACEHOLDER",
        "source": "placeholder",
        "source_type": "placeholder",
        "device": None,
        "verification": "unverified",
        "reason": "割り当てが未定",
    }


def ableton_search_browser(
    category: str = "instruments",
    path: list[str] | None = None,
    query: str | None = None,
    max_items: int = 50,
) -> dict[str, Any]:
    """List one browser node. This does not load anything."""
    listed, error = _browse(category, path or [], max_items)
    if error or listed is None:
        return {"ok": False, "error": error or "ableton_error", "items": []}
    items = _public_items(listed.get("items"))
    if query:
        items = [item for item in items if item.get("name") == query]
    return {
        "ok": True,
        "category": listed.get("category"),
        "path": listed.get("path"),
        "count": len(items),
        "items": items,
        "ambiguous": bool(query) and len(items) != 1,
    }


def ableton_list_instruments() -> dict[str, Any]:
    """Instruments this Live's browser lists at the instruments root."""
    found = ableton_search_browser(category="instruments", max_items=200)
    if not found.get("ok"):
        return found
    items = [
        item
        for item in found["items"]
        if item.get("name") in STOCK_INSTRUMENTS or item.get("is_device") is True
    ]
    return {"ok": True, "count": len(items), "instruments": items}


def ableton_load_stock_instrument(
    device_name: str,
    track_index: int | None = None,
    confirm: bool = False,
) -> dict[str, Any]:
    """Load one browser-listed stock instrument. A second match is a refusal."""
    if not confirm:
        return {
            "ok": False,
            "status": "confirmation_required",
            "risk": RISK_CONFIRM,
            "live_changed": False,
            "message": "confirm=true が無いのでデバイスは載せていません",
        }
    if device_name not in STOCK_INSTRUMENTS:
        return _failed("許可リストに無いデバイス名です")
    if isinstance(track_index, bool) or not isinstance(track_index, int) or track_index < 0:
        return _failed("track_index が不正です")
    choice, choose_error = _unique_instrument(device_name)
    if choose_error or choice is None:
        return _failed(choose_error or "not_found")
    loaded = _insert_one(track_index, device_name, choice)
    if not loaded.get("ok"):
        return loaded
    read_back, read_error = invoke(
        "get_track_devices",
        timeout=_READ_TIMEOUT,
        track_index=track_index,
    )
    devices = read_back.get("devices") if isinstance(read_back, dict) else None
    names = [device.get("name") for device in devices or [] if isinstance(device, dict)]
    if read_error or device_name not in names:
        return {
            "ok": False,
            "status": "FAILED",
            "live_changed": True,
            "partial": True,
            "error": read_error or "read_back_mismatch",
            "requested": device_name,
            "devices": names,
        }
    _write_verified(device_name, choice.get("uri"))
    return {
        "ok": True,
        "status": "SUCCESS",
        "live_changed": True,
        "track_index": track_index,
        "device": device_name,
        "browser_uri": choice.get("uri"),
        "device_count": len(names),
        "devices": names,
    }


def ableton_device_probe(confirm: bool = False) -> dict[str, Any]:
    """Create KIHACHI DEVICE PROBE, load one stock instrument, and write four notes.

    A failed load stops. Nothing is retried, and existing song tracks are not used.
    """
    if not confirm:
        return {
            "ok": False,
            "status": "confirmation_required",
            "risk": RISK_CONFIRM,
            "live_changed": False,
            "message": "confirm=true が無いのでプローブトラックは作っていません",
        }
    state, error = invoke("get_state", timeout=_READ_TIMEOUT)
    if error or state is None:
        return _failed(error or "ableton_error")
    if state.get("is_playing"):
        return {"ok": False, "status": "blocked", "live_changed": False, "message": "再生中です"}
    tracks = [track for track in state.get("tracks") or [] if isinstance(track, dict)]
    if any(track.get("name") == PROBE_TRACK for track in tracks):
        return _failed("同名のプローブが既にあるので、作り直しも削除もしません")
    created, create_error = invoke(
        "create_track",
        timeout=_WRITE_TIMEOUT,
        track_type="midi",
        index=-1,
        name=PROBE_TRACK,
    )
    if create_error or created is None or created.get("name") != PROBE_TRACK:
        return _failed(create_error or "unconfirmed")
    track_index = created.get("index")
    if isinstance(track_index, bool) or not isinstance(track_index, int):
        return _failed("トラック番号を読めません")
    chosen_name, choice = _first_listed_synth()
    if chosen_name is None or choice is None:
        return {
            "ok": False,
            "status": "FAILED",
            "partial": True,
            "live_changed": True,
            "track_index": track_index,
            "error": "instruments ブラウザに一意の内蔵シンセがありません",
        }
    loaded = _insert_one(track_index, chosen_name, choice)
    if not loaded.get("ok"):
        loaded["partial"] = True
        loaded["live_changed"] = True
        loaded["track_index"] = track_index
        return loaded
    notes = [
        {"pitch": pitch, "start_time": float(index), "duration": 0.5, "velocity": 100}
        for index, pitch in enumerate((63, 66, 70, 75))
    ]
    clip, clip_error = invoke(
        "create_midi_clip",
        timeout=45.0,
        track_index=track_index,
        clip_index=0,
        length_beats=4.0,
        name=PROBE_TRACK,
        notes=notes,
    )
    read_back, read_error = invoke(
        "get_track_devices",
        timeout=_READ_TIMEOUT,
        track_index=track_index,
    )
    heard, heard_error = invoke(
        "get_midi_clip_notes",
        timeout=_READ_TIMEOUT,
        track_index=track_index,
        clip_index=0,
    )
    devices = read_back.get("devices") if isinstance(read_back, dict) else None
    names = [device.get("name") for device in devices or [] if isinstance(device, dict)]
    note_count = heard.get("note_count") if isinstance(heard, dict) else None
    device_ok = chosen_name in names and not read_error
    notes_ok = note_count == 4 and not clip_error and not heard_error and clip is not None
    if device_ok:
        _write_verified(chosen_name, choice.get("uri"))
    return {
        "ok": device_ok and notes_ok,
        "status": "SUCCESS" if device_ok and notes_ok else "FAILED",
        "live_changed": True,
        "track_name": PROBE_TRACK,
        "track_index": track_index,
        "device": chosen_name if device_ok else None,
        "requested": chosen_name,
        "browser_uri": choice.get("uri"),
        "devices": names,
        "device_count": len(names),
        "note_count": note_count,
        "audio_output": "NOT VERIFIED",
    }


def modest_level_parameter(parameters: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Return one Volume parameter only when its range is the mixer's 0–1 scale."""
    matches = []
    for parameter in parameters:
        if parameter.get("name") != "Volume" or parameter.get("is_quantized") is True:
            continue
        if parameter.get("min") == 0 and parameter.get("max") == 1 and isinstance(parameter.get("index"), int):
            matches.append(parameter)
    if len(matches) != 1:
        return None
    return {"parameter_index": matches[0]["index"], "value": BASS_PROBE_VOLUME}


def meter_rose(before: object, after: object) -> bool:
    """Activity is a rise above the reading taken while stopped. No fixed threshold."""
    if isinstance(before, bool) or isinstance(after, bool):
        return False
    if not isinstance(before, (int, float)) or not isinstance(after, (int, float)):
        return False
    return float(after) > float(before) and float(after) > 0


def ensure_stopped() -> dict[str, Any]:
    """Leave transport stopped. One stop call, then one read. No second attempt."""
    state, error = invoke("get_state", timeout=_READ_TIMEOUT)
    if error or state is None:
        return {"ok": False, "stopped": False, "error": error or "ableton_error"}
    if state.get("is_playing") is False:
        return {"ok": True, "stopped": True, "already_stopped": True, "live_changed": False}
    stopped, stop_error = invoke("set_transport", timeout=_WRITE_TIMEOUT, action="stop")
    again, again_error = invoke("get_state", timeout=_READ_TIMEOUT)
    playing = True if again is None else again.get("is_playing") is not False
    return {
        "ok": not playing and not stop_error and not again_error and stopped is not None,
        "stopped": not playing,
        "already_stopped": False,
        "live_changed": True,
        "error": stop_error or again_error,
    }


def read_track_meter(track_index: int) -> dict[str, Any]:
    """Read output_meter_level from get_mix_snapshot. Left/right meters are not in this script."""
    snapshot, error = invoke("get_mix_snapshot", timeout=_READ_TIMEOUT)
    if error or snapshot is None:
        return {"ok": False, "error": error or "ableton_error", "output_meter_level": None}
    tracks = snapshot.get("tracks")
    if not isinstance(tracks, list):
        return {"ok": False, "error": "no_tracks", "output_meter_level": None}
    for track in tracks:
        if isinstance(track, dict) and track.get("index") == track_index:
            return {
                "ok": True,
                "output_meter_level": track.get("output_meter_level"),
                "name": track.get("name"),
                "is_playing_field": "song.is_playing via get_state",
            }
    return {"ok": False, "error": "track_missing", "output_meter_level": None}


def launch_clip_once(track_index: int, clip_index: int = 0) -> dict[str, Any]:
    fired, error = invoke(
        "fire_clip",
        timeout=_WRITE_TIMEOUT,
        track_index=track_index,
        clip_index=clip_index,
    )
    if error or fired is None:
        return {"ok": False, "playback_started": False, "error": error or "unconfirmed"}
    state, state_error = invoke("get_state", timeout=_READ_TIMEOUT)
    playing = bool(state and state.get("is_playing") is True)
    return {
        "ok": playing and not state_error,
        "playback_started": playing,
        "fired": fired.get("fired"),
        "error": state_error,
    }


def audition_clip(track_index: int, clip_index: int = 0) -> dict[str, Any]:
    """One launch and one stop. A failed stop does not launch again."""
    ready = ensure_stopped()
    if not ready.get("stopped"):
        return {
            "ok": False,
            "playback_started": False,
            "meter_activity": False,
            "stopped": False,
            "before": None,
            "after": None,
            "human": "HUMAN_NOT_VERIFIED",
            "error": ready.get("error") or "stop_not_guaranteed",
        }
    before = read_track_meter(track_index)
    launched = launch_clip_once(track_index, clip_index)
    after = read_track_meter(track_index)
    ended = stop_once()
    before_level = before.get("output_meter_level")
    after_level = after.get("output_meter_level")
    return {
        "ok": bool(launched.get("playback_started")) and bool(ended.get("stopped")) and meter_rose(before_level, after_level),
        "playback_started": bool(launched.get("playback_started")),
        "meter_activity": meter_rose(before_level, after_level),
        "stopped": bool(ended.get("stopped")),
        "before": before_level,
        "after": after_level,
        "human": "HUMAN_NOT_VERIFIED",
        "error": launched.get("error") or ended.get("error"),
    }


def stop_once() -> dict[str, Any]:
    """Stop transport and the probe's launching slot is handled by the caller if needed."""
    stopped, error = invoke("set_transport", timeout=_WRITE_TIMEOUT, action="stop")
    state, state_error = invoke("get_state", timeout=_READ_TIMEOUT)
    playing = True if state is None else state.get("is_playing") is not False
    return {
        "ok": not playing and not error and not state_error and stopped is not None,
        "stopped": not playing,
        "error": error or state_error,
    }


def register_sound_tools(mcp: Any) -> None:
    read_only = {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": True}
    write = {
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    }
    for tool in (ableton_list_instruments, ableton_search_browser):
        mcp.tool(tool, annotations=read_only, meta={"kihachiRisk": RISK_SAFE})
    for tool in (ableton_load_stock_instrument, ableton_device_probe):
        mcp.tool(tool, annotations=write, meta={"kihachiRisk": RISK_CONFIRM})


def _browse(category: str, path: list[str], max_items: int) -> tuple[dict[str, Any] | None, str | None]:
    if category not in {
        "instruments",
        "sounds",
        "drums",
        "audio_effects",
        "midi_effects",
        "samples",
        "plugins",
        "max_for_live",
        "packs",
        "user_library",
    }:
        return None, "unknown category"
    return invoke(
        "browse_presets",
        timeout=_READ_TIMEOUT,
        category=category,
        path=path,
        max_items=max_items,
    )


def _public_items(items: object) -> list[dict[str, Any]]:
    if not isinstance(items, list):
        return []
    public = []
    for item in items:
        if not isinstance(item, dict):
            continue
        public.append(
            {
                "name": item.get("name"),
                "uri": item.get("uri"),
                "is_device": item.get("is_device"),
                "is_loadable": item.get("is_loadable"),
                "is_folder": item.get("is_folder"),
            }
        )
    return public


def _unique_instrument(device_name: str) -> tuple[dict[str, Any] | None, str | None]:
    listed, error = _browse("instruments", [], 200)
    if error or listed is None:
        return None, error or "ableton_error"
    matches = [item for item in _public_items(listed.get("items")) if item.get("name") == device_name]
    if len(matches) != 1:
        return None, "同名のブラウザ項目が1件ではないのでロードしません"
    return matches[0], None


def _first_listed_synth() -> tuple[str | None, dict[str, Any] | None]:
    listed, error = _browse("instruments", [], 200)
    if error or listed is None:
        return None, None
    items = _public_items(listed.get("items"))
    for name in PROBE_PREFERENCE:
        matches = [item for item in items if item.get("name") == name]
        if len(matches) == 1:
            return name, matches[0]
    return None, None


def _insert_one(track_index: int, device_name: str, choice: dict[str, Any]) -> dict[str, Any]:
    if choice.get("is_loadable") is True and choice.get("is_folder") is not True:
        loaded, load_error = invoke(
            "load_preset",
            timeout=_WRITE_TIMEOUT,
            track_index=track_index,
            category="instruments",
            path=[],
            name=device_name,
        )
        command = "load_preset"
    elif device_name in STOCK_INSTRUMENTS:
        loaded, load_error = invoke(
            "insert_first_available_instrument",
            timeout=_WRITE_TIMEOUT,
            track_index=track_index,
            candidates=[device_name],
        )
        command = "insert_first_available_instrument"
    else:
        return _failed("ブラウザ項目を一意にロードできません")
    if load_error or loaded is None:
        return _failed(load_error or "unconfirmed")
    if command == "load_preset" and loaded.get("verified_single_add") is not True:
        return {
            "ok": False,
            "status": "FAILED",
            "live_changed": True,
            "error": "device_count_not_confirmed",
            "loaded": loaded.get("loaded"),
        }
    reported = loaded.get("name") or loaded.get("loaded")
    if reported != device_name:
        return {
            "ok": False,
            "status": "FAILED",
            "live_changed": True,
            "error": "read_back_mismatch",
            "requested": device_name,
            "loaded": reported,
        }
    return {"ok": True, "loaded": loaded}


def _verified_path() -> Path:
    override = os.environ.get("KIHACHI_VERIFIED_INSTRUMENT_PATH", "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / "Library/Application Support/KIHACHI/verified-instrument.json"


def _read_verified() -> dict[str, Any] | None:
    path = _verified_path()
    if not path.is_file():
        return None
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(loaded, dict) or not isinstance(loaded.get("name"), str):
        return None
    return loaded


def _write_verified(name: str, uri: object) -> None:
    path = _verified_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"name": name, "uri": uri if isinstance(uri, str) else None}
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def _failed(detail: str) -> dict[str, Any]:
    return {"ok": False, "status": "FAILED", "live_changed": False, "error": detail}
