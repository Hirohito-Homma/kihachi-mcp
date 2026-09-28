import json
from pathlib import Path

from live_fixtures import gate, snapshot_of

from kihachi_mcp.models.live_contract import OP_CREATE_AUDIO_TRACK, OP_LOAD_LIVE_DEVICE
from kihachi_mcp.services.device_probe import (
    PROBE_DEVICES,
    PROBE_TRACK,
    create_probe_plan,
    load_parameters,
)
from kihachi_mcp.services.live_execution_service import LiveExecutionService
from kihachi_mcp.services.live_state_inspector import LiveStateInspector
from kihachi_mcp.services.live_transport_fake import FakeLiveSet, FakeLiveTransport
from kihachi_mcp.services.studio_runtime import StudioRuntime

_COMPRESSOR = {
    "class_name": "Compressor2",
    "parameters": [
        {"name": "Threshold", "min": 0.0, "max": 1.0, "is_quantized": False,
         "value_items": [], "default": 1.0},
        {"name": "Model", "min": 0.0, "max": 2.0, "is_quantized": True,
         "value_items": ["Peak", "RMS", "Expand"], "default": 0.0},
    ],
}


def _runtime(tmp_path: Path, live: FakeLiveSet):
    transport = FakeLiveTransport(live)
    inspector = LiveStateInspector(transport)
    approvals = gate(tmp_path)
    runtime = StudioRuntime(
        transport=transport,
        inspector=inspector,
        executor=LiveExecutionService(
            transport=transport, inspector=inspector, approval_gate=approvals
        ),
        gate=approvals,
        export_dir=tmp_path / "exports",
        parameter_file=tmp_path / "parameters.json",
    )
    return runtime, transport


def test_the_probe_plan_adds_one_track_and_only_missing_devices() -> None:
    live = FakeLiveSet(live_version="12.4.3")
    live.add_track("User bass")
    probe = live.add_track(PROBE_TRACK, track_type="audio")
    probe["device_names"] = ["EQ Eight"]
    plan = create_probe_plan(snapshot_of(FakeLiveTransport(live)))
    ops = [operation.op for operation in plan.operations]
    assert OP_CREATE_AUDIO_TRACK not in ops
    loads = [op.arguments["device_name"] for op in plan.operations if op.op == OP_LOAD_LIVE_DEVICE]
    assert loads == [name for name in PROBE_DEVICES if name != "EQ Eight"]
    assert all(op.target["track_index"] == probe["index"] for op in plan.operations)
    assert not any(operation.destructive for operation in plan.operations)


def test_the_probe_refuses_while_live_plays() -> None:
    live = FakeLiveSet(live_version="12.4.3", is_playing=True)
    plan = create_probe_plan(snapshot_of(FakeLiveTransport(live)))
    assert plan.conflicts and not plan.operations


def test_probing_saves_the_parameter_names_live_reports(tmp_path: Path) -> None:
    live = FakeLiveSet(live_version="12.4.3", device_parameters={"Compressor": _COMPRESSOR})
    live.add_track("User drums")
    runtime, transport = _runtime(tmp_path, live)
    assert runtime.probe_device_parameters()["ok"] is False  # needs confirmation
    result = runtime.probe_device_parameters(confirmed=True)
    probe = next(track for track in transport.live_set.tracks if track["name"] == PROBE_TRACK)
    assert probe["device_names"] == list(PROBE_DEVICES)
    assert transport.live_set.tracks[0]["device_names"] == []  # the user's track is untouched
    saved = load_parameters(tmp_path / "parameters.json")
    assert saved["live_version"] == "12.4.3"
    names = [item["name"] for item in saved["devices"]["Compressor"]["parameters"]]
    assert names == ["Threshold", "Model"]
    assert result["devices"]["Compressor"] == 2
    again = runtime.probe_device_parameters(confirmed=True)
    assert again["receipt"] is None  # nothing left to insert the second time
    assert json.loads((tmp_path / "parameters.json").read_text())["devices"]
