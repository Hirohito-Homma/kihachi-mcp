"""Message contract parity between the Python side and the Max for Live device.

Max is not installed in CI, so this suite cannot prove the device works inside
Ableton Live. What it can prove is that the JavaScript source and the Python
services agree on the protocol: the same name, version, methods, operations,
precondition kinds, and ownership markers.

Real-device behaviour is covered by the manual checklist in
``docs/MANUAL_LIVE_TESTS.md``.
"""

import re
import shutil
import subprocess
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
    "device_name_at_index",
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


def test_parameter_items_are_compared_as_text(source: str) -> None:
    """Max hands "12"/"24" over as numbers; indexOf("24") on [12, 24] failed
    in Live on 2026-09-27 and stopped an apply at Wavetable's filter slope."""
    assert "items.push(String(raw[at]))" in source
    assert "items.indexOf(String(args.item))" in source


def test_the_device_source_exists() -> None:
    assert DEVICE_SOURCE.is_file()
    assert BRIDGE_SOURCE.is_file()


def test_drum_rack_summary_exposes_occupied_pad_sample_path(source: str) -> None:
    assert "function readDrumRackSummary(trackIndex)" in source
    assert "sample_path: chainCount === 1 ?" in source
    assert "drumPadSamplePath(" in source
    assert "device_class_name: padDeviceClass" in source


def test_replacement_accepts_live_simpler_class_name(source: str) -> None:
    assert 'deviceClass !== "Simpler" && deviceClass !== "OriginalSimpler"' in source


def test_drum_chain_is_inserted_on_rack_then_assigned_to_pad(source: str) -> None:
    """DrumPad exposes chains for reading; insert_chain belongs to RackDevice."""
    block = source.split("function loadDrumPadSample", 1)[1].split(
        "function replaceDrumPadSample", 1
    )[0]
    assert 'rack.api.call("insert_chain")' in block
    assert 'pad.api.call("insert_chain")' not in block
    assert 'var chainPath = rack.path + " chains " + (after - 1)' in block
    assert 'chain.set("in_note", note)' in block
    assert 'countChildren(pad.api, "chains") !== 1' in block


def test_drum_chain_loading_with_liveapi_model() -> None:
    """Catch wrong LiveAPI method targets before the next one-shot Live apply."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is unavailable for the Max JavaScript smoke test")
    fixture = Path(__file__).with_name("maxforlive_drum_chain_smoke.js")
    subprocess.run([node, str(fixture)], check=True, timeout=10)


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
    assert _js_constant(source, "DEVICE_VERSION") == "kihachi-live-device/0.3.5"
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
    from kihachi_mcp.models.live_contract import REMOTE_SCRIPT_OPS

    for op in SUPPORTED_OPS - REMOTE_SCRIPT_OPS:
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


def test_device_respond_ok_emits_error_when_exceeding_max_outlet_bytes() -> None:
    """respondOk が Max outlet 上限を超える場合は即時に response_too_large JSON を返す。"""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is unavailable")
    script = f"""
const fs = require("fs");
const vm = require("vm");
const outputs = [];
const sandbox = {{
  autowatch: 0,
  inlets: 0,
  outlets: 0,
  outlet: (idx, ...args) => outputs.push({{ idx, args }}),
  LiveAPI: function () {{}},
}};
vm.runInNewContext(fs.readFileSync({str(DEVICE_SOURCE)!r}, "utf8"), sandbox);
sandbox.respondOk("req-big", {{ blob: "x".repeat(35000) }});
if (outputs.length !== 1) throw new Error("expected 1 outlet call");
const reply = JSON.parse(outputs[0].args[0]);
if (reply.ok !== false || reply.request_id !== "req-big" || reply.error.code !== "response_too_large") {{
  throw new Error("unexpected reply: " + outputs[0].args[0]);
}}
"""
    subprocess.run([node, "-e", script], check=True, timeout=10)


def test_bridge_emits_immediate_error_json_on_invalid_or_oversized_response() -> None:
    """kihachi.bridge.js が不正 JSON または 60KB 超の返信を受けた際に黙殺せず即エラー JSON を返す。"""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is unavailable")
    script = f"""
const fs = require("fs");
const vm = require("vm");
const Module = require("module");
const sent = [];
const handlers = {{}};
const fakeMaxApi = {{
  outlet: () => {{}},
  addHandler: (name, fn) => {{ handlers[name] = fn; }},
}};
const fakeSocket = {{
  on: () => {{}},
  bind: () => {{}},
  send: (buf, port, host, cb) => {{ sent.push(buf.toString("utf8")); if (cb) cb(null); }},
  close: () => {{}},
}};
const fakeRequire = (name) => {{
  if (name === "max-api") return fakeMaxApi;
  if (name === "dgram") return {{ createSocket: () => fakeSocket }};
  return require(name);
}};
const fn = new Function("require", "process", fs.readFileSync({str(BRIDGE_SOURCE)!r}, "utf8"));
fn(fakeRequire, {{ platform: "darwin", on: () => {{}} }});

handlers.response('{{"protocol":"kihachi.live","version":1,"request_id":"req-cut","ok":true,"result":');
handlers.response(JSON.stringify({{ protocol: "kihachi.live", version: 1, request_id: "req-huge", ok: true, result: {{ data: "a".repeat(65000) }} }}));

if (sent.length !== 2) throw new Error("expected 2 immediate error replies, got " + sent.length);
const r1 = JSON.parse(sent[0]);
const r2 = JSON.parse(sent[1]);
if (r1.ok !== false || r1.request_id !== "req-cut" || r1.error.code !== "protocol") {{
  throw new Error("unexpected r1: " + sent[0]);
}}
if (r2.ok !== false || r2.request_id !== "req-huge" || r2.error.code !== "response_too_large") {{
  throw new Error("unexpected r2: " + sent[1]);
}}
"""
    subprocess.run([node, "-e", script], check=True, timeout=10)


def test_amxd_embeds_matching_js_sources() -> None:
    """配布用 .amxd が最新の kihachi.device.js / kihachi.bridge.js を Freeze 構造（flag / code 登録含む）で内包していること。"""
    from scripts.build_amxd import (
        check_amxd_sync,
        extract_amxd_dire_metadata,
        extract_patcher_json,
    )

    maxforlive_dir = DEVICE_SOURCE.parent
    amxd_path = maxforlive_dir / "KIHACHI Live Device.amxd"
    assert amxd_path.is_file()
    assert check_amxd_sync(amxd_path, maxforlive_dir) == []

    amxd_bytes = amxd_path.read_bytes()
    dire_meta = extract_amxd_dire_metadata(amxd_bytes)
    assert dire_meta["kihachi.device.js"]["flag"] == 0x0
    assert dire_meta["kihachi.bridge.js"]["flag"] == 0x8

    patch = extract_patcher_json(amxd_bytes)
    project = patch["patcher"]["project"]
    assert project["readonly"] == 1
    assert project["contents"]["code"] == {
        "kihachi.device.js": {"kind": "javascript", "local": 1},
        "kihachi.bridge.js": {"kind": "javascript", "local": 1},
    }


def test_check_amxd_sync_detects_missing_code_registration_and_wrong_flag(tmp_path: Path) -> None:
    """check_amxd_sync が dire.flag 不一致や patcher.project.contents.code 欠落を検知すること。"""
    from scripts.build_amxd import check_amxd_sync

    maxforlive_dir = DEVICE_SOURCE.parent
    for js_name in ("kihachi.device.js", "kihachi.bridge.js"):
        (tmp_path / js_name).write_bytes((maxforlive_dir / js_name).read_bytes())

    # 壊した Freeze .amxd（contents.code 欠落 + flag=0 のままのケースを模倣）
    amxd_path = tmp_path / "KIHACHI Live Device.amxd"
    raw = (maxforlive_dir / "KIHACHI Live Device.amxd").read_bytes()
    mutated = raw.replace(b'"code": {', b'"xode": {')
    assert mutated != raw
    amxd_path.write_bytes(mutated)

    errors = check_amxd_sync(amxd_path, tmp_path)
    assert any("contents.code" in err for err in errors)

