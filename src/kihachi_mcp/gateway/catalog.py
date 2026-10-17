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
        "name": "ableton_list_instruments",
        "status": "SUPPORTED",
        "risk": RISK_SAFE,
        "remote_command": "browse_presets",
        "note": "instruments ブラウザの列挙だけ。ロードしない",
    },
    {
        "name": "ableton_search_browser",
        "status": "SUPPORTED",
        "risk": RISK_SAFE,
        "remote_command": "browse_presets",
        "note": "同名が複数なら ambiguous。このツールはロードしない",
    },
    {
        "name": "ableton_load_stock_instrument",
        "status": "SUPPORTED",
        "risk": RISK_CONFIRM,
        "remote_command": "insert_first_available_instrument, load_preset",
        "note": "ブラウザで名前が1件のときだけ。読み戻しが違うと成功にしない",
    },
    {
        "name": "ableton_device_probe",
        "status": "SUPPORTED",
        "risk": RISK_CONFIRM,
        "remote_command": "create_track, insert_first_available_instrument, create_midi_clip",
        "note": "KIHACHI DEVICE PROBE だけ。既存曲トラックには載せない",
    },
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
        "name": "load_sample_to_simpler",
        "status": "SUPPORTED",
        "risk": RISK_CONFIRM,
        "remote_command": "load_simpler_sample",
        "note": "Simpler.replace_sample。sample.file_path が一致したときだけ VERIFIED。Live 受入は未実施",
    },
    {
        "name": "list_drum_pads",
        "status": "SUPPORTED",
        "risk": RISK_SAFE,
        "remote_command": "list_drum_pads",
    },
    {
        "name": "load_sample_to_pad",
        "status": "SUPPORTED",
        "risk": RISK_CONFIRM,
        "remote_command": "load_drum_pad_sample",
        "note": "空パッドは insert_chain して in_note を合わせ、Simpler.replace_sample する",
    },
    {
        "name": "get_arrangement_midi_notes",
        "status": "SUPPORTED",
        "risk": RISK_SAFE,
        "remote_command": "get_arrangement_midi_notes",
        "note": "start_time と end_time が一つに決まる Arrangement MIDI だけ",
    },
    {
        "name": "add_arrangement_midi_notes",
        "status": "SUPPORTED",
        "risk": RISK_CONFIRM,
        "remote_command": "replace_arrangement_midi_notes",
    },
    {
        "name": "replace_arrangement_midi_notes",
        "status": "SUPPORTED",
        "risk": RISK_CONFIRM,
        "remote_command": "replace_arrangement_midi_notes",
    },
    {
        "name": "delete_arrangement_midi_notes",
        "status": "SUPPORTED",
        "risk": RISK_DESTRUCTIVE,
        "remote_command": "replace_arrangement_midi_notes",
        "note": "pitch と start_time が一つに決まるノートだけ消す",
    },
    {
        "name": "move_arrangement_clip",
        "status": "SUPPORTED",
        "risk": RISK_DESTRUCTIVE,
        "remote_command": "move_arrangement_clip",
        "note": "重ならない位置へ複製してから元を delete_clip。start_time の代入はしない",
    },
    {
        "name": "duplicate_arrangement_clip",
        "status": "SUPPORTED",
        "risk": RISK_CONFIRM,
        "remote_command": "duplicate_arrangement_clip",
    },
    {
        "name": "delete_arrangement_clip",
        "status": "SUPPORTED",
        "risk": RISK_DESTRUCTIVE,
        "remote_command": "delete_arrangement_clip",
        "note": "start_time と end_time が一意のクリップだけ。複数一致は拒否",
    },
    {
        "name": "ableton_get_song_time",
        "status": "SUPPORTED",
        "risk": RISK_SAFE,
        "remote_command": "get_transport_state",
        "note": "単位は beats。current_song_time と、停止中の再生開始位置 start_time を両方返す",
    },
    {
        "name": "ableton_set_song_time",
        "status": "SUPPORTED",
        "risk": RISK_CONFIRM,
        "remote_command": None,
        "note": "絶対シークは送らない。current_song_time への代入は移動しないと Remote Script が記録している。jump_by にはしない",
    },
    {
        "name": "ableton_set_start_time",
        "status": "SUPPORTED",
        "risk": RISK_CONFIRM,
        "remote_command": "set_start_time",
        "note": "停止中に Arrangement の start_time だけを書く。再生は始めない。current_song_time と jump_by は使わない",
    },
    {
        "name": "ableton_jump_song_time",
        "status": "SUPPORTED",
        "risk": RISK_CONFIRM,
        "remote_command": "jump_transport",
        "note": "相対移動だけ。絶対シークには使わない",
    },
    {
        "name": "ableton_get_playback_state",
        "status": "SUPPORTED",
        "risk": RISK_SAFE,
        "remote_command": "get_transport_state",
        "note": "playing_session_clips と fired_clips は Remote Script が返さないので null",
    },
    {
        "name": "produce_ableton_song",
        "status": "SUPPORTED",
        "risk": RISK_CONFIRM,
        "remote_command": "create_track, create_midi_clip, copy_session_clip_to_arrangement",
        "note": "dry_run では Live を呼ばない。confirm が無い書き込みはしない",
    },
    {
        "name": "sample_library_status",
        "status": "SUPPORTED",
        "risk": RISK_SAFE,
        "remote_command": None,
        "note": "設定したライブラリの件数だけ返す。パスは返さない",
    },
    {
        "name": "index_sample_library",
        "status": "SUPPORTED",
        "risk": RISK_CONFIRM,
        "remote_command": None,
        "note": "KIHACHI_SAMPLE_LIBRARY_PATHS だけを読む。音声ファイルは変更しない",
    },
    {
        "name": "search_samples",
        "status": "SUPPORTED",
        "risk": RISK_SAFE,
        "remote_command": None,
        "note": "sample_id を返す。絶対パスは返さない",
    },
    {"name": "get_sample", "status": "SUPPORTED", "risk": RISK_SAFE, "remote_command": None},
    {
        "name": "get_sample_metadata",
        "status": "SUPPORTED",
        "risk": RISK_SAFE,
        "remote_command": None,
        "note": "sample_id で引く。絶対パスは返さない",
    },
    {
        "name": "find_samples_for_song",
        "status": "SUPPORTED",
        "risk": RISK_SAFE,
        "remote_command": None,
        "note": "サンプル計画だけ。Live は呼ばない",
    },
    {
        "name": "select_sample",
        "status": "SUPPORTED",
        "risk": RISK_CONFIRM,
        "remote_command": None,
        "note": "assisted の採用だけを覚える。Live は呼ばない",
    },
    {
        "name": "place_sample",
        "status": "SUPPORTED",
        "risk": RISK_CONFIRM,
        "remote_command": "import_audio_clip, copy_session_clip_to_arrangement",
        "note": "ワープ済みのときだけ Arrangement へコピーする。ワープを付けるコマンドは無い",
    },
    {
        "name": "sample_loading_capabilities",
        "status": "SUPPORTED",
        "risk": RISK_SAFE,
        "remote_command": None,
        "note": "サンプル取り込みの可否。Live は呼ばない",
    },
    {
        "name": "probe_indexed_sample_audio",
        "status": "SUPPORTED",
        "risk": RISK_CONFIRM,
        "remote_command": "import_audio_clip",
        "note": "sample_id だけを受ける。Session のオーディオクリップ。Arrangement へは置かない",
    },
    {
        "name": "place_audio_one_shot_sample",
        "status": "SUPPORTED",
        "risk": RISK_CONFIRM,
        "remote_command": "copy_session_clip_to_arrangement",
        "note": "audio_one_shot のときだけ未ワープを複製する。通常コピーは未ワープを拒否する。削除はできない",
    },
)

# Remote Script commands that exist and are intentionally not called from here.
PARTIAL: tuple[dict[str, Any], ...] = (
    {"name": "launch_clip", "status": "PARTIAL", "risk": RISK_CONFIRM, "remote_command": "fire_clip"},
    {
        "name": "stop_all_clips",
        "status": "PARTIAL",
        "risk": RISK_CONFIRM,
        "remote_command": "stop_track_clips",
        "note": "track.stop_all_clips。ゲートウェイのツールにはしていない",
    },
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
    {
        "name": "delete_clip",
        "status": "NOT IMPLEMENTED",
        "risk": RISK_DESTRUCTIVE,
        "remote_command": None,
        "note": "曖昧な一括削除はしない。一意な対象は delete_arrangement_clip",
    },
    {"name": "overwrite_project", "status": "NOT IMPLEMENTED", "risk": RISK_DESTRUCTIVE, "remote_command": None},
    {"name": "replace_device", "status": "NOT IMPLEMENTED", "risk": RISK_DESTRUCTIVE, "remote_command": None},
    {"name": "remove_device", "status": "NOT IMPLEMENTED", "risk": RISK_DESTRUCTIVE, "remote_command": None},
    {"name": "save_over_existing_project", "status": "NOT IMPLEMENTED", "risk": RISK_DESTRUCTIVE, "remote_command": None},
    {
        "name": "back_to_arrangement",
        "status": "NOT IMPLEMENTED",
        "risk": RISK_CONFIRM,
        "remote_command": None,
        "note": "song.back_to_arranger は get_transport_state が bool で読むだけ。代入もメソッドも無く、真偽の意味はこのコードに無い",
    },
    {
        "name": "continue_playing",
        "status": "NOT IMPLEMENTED",
        "risk": RISK_CONFIRM,
        "remote_command": None,
        "note": "Remote Script に continue_playing は無い。再生は song.start_playing",
    },
    {
        "name": "play_selection",
        "status": "NOT IMPLEMENTED",
        "risk": RISK_CONFIRM,
        "remote_command": None,
        "note": "Remote Script に play_selection は無い",
    },
)


def catalogue() -> dict[str, Any]:
    """Static inventory. This does not contact Ableton Live."""
    return {
        "bridge": "abletongpt-remote-script",
        "exposed": list(EXPOSED),
        "partial": list(PARTIAL),
        "not_implemented": list(NOT_IMPLEMENTED),
    }
