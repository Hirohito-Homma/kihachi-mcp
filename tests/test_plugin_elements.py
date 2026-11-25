"""サードパーティプラグイン指定（elements -> load_plugin / set_plugin_param）のユニットテスト。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from live_fixtures import gate

from kihachi_mcp.models.live_contract import (
    OP_LOAD_PLUGIN,
    OP_SET_PLUGIN_PARAM,
    STATUS_PARTIALLY_APPLIED,
    STATUS_VERIFIED,
)
from kihachi_mcp.services.live_execution_service import LiveExecutionService
from kihachi_mcp.services.live_state_inspector import LiveStateInspector
from kihachi_mcp.services.live_transport_fake import FakeLiveSet, FakeLiveTransport
from kihachi_mcp.services.studio_runtime import StudioRuntime


class _FakeRemoteScript:
    """AbletonGPT Remote Script (9877) の挙動を模擬するテスト用フェイク。"""

    def __init__(
        self,
        *,
        polls_before_appear: int = 1,
        never_appear: bool = False,
        loaded_device_name: str = "EZbass",
        user_library_tree: dict[tuple[str, ...], list[dict[str, Any]]] | None = None,
    ) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.polls_before_appear = polls_before_appear
        self.never_appear = never_appear
        self.loaded_device_name = loaded_device_name
        self.user_library_tree = user_library_tree or {}
        self._load_called = False
        self._poll_count = 0
        self._params: list[dict[str, Any]] = [
            {"index": 0, "name": "Device On", "value": 1.0, "display_value": "On"},
            {"index": 4, "name": "4. Bass Control - High", "value": 0.82, "display_value": "82.0%"},
            {"index": 5, "name": "5. Comp - Threshold", "value": 0.5, "display_value": "50.0%"},
        ]

    def __call__(self, command: str, timeout: float | None = None, **params: Any) -> Any:
        self.calls.append((command, dict(params)))
        if command == "browse_presets":
            path = tuple(params.get("path") or [])
            return {"items": self.user_library_tree.get(path, [])}
        if command == "load_preset":
            self._load_called = True
            self._poll_count = 0
            return {"loaded": True}
        if command == "get_track_devices":
            if not self._load_called or self.never_appear:
                return {"devices": []}
            self._poll_count += 1
            if self._poll_count < self.polls_before_appear:
                return {"devices": []}
            return {
                "devices": [
                    {
                        "index": 0,
                        "name": self.loaded_device_name,
                        "class_name": "PluginDevice",
                        "type": 1,
                        "parameters": [dict(p) for p in self._params],
                    }
                ]
            }
        if command == "set_device_parameter":
            p_idx = int(params["parameter_index"])
            val = float(params["value"])
            for p in self._params:
                if int(p["index"]) == p_idx:
                    p["value"] = val
                    p["display_value"] = f"{val * 100:.1f}%"
            return {"ok": True}
        raise AssertionError(f"unexpected remote command: {command}")


def _studio_with_remote(
    tmp_path: Path,
    remote: _FakeRemoteScript | None,
    *,
    timeout_seconds: float = 30.0,
) -> tuple[StudioRuntime, FakeLiveSet]:
    live = FakeLiveSet(live_version="12.4.5", tempo=124.0)
    transport = FakeLiveTransport(live)
    inspector = LiveStateInspector(transport)
    approvals = gate(tmp_path)
    clock_val = [0.0]

    def fake_clock() -> float:
        return clock_val[0]

    def fake_sleep(seconds: float) -> None:
        clock_val[0] += seconds

    executor = LiveExecutionService(
        transport=transport,
        inspector=inspector,
        approval_gate=approvals,
        plugin_remote=remote,
        plugin_timeout_seconds=timeout_seconds,
        plugin_poll_interval=0.5,
        clock=fake_clock,
        sleeper=fake_sleep,
    )
    runtime = StudioRuntime(
        transport=transport,
        inspector=inspector,
        executor=executor,
        gate=approvals,
        export_dir=tmp_path / "exports",
        candidate_dir=tmp_path / "candidates",
    )
    runtime.update_settings({"ai_provider": "deterministic"}, persist=False)
    return runtime, live


def _make_candidate_with_elements(
    runtime: StudioRuntime, elements: dict[str, Any] | None
) -> str:
    parent_id = runtime.generate("Mutation Funk、124 BPM、Gm、16小節", seed=3)["candidate"][
        "candidate_id"
    ]
    if not elements:
        return parent_id
    proposal = runtime.propose_composer_adoption(
        parent_id,
        "bass",
        4,
        [{"pitch": 43, "start": 0.0, "duration": 0.25, "velocity": 100}],
        section_name="Drop",
        elements=elements,
    )
    assert proposal["ok"] is True
    return runtime.decide_revision(proposal["revision"]["revision_id"], True)[
        "selected_candidate_id"
    ]


def test_plugin_plan_backward_compatibility(tmp_path: Path) -> None:
    """plugin 指定のない候補は従来と完全に同一の plan / op 列になること。"""
    runtime, _ = _studio_with_remote(tmp_path, _FakeRemoteScript())
    plain_id = _make_candidate_with_elements(runtime, None)
    preview = runtime.apply_preview(plain_id)
    assert preview["ok"] is True
    ops = [op["op"] for op in preview["plan"]["operations"]]
    assert OP_LOAD_PLUGIN not in ops
    assert OP_SET_PLUGIN_PARAM not in ops
    assert preview["summary"]["planned_devices"]["Bass"] == {"device": "Analog"}


def test_plugin_load_raw_and_dynamic_param_resolution_success(tmp_path: Path) -> None:
    """raw プラグインの非同期ポーリングロード・動的パラメータ解決・レシート読み戻しを検証する。"""
    remote = _FakeRemoteScript(polls_before_appear=2, loaded_device_name="EZbass")
    runtime, _ = _studio_with_remote(tmp_path, remote)
    cid = _make_candidate_with_elements(
        runtime,
        {
            "bass": {
                "instrument": "plugin",
                "source": "raw",
                "name": "EZbass",
                "path": ["VST3", "Toontrack"],
                "params": {"4. Bass Control - High": 0.6},
            }
        },
    )

    preview = runtime.apply_preview(cid)
    assert preview["ok"] is True
    assert preview["summary"]["planned_devices"]["Bass"] == {
        "device": "EZbass",
        "source": "raw",
        "plugin": True,
    }
    plan_ops = preview["plan"]["operations"]
    load_ops = [op for op in plan_ops if op["op"] == OP_LOAD_PLUGIN]
    param_ops = [op for op in plan_ops if op["op"] == OP_SET_PLUGIN_PARAM]
    assert len(load_ops) == 1
    assert load_ops[0]["arguments"]["source"] == "raw"
    assert load_ops[0]["arguments"]["path"] == ["VST3", "Toontrack"]
    assert len(param_ops) == 1
    assert param_ops[0]["arguments"]["param_name"] == "4. Bass Control - High"

    result = runtime.apply(cid, confirmed=True)
    assert result["ok"] is True
    assert result["receipt"]["status"] == STATUS_VERIFIED
    assert result["warnings"] == []
    assert len(result["plugin_devices"]) == 1
    pdev = result["plugin_devices"][0]
    assert pdev["device_name"] == "EZbass"
    assert pdev["params"] == [
        {
            "param_name": "4. Bass Control - High",
            "parameter_index": 4,
            "value": 0.6,
            "display_value": "60.0%",
        }
    ]
    # load_preset が plugins カテゴリで呼ばれたこと
    load_call = next(c for c in remote.calls if c[0] == "load_preset")
    assert load_call[1]["category"] == "plugins"
    assert load_call[1]["path"] == ["VST3", "Toontrack"]
    assert load_call[1]["name"] == "EZbass"


def test_plugin_load_preset_source_uses_user_library(tmp_path: Path) -> None:
    """source='preset' が path なしで user_library から .adv を探索・ロードできること。"""
    remote = _FakeRemoteScript(
        polls_before_appear=1,
        loaded_device_name="EZbass",
        user_library_tree={
            (): [{"name": "Presets", "is_folder": True, "is_loadable": False}],
            ("Presets",): [{"name": "SlapEZ.adv", "is_folder": False, "is_loadable": True}],
        },
    )
    runtime, _ = _studio_with_remote(tmp_path, remote)
    cid = _make_candidate_with_elements(
        runtime,
        {
            "bass": {
                "instrument": "plugin",
                "source": "preset",
                "name": "SlapEZ.adv",
                "params": {"5. Comp - Threshold": 0.4},
            }
        },
    )

    result = runtime.apply(cid, confirmed=True)
    assert result["ok"] is True
    assert result["receipt"]["status"] == STATUS_VERIFIED
    load_call = next(c for c in remote.calls if c[0] == "load_preset")
    assert load_call[1]["category"] == "user_library"
    assert load_call[1]["path"] == ["Presets"]
    assert load_call[1]["name"] == "SlapEZ.adv"


def test_plugin_load_timeout_returns_partial_failure_receipt(tmp_path: Path) -> None:
    """30秒タイムアウト時に標準デバイスへ差し替えず部分失敗レシートを返すこと。"""
    remote = _FakeRemoteScript(never_appear=True)
    runtime, live = _studio_with_remote(tmp_path, remote, timeout_seconds=1.5)
    cid = _make_candidate_with_elements(
        runtime,
        {
            "bass": {
                "instrument": "plugin",
                "source": "raw",
                "name": "EZbass",
                "path": ["VST3", "Toontrack"],
            }
        },
    )

    result = runtime.apply(cid, confirmed=True)
    assert result["ok"] is False
    assert result["partial"] is True
    assert result["receipt"]["status"] == STATUS_PARTIALLY_APPLIED
    assert "load_plugin" in result["receipt"]["failed_operation"]
    assert "timed out" in result["receipt"]["error"]
    # 標準デバイスへ差し替えられていないこと
    bass_track = next(t for t in live.tracks if "Bass" in t["name"])
    assert bass_track["device_names"] == []


def test_plugin_param_name_mismatch_records_warning_in_receipt(tmp_path: Path) -> None:
    """存在しないパラメータ名はエラー停止せずレシートに警告として残すこと。"""
    remote = _FakeRemoteScript(polls_before_appear=1, loaded_device_name="EZbass")
    runtime, _ = _studio_with_remote(tmp_path, remote)
    cid = _make_candidate_with_elements(
        runtime,
        {
            "bass": {
                "instrument": "plugin",
                "source": "raw",
                "name": "EZbass",
                "path": ["VST3", "Toontrack"],
                "params": {
                    "4. Bass Control - High": 0.7,
                    "NonExistent Knob": 0.5,
                },
            }
        },
    )

    result = runtime.apply(cid, confirmed=True)
    assert result["ok"] is True
    assert result["receipt"]["status"] == STATUS_VERIFIED
    assert any("NonExistent Knob" in w for w in result["warnings"])
    readbacks = result["receipt"]["readback"]
    warn_rb = next(
        r for r in readbacks
        if r["op"] == OP_SET_PLUGIN_PARAM and r["observed"].get("param_name") == "NonExistent Knob"
    )
    assert warn_rb["observed"]["matched"] is False
    assert "NonExistent Knob" in warn_rb["observed"]["warning"]


def test_plugin_invalid_spec_or_unavailable_blocks_preview_without_fallback(tmp_path: Path) -> None:
    """不正なプラグイン指定や AbletonGPT 未接続時はプレビューをブロックし代替しないこと。"""
    # 1. raw で path なし
    runtime, _ = _studio_with_remote(tmp_path, _FakeRemoteScript())
    bad_id = _make_candidate_with_elements(
        runtime,
        {
            "bass": {
                "instrument": "plugin",
                "source": "raw",
                "name": "EZbass",
            }
        },
    )
    preview_bad = runtime.apply_preview(bad_id)
    assert preview_bad["ok"] is False
    assert preview_bad["status"] == "blocked"
    assert any(c["kind"] == "plugin_unavailable" for c in preview_bad["conflicts"])

    # 2. AbletonGPT 未接続（plugin_remote=None, kit_loader=None）
    runtime_no_gpt, _ = _studio_with_remote(tmp_path, None)
    valid_id = _make_candidate_with_elements(
        runtime_no_gpt,
        {
            "bass": {
                "instrument": "plugin",
                "source": "raw",
                "name": "EZbass",
                "path": ["VST3", "Toontrack"],
            }
        },
    )
    preview_no_gpt = runtime_no_gpt.apply_preview(valid_id)
    assert preview_no_gpt["ok"] is False
    assert preview_no_gpt["status"] == "blocked"
    assert any(c["kind"] == "plugin_loader_unavailable" for c in preview_no_gpt["conflicts"])


def test_plugin_raw_vital_with_params_emits_preset_recommendation_warning(
    tmp_path: Path,
) -> None:
    """Vital を source='raw' かつ Device On 以外の params 付きで計画すると preset 推奨警告が出ること。"""
    runtime, _ = _studio_with_remote(tmp_path, _FakeRemoteScript(loaded_device_name="Vital"))
    cid = _make_candidate_with_elements(
        runtime,
        {
            "pad": {
                "instrument": "plugin",
                "source": "raw",
                "name": "Vital",
                "path": ["VST3", "Vital Audio"],
                "params": {"Filter 1 Cutoff": 0.55},
            }
        },
    )
    preview = runtime.apply_preview(cid)
    assert preview["ok"] is True
    assert any("source='preset'" in w and "Vital" in w for w in preview["warnings"])
