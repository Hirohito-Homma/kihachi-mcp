"""Truthful Ableton capability discovery derived from the active contracts."""

from __future__ import annotations

from typing import Any

from kihachi_mcp.models.live_contract import SCHEMA_VERSION, SUPPORTED_OPS
from kihachi_mcp.services.live_transport import SUPPORTED_METHODS

SUPPORTED = "supported"
CONDITIONAL = "conditional"
UNSUPPORTED = "unsupported"


def discover_capabilities(
    health: dict[str, Any], abletongpt_state: str | None = None
) -> dict[str, Any]:
    """Describe only features evidenced by the Python/Max protocol contracts."""
    connected = bool(health.get("connected"))
    protocol_supported = bool(health.get("protocol_supported", connected))
    bridge_ready = connected and protocol_supported
    remote_ready = abletongpt_state == "ready"

    rows = [
        _row(
            "transport",
            CONDITIONAL,
            ["set_tempo"] if "set_tempo" in SUPPORTED_OPS else [],
            ["play", "stop", "record", "set_position"],
            bridge_ready,
            "テンポ変更だけを承認後に実行できます。再生・停止・録音・位置移動は未実装です。",
        ),
        _row(
            "tracks",
            CONDITIONAL,
            _present("create_midi_track", "create_audio_track", "set_track_name", "set_track_color"),
            ["duplicate_track", "delete_track", "mute", "solo", "arm"],
            bridge_ready,
            "新規のKIHACHI管理トラックだけを対象にします。",
        ),
        _row(
            "midi",
            CONDITIONAL,
            _present("create_session_clip", "replace_clip_notes"),
            ["read_note_events", "quantize_existing_clip", "humanize_existing_clip"],
            bridge_ready,
            "管理候補のノート書込みは可能ですが、任意の既存Live MIDIノート取得は未実装です。",
        ),
        _row(
            "audio_clips",
            UNSUPPORTED,
            [],
            ["create_audio_clip", "read_audio_clip", "replace_audio_clip"],
            False,
            "Audio Track作成は可能ですが、Audio Clip配置契約はありません。",
        ),
        _row(
            "arrangement",
            CONDITIONAL,
            _present("create_locator", "place_arrangement_clip", "delete_locator", "delete_arrangement_clip"),
            ["move_arbitrary_clip", "duplicate_arbitrary_clip"],
            bridge_ready,
            "KIHACHI管理クリップとロケーターに限定し、削除は承認が必要です。",
        ),
        _row(
            "devices",
            CONDITIONAL,
            _present("load_live_device", "set_device_parameter", "set_sidechain_source"),
            ["load_external_plugin", "delete_device"],
            bridge_ready,
            "許可済みLive標準デバイスと読み戻せるパラメータだけを扱います。",
        ),
        _row(
            "browser",
            CONDITIONAL,
            ["search_and_load_core_library_kit"] if remote_ready else [],
            ["general_browser_search"],
            remote_ready,
            "AbletonGPT Remote ScriptがREADYの場合だけ、既知のCore Libraryキットを検索・ロードします。",
        ),
        _row(
            "mixer",
            CONDITIONAL,
            _present("set_track_mixer"),
            ["returns", "master_mixer", "automation"],
            bridge_ready,
            "管理トラックのvolume/panだけを検証付きで設定します。",
        ),
        _row(
            "sends",
            UNSUPPORTED,
            [],
            ["read_sends", "set_sends", "create_return"],
            False,
            "現在のLive stateと操作契約にsend/returnがありません。",
        ),
        _row(
            "automation",
            UNSUPPORTED,
            [],
            ["read_automation", "write_automation", "delete_automation"],
            False,
            "現在のMax for Live契約にautomation操作がありません。",
        ),
        _row(
            "undo",
            UNSUPPORTED,
            [],
            ["undo"],
            False,
            "KIHACHIの計画・受領書はありますが、Live Undoを呼ぶ契約はありません。",
        ),
        _row(
            "redo",
            UNSUPPORTED,
            [],
            ["redo"],
            False,
            "Live Redoを呼ぶ契約はありません。",
        ),
        _row(
            "save",
            UNSUPPORTED,
            [],
            ["save_live_set", "save_live_set_as"],
            False,
            "Set保存を実行可能として提示しません。",
        ),
        _row(
            "sample_loading",
            CONDITIONAL,
            _present("load_drum_pad_sample"),
            ["load_loop_to_audio_track", "replace_arbitrary_sample"],
            bridge_ready,
            "ローカルone-shotを空のDrum Rack padへロードする範囲だけ対応します。",
        ),
        _row(
            "warp",
            UNSUPPORTED,
            [],
            ["enable_warp", "tempo_match", "transpose_audio", "set_warp_markers"],
            False,
            "現在の操作契約にwarpがありません。",
        ),
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "connected": connected,
        "protocol_supported": protocol_supported,
        "bridge_ready": bridge_ready,
        "live_version": str(health.get("live_version") or ""),
        "abletongpt_state": abletongpt_state or "unavailable",
        "capabilities": rows,
        "executable_capability_ids": [
            row["id"] for row in rows if row["status"] != UNSUPPORTED and row["runtime_ready"]
        ],
        "contract_evidence": {
            "methods": sorted(SUPPORTED_METHODS),
            "operations": sorted(SUPPORTED_OPS),
        },
    }


def _present(*operations: str) -> list[str]:
    return [operation for operation in operations if operation in SUPPORTED_OPS]


def _row(
    capability_id: str,
    status: str,
    actions: list[str],
    unsupported_actions: list[str],
    runtime_ready: bool,
    detail: str,
) -> dict[str, Any]:
    return {
        "id": capability_id,
        "status": status,
        "runtime_ready": runtime_ready,
        "actions": actions,
        "unsupported_actions": unsupported_actions,
        "detail": detail,
    }
