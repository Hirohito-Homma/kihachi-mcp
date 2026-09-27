"""Message contract parity between the Python side and the Max for Live device.

Max is not installed in CI, so this suite cannot prove the device works inside
Ableton Live. What it can prove is that the JavaScript source and the Python
services agree on the protocol: the same name, version, methods, operations,
precondition kinds, and ownership markers.

Real-device behaviour is covered by the manual checklist in
``docs/MANUAL_LIVE_TESTS.md``.
"""

import re
from pathlib import Path

import pytest

from kihachi_mcp.models.live_contract import (
    MANAGED_CLIP_PREFIX,
    MANAGED_MARKER,
    SCHEMA_VERSION,
    SUPPORTED_OPS,
)
from kihachi_mcp.services.live_device_catalog import STOCK_DEVICE_NAMES
from kihachi_mcp.services.live_transport import (
    PROTOCOL_NAME,
    PROTOCOL_VERSION,
    SUPPORTED_METHODS,
)

DEVICE_SOURCE = (
    Path(__file__).resolve().parents[1] / "maxforlive" / "kihachi.device.js"
)
BRIDGE_SOURCE = (
    Path(__file__).resolve().parents[1] / "maxforlive" / "kihachi.bridge.js"
)

PRECONDITION_KINDS = {
    "not_recording",
    "transport_stopped",
    "track_name_at_index",
    "track_missing",
    "scene_name_at_index",
    "session_slot_empty",
    "clip_is_managed",
    "device_available",
    "arrangement_range_free",
}


@pytest.fixture(scope="module")
def source() -> str:
    """Return the Max device JavaScript source."""
    return DEVICE_SOURCE.read_text(encoding="utf-8")


def _js_constant(source: str, name: str) -> str:
    match = re.search(rf'var {name} = "([^"]+)";', source)
    assert match is not None, f"{name} is not declared in the device source"
    return match.group(1)


def _js_number(source: str, name: str) -> int:
    match = re.search(rf"var {name} = (\d+);", source)
    assert match is not None, f"{name} is not declared in the device source"
    return int(match.group(1))


def test_the_device_source_exists() -> None:
    assert DEVICE_SOURCE.is_file()
    assert BRIDGE_SOURCE.is_file()


def test_node_bridge_uses_raw_loopback_udp() -> None:
    source = BRIDGE_SOURCE.read_text(encoding="utf-8")
    assert 'require("dgram")' in source
    assert 'require("max-api")' in source
    assert 'const LOOPBACK = "127.0.0.1"' in source
    assert "const BRIDGE_VERSION = 3" in source


def test_node_bridge_raises_the_macos_send_buffer_for_large_replies() -> None:
    """A get_state reply for ~50 Session clips is over macOS's 9216-byte default."""
    source = BRIDGE_SOURCE.read_text(encoding="utf-8")
    assert "const SEND_BUFFER_BYTES = 65507" in source
    assert "socket.setSendBufferSize(SEND_BUFFER_BYTES)" in source
    assert "not sent:" in source
    assert 'maxApi.outlet("request"' in source
    assert 'maxApi.addHandler("response"' in source
    assert "request.token !== handshake.token" in source
    assert "delete request.token" in source
    assert "request.bridge_authenticated = true" in source


def test_protocol_name_and_versions_match(source: str) -> None:
    assert _js_constant(source, "PROTOCOL_NAME") == PROTOCOL_NAME
    assert _js_number(source, "PROTOCOL_VERSION") == PROTOCOL_VERSION
    assert _js_number(source, "SCHEMA_VERSION") == SCHEMA_VERSION
    assert _js_constant(source, "DEVICE_VERSION") == "kihachi-live-device/0.2.9"
    assert 'call("get_version_string")' in source
    assert "include_session_clips" in source


def test_arrangement_readback_uses_the_clip_edge_not_loop_length(source: str) -> None:
    assert 'clip.set("end_marker", args.length_beats)' in source
    assert 'getProperty(clip, "end_time")' in source
    assert "length_beats: clipEnd - clipStart" in source
    assert "Full note dictionaries make large Arrangements time out" in source
    assert 'request.method === "get_arrangement_summary"' in source
    assert 'request.method === "get_arrangement_track_summary"' in source
    assert "function readArrangementTrack(trackIndex)" in source
    assert 'request.method === "get_locator_summary"' in source
    assert "function readLocatorSummary()" in source
    assert 'request.method === "get_drum_rack_summary"' in source
    assert "function readDrumRackSummary(trackIndex)" in source
    assert 'request.method === "get_track_playback_summary"' in source
    assert "function readTrackPlaybackSummary(trackIndex)" in source
    assert "function midiPitches(clip)" in source
    assert 'request.method === "get_playback_context_summary"' in source
    assert "function readPlaybackContextSummary(trackIndexes)" in source
    assert 'getPropertyOrNull(clip, "muted")' in source
    assert 'getPropertyOrNull(track, "output_meter_level")' in source
    assert 'request.method === "apply_masking_reduction"' in source
    assert "function applyMaskingReduction(requestId)" in source


def test_locator_creation_waits_for_live_and_is_idempotent(source: str) -> None:
    assert "function locatorAt(beats)" in source
    assert "function applyLocatorAndRespond(requestId, operation)" in source
    assert "createTask.schedule(75)" in source
    assert "verifyTask.schedule(75)" in source
    assert 'if (observed.name !== args.name)' in source


def test_arrangement_placement_uses_the_official_track_api(source: str) -> None:
    assert '"duplicate_clip_to_arrangement", "id " + source.id, args.start_beats' in source
    assert 'slot.call("duplicate_clip_to"' not in source
    assert "function applyArrangementClipAndRespond(requestId, operation)" in source
    assert "function arrangementClipAt(trackIndex, startBeats)" in source


def test_recovery_deletes_only_exact_managed_arrangement_targets(source: str) -> None:
    assert "function managedArrangementClip(trackIndex, name, startBeats)" in source
    assert "function applyArrangementDeleteAndRespond" in source
    assert 'track.call("delete_clip", "id " + clip.id)' in source
    assert "function applyLocatorDeleteAndRespond" in source
    assert 'operation.op === "delete_arrangement_clip"' in source
    assert 'operation.op === "delete_locator"' in source


def test_every_python_method_is_handled_by_the_device(source: str) -> None:
    for method in SUPPORTED_METHODS:
        assert f'request.method === "{method}"' in source, method


def test_every_python_operation_is_handled_by_the_device(source: str) -> None:
    for op in SUPPORTED_OPS:
        assert f'op === "{op}"' in source, op


def test_every_precondition_kind_is_checked_by_the_device(source: str) -> None:
    for kind in PRECONDITION_KINDS:
        assert f'kind === "{kind}"' in source, kind


def test_the_planner_only_emits_precondition_kinds_the_device_knows() -> None:
    planner_source = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "kihachi_mcp"
        / "services"
    )
    emitted: set[str] = set()
    for module in ("live_mutation_planner.py", "arrangement_expander.py"):
        text = (planner_source / module).read_text(encoding="utf-8")
        emitted.update(re.findall(r'LivePrecondition\(\s*"([a-z_]+)"', text))

    assert emitted
    assert emitted <= PRECONDITION_KINDS


def test_ownership_markers_match(source: str) -> None:
    assert _js_constant(source, "MANAGED_MARKER") == MANAGED_MARKER
    assert _js_constant(source, "MANAGED_CLIP_PREFIX") == f"[{MANAGED_CLIP_PREFIX}"


def test_the_device_reports_only_catalogued_stock_devices(source: str) -> None:
    block = re.search(
        r"var AVAILABLE_DEVICES = \[(.*?)\];", source, re.DOTALL
    )
    assert block is not None
    declared = set(re.findall(r'"([^"]+)"', block.group(1)))

    assert declared == set(STOCK_DEVICE_NAMES)


def test_stock_device_loading_uses_the_official_live_12_3_api(source: str) -> None:
    assert "INSERT_DEVICE_MIN_LIVE_MAJOR = 12" in source
    assert "INSERT_DEVICE_MIN_LIVE_MINOR = 3" in source
    assert 'track.call("insert_device", args.device_name)' in source
    assert 'outlet(1, "load_device"' not in source


def test_the_device_refuses_requests_without_a_valid_token(source: str) -> None:
    assert "request.bridge_authenticated !== true" in source
    assert '"unauthorized", "session token rejected"' in source


def test_the_device_never_echoes_the_supplied_token(source: str) -> None:
    unauthorized = re.search(
        r'respondError\(requestId, "unauthorized"[^)]*\)', source
    )
    assert unauthorized is not None
    assert "request.token" not in unauthorized.group(0)


def test_the_device_rejects_duplicate_request_ids(source: str) -> None:
    assert "seenRequestIds[requestId]" in source
    assert '"duplicate_request"' in source


def test_the_device_never_saves_the_set(source: str) -> None:
    assert 'call("save' not in source
    assert "save_set" not in source


def test_the_device_does_not_delete_tracks_scenes_devices_or_session_clips(
    source: str,
) -> None:
    for forbidden in (
        "delete_track",
        "delete_scene",
        "delete_device",
        'slot.call("delete_clip"',
    ):
        assert forbidden not in source, forbidden
    assert source.count('track.call("delete_clip", "id " + clip.id)') == 1


def test_the_device_source_avoids_es6_syntax(source: str) -> None:
    """The Max [js] engine is ES5; ES6 syntax fails to load at runtime."""
    code = "\n".join(
        line
        for line in source.splitlines()
        if not line.strip().startswith(("*", "/*", "//"))
    )

    assert not re.search(r"\bconst\s+\w+\s*=", code)
    assert not re.search(r"\blet\s+\w+\s*=", code)
    assert "=>" not in code
    assert "`" not in code
