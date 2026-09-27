from pathlib import Path

import pytest
from live_fixtures import gate

from kihachi_mcp.services.abletongpt_kits import (
    AbletonGPTKitLoader,
    KitLoadError,
    RemoteScriptSocket,
)
from kihachi_mcp.services.brief_parser import extract_explicit
from kihachi_mcp.services.live_execution_service import LiveExecutionService
from kihachi_mcp.services.live_state_inspector import LiveStateInspector
from kihachi_mcp.services.live_transport_fake import FakeLiveSet, FakeLiveTransport
from kihachi_mcp.services.midi_candidate_builder import build_candidate
from kihachi_mcp.services.studio_interpreter import assemble_brief
from kihachi_mcp.services.studio_runtime import StudioRuntime


class FakeRemoteScript:
    """AbletonGPT's Remote Script, as far as kit loading sees it."""

    def __init__(self, browser=None, occupied=(), load_times_out=False, loads_wrong=False):
        self.browser = browser or {
            (): [
                {"name": "Drum Hits", "is_folder": True},
                {"name": "909 Core Kit.adg", "is_loadable": True},
                {"name": "707 Core Kit.adg", "is_loadable": True},
            ],
            ("Drum Hits",): [{"name": "Kick 1.wav", "is_loadable": True}],
        }
        self.devices = {index: [{"name": "Drum Rack", "type": 1}] for index in occupied}
        self.load_times_out = load_times_out
        self.loads_wrong = loads_wrong
        self.calls = []
        self.online = True

    def __call__(self, command, timeout=None, **params):
        self.calls.append((command, params))
        if not self.online:
            raise KitLoadError("offline")
        if command == "ping":
            return {"pong": True}
        if command == "browse_presets":
            return {"items": self.browser.get(tuple(params["path"]), [])}
        if command == "get_track_devices":
            return {"devices": self.devices.get(params["track_index"], [])}
        if command == "load_preset":
            name = params["name"][:-4]
            self.devices[params["track_index"]] = [
                {"name": "Other Kit" if self.loads_wrong else name, "type": 1}
            ]
            if self.load_times_out:
                raise KitLoadError("timed out")
            return {"loaded": params["name"]}
        raise AssertionError(command)


def test_the_first_choice_is_loaded_and_the_walk_stops_at_the_root() -> None:
    remote = FakeRemoteScript()
    assert AbletonGPTKitLoader(remote).load(3, ["909 Core Kit", "707 Core Kit"]) == "909 Core Kit"
    browses = [params["path"] for command, params in remote.calls if command == "browse_presets"]
    assert browses == [[]]
    load = next(params for command, params in remote.calls if command == "load_preset")
    assert load == {"track_index": 3, "category": "drums", "path": [], "name": "909 Core Kit.adg"}


def test_a_later_candidate_is_used_when_the_first_is_missing() -> None:
    remote = FakeRemoteScript(browser={(): [{"name": "707 Core Kit.adg", "is_loadable": True}]})
    assert AbletonGPTKitLoader(remote).load(1, ["909 Core Kit", "707 Core Kit"]) == "707 Core Kit"


def test_a_track_that_already_has_an_instrument_is_refused() -> None:
    remote = FakeRemoteScript(occupied=(2,))
    with pytest.raises(KitLoadError, match="既に楽器"):
        AbletonGPTKitLoader(remote).load(2, ["909 Core Kit"])
    assert not [c for c in remote.calls if c[0] == "load_preset"]


def test_a_timed_out_load_is_judged_by_readback_and_never_resent() -> None:
    remote = FakeRemoteScript(load_times_out=True)
    assert AbletonGPTKitLoader(remote).load(1, ["909 Core Kit"]) == "909 Core Kit"
    assert sum(1 for c in remote.calls if c[0] == "load_preset") == 1


def test_a_readback_that_names_another_kit_fails() -> None:
    with pytest.raises(KitLoadError, match="確認できません"):
        AbletonGPTKitLoader(FakeRemoteScript(loads_wrong=True)).load(1, ["909 Core Kit"])


def test_no_candidate_in_the_browser_fails_without_loading() -> None:
    remote = FakeRemoteScript(browser={(): []})
    with pytest.raises(KitLoadError, match="候補のキットがありません"):
        AbletonGPTKitLoader(remote).load(1, ["909 Core Kit"])


def test_a_non_local_host_is_refused(tmp_path: Path) -> None:
    config = tmp_path / "config.json"
    config.write_text('{"host": "10.0.0.5", "token": "x"}', encoding="utf-8")
    with pytest.raises(KitLoadError, match="ローカル"):
        RemoteScriptSocket.from_config(config)


def _intent():
    return {
        "genre": "tech_house", "tempo": 120, "bars": 32, "key": "Dm", "mood": "",
        "hats_first_half": "normal", "hats_second_half": "normal", "bass_register": "mid",
        "note_density": "normal", "drop_start_bar": 0, "unhandled": [], "ambiguous": [],
    }


def _runtime(tmp_path: Path, remote: FakeRemoteScript):
    transport = FakeLiveTransport(FakeLiveSet(live_version="12.4.5", tempo=120))
    inspector = LiveStateInspector(transport)
    approvals = gate(tmp_path)
    runtime = StudioRuntime(
        transport=transport,
        inspector=inspector,
        executor=LiveExecutionService(transport=transport, inspector=inspector, approval_gate=approvals),
        gate=approvals,
        kit_loader=AbletonGPTKitLoader(remote),
    )
    candidate = build_candidate(assemble_brief(extract_explicit("ダブテクノ、32小節"), _intent()), seed=4)
    runtime._store(candidate)
    return runtime, candidate, transport


def test_dub_techno_leaves_drum_tracks_empty_and_loads_kits_after_apply(tmp_path: Path) -> None:
    remote = FakeRemoteScript()
    runtime, candidate, transport = _runtime(tmp_path, remote)
    preview = runtime.apply_preview(candidate.candidate_id)
    assert preview["summary"]["kits"] == ["Hats", "Kick"]
    assert any("AbletonGPT 経由" in w for w in preview["warnings"])
    result = runtime.apply(candidate.candidate_id, confirmed=True)
    assert result["ok"] is True, result
    kick = next(t for t in transport.live_set.tracks if "KIHACHI Kick" in t["name"])
    assert kick["device_names"] == []  # KIHACHI left it for the kit
    assert {item["part"]: item["kit"] for item in result["kits"]} == {
        "Hats": "909 Core Kit", "Kick": "909 Core Kit"
    }
    loaded = sorted(p["track_index"] for c, p in remote.calls if c == "load_preset")
    kihachi = sorted(t["index"] for t in transport.live_set.tracks if "Kick" in t["name"] or "Hats" in t["name"])
    assert loaded == kihachi


def test_without_abletongpt_the_drum_rack_path_is_kept(tmp_path: Path) -> None:
    remote = FakeRemoteScript()
    remote.online = False
    runtime, candidate, transport = _runtime(tmp_path, remote)
    preview = runtime.apply_preview(candidate.candidate_id)
    assert preview["summary"]["kits"] == []
    assert any("接続できないため" in w for w in preview["warnings"])
    result = runtime.apply(candidate.candidate_id, confirmed=True)
    assert result["ok"] is True
    kick = next(t for t in transport.live_set.tracks if "KIHACHI Kick" in t["name"])
    assert kick["device_names"] == ["Drum Rack"]
    assert result["kits"] == []


def test_a_failed_kit_load_is_reported_and_not_retried(tmp_path: Path) -> None:
    remote = FakeRemoteScript(loads_wrong=True)
    runtime, candidate, _transport = _runtime(tmp_path, remote)
    result = runtime.apply(candidate.candidate_id, confirmed=True)
    assert result["ok"] is False
    assert result["receipt"]["status"] == "verified"
    assert all(item["ok"] is False for item in result["kits"])
    assert sum(1 for c in remote.calls if c[0] == "load_preset") == 2  # once per track
