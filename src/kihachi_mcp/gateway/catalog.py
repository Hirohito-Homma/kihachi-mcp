"""What the gateway exposes, and what the AbletonGPT Remote Script can do.

Status values:

- SUPPORTED: a gateway tool calls this today
- PARTIAL: the Remote Script command exists, and this gateway does not call it
- NOT IMPLEMENTED: no Remote Script command was found for that action

This list is the AbletonGPT socket (TCP 127.0.0.1:9877). The Max for Live
device is a separate bridge and is not called from these tools.
"""

from __future__ import annotations

from typing import Any

RISK_SAFE = "safe"
RISK_CONFIRM = "requires_confirmation"
RISK_DESTRUCTIVE = "destructive"

EXPOSED: tuple[dict[str, Any], ...] = (
    {
        "name": "ableton_ping",
        "status": "SUPPORTED",
        "risk": RISK_SAFE,
        "remote_command": "ping",
    },
    {
        "name": "ableton_status",
        "status": "SUPPORTED",
        "risk": RISK_SAFE,
        "remote_command": "get_state",
        "note": "live_version と project は Remote Script が返さないため null",
    },
    {
        "name": "server_status",
        "status": "SUPPORTED",
        "risk": RISK_SAFE,
        "remote_command": None,
    },
    {
        "name": "set_tempo",
        "status": "SUPPORTED",
        "risk": RISK_CONFIRM,
        "remote_command": "set_tempo",
        "note": "confirm=true がない呼び出しは Live に送らない",
    },
    {
        "name": "ableton_tool_catalogue",
        "status": "SUPPORTED",
        "risk": RISK_SAFE,
        "remote_command": None,
    },
    {"name": "ableton_play", "status": "SUPPORTED", "risk": RISK_CONFIRM, "remote_command": "set_transport"},
    {"name": "ableton_stop", "status": "SUPPORTED", "risk": RISK_CONFIRM, "remote_command": "set_transport"},
    {"name": "ableton_list_tracks", "status": "SUPPORTED", "risk": RISK_SAFE, "remote_command": "get_state"},
    {
        "name": "ableton_create_midi_track",
        "status": "SUPPORTED",
        "risk": RISK_CONFIRM,
        "remote_command": "create_track",
        "note": "末尾に追加する。挿入位置は受け取らない",
    },
    {"name": "ableton_create_audio_track", "status": "SUPPORTED", "risk": RISK_CONFIRM, "remote_command": "create_track"},
    {
        "name": "ableton_create_midi_clip",
        "status": "SUPPORTED",
        "risk": RISK_CONFIRM,
        "remote_command": "create_midi_clip",
        "note": "ノート時間は beats。小節は 4/4 の 4 beats",
    },
    {
        "name": "ableton_add_notes",
        "status": "SUPPORTED",
        "risk": RISK_CONFIRM,
        "remote_command": "apply_expression_to_clip",
        "note": "既存ノートと合わせて置き換える。曲の初回生成は create_midi_clip にノートを載せる",
    },
    {"name": "ableton_set_volume", "status": "SUPPORTED", "risk": RISK_CONFIRM, "remote_command": "set_track_volume"},
    {"name": "ableton_set_pan", "status": "SUPPORTED", "risk": RISK_CONFIRM, "remote_command": "set_track_pan"},
    {"name": "ableton_mute", "status": "SUPPORTED", "risk": RISK_CONFIRM, "remote_command": "set_track_mute"},
    {"name": "ableton_solo", "status": "SUPPORTED", "risk": RISK_CONFIRM, "remote_command": "set_track_solo"},
    {"name": "ableton_list_devices", "status": "SUPPORTED", "risk": RISK_SAFE, "remote_command": "get_track_devices"},
    {
        "name": "ableton_get_arrangement",
        "status": "SUPPORTED",
        "risk": RISK_SAFE,
        "remote_command": "get_arrangement_clips",
    },
    {
        "name": "get_midi_clip_notes",
        "status": "SUPPORTED",
        "risk": RISK_SAFE,
        "remote_command": "get_midi_clip_notes",
        "note": "Session MIDI クリップを読み取り、4096 ノート超の切り詰めを明示する。書き込みなし",
    },
    {
        "name": "produce_ableton_song",
        "status": "SUPPORTED",
        "risk": RISK_CONFIRM,
        "remote_command": "create_track, create_midi_clip, copy_session_clip_to_arrangement",
        "note": "dry_run では Live を呼ばない。confirm が無い書き込みはしない",
    },
)

# Remote Script commands that exist and are intentionally not called from here.
PARTIAL: tuple[dict[str, Any], ...] = (
    {"name": "get_transport_state", "status": "PARTIAL", "risk": RISK_SAFE, "remote_command": "get_transport_state"},
    {"name": "launch_clip", "status": "PARTIAL", "risk": RISK_CONFIRM, "remote_command": "fire_clip"},
    {"name": "set_device_parameter", "status": "PARTIAL", "risk": RISK_CONFIRM, "remote_command": "set_device_parameter"},
    {"name": "load_preset", "status": "PARTIAL", "risk": RISK_CONFIRM, "remote_command": "load_preset"},
    {"name": "add_native_device", "status": "PARTIAL", "risk": RISK_CONFIRM, "remote_command": "add_native_device"},
    {"name": "toggle_cue_at_playhead", "status": "PARTIAL", "risk": RISK_DESTRUCTIVE, "remote_command": "toggle_cue_at_playhead"},
)

NOT_IMPLEMENTED: tuple[dict[str, Any], ...] = (
    {
        "name": "rename_track",
        "status": "NOT IMPLEMENTED",
        "risk": RISK_CONFIRM,
        "remote_command": None,
        "note": "create_track の name で作成時に命名する。改名コマンドは無い",
    },
    {"name": "delete_track", "status": "NOT IMPLEMENTED", "risk": RISK_DESTRUCTIVE, "remote_command": None},
    {"name": "delete_clip", "status": "NOT IMPLEMENTED", "risk": RISK_DESTRUCTIVE, "remote_command": None},
    {"name": "overwrite_project", "status": "NOT IMPLEMENTED", "risk": RISK_DESTRUCTIVE, "remote_command": None},
    {"name": "replace_device", "status": "NOT IMPLEMENTED", "risk": RISK_DESTRUCTIVE, "remote_command": None},
    {"name": "remove_device", "status": "NOT IMPLEMENTED", "risk": RISK_DESTRUCTIVE, "remote_command": None},
    {"name": "save_over_existing_project", "status": "NOT IMPLEMENTED", "risk": RISK_DESTRUCTIVE, "remote_command": None},
)


def catalogue() -> dict[str, Any]:
    """Static inventory. This does not contact Ableton Live."""
    return {
        "bridge": "abletongpt-remote-script",
        "exposed": list(EXPOSED),
        "partial": list(PARTIAL),
        "not_implemented": list(NOT_IMPLEMENTED),
    }
