"""Vitalホスト公開パラメータの安全な変更計画を検証する。"""

from live_fixtures import snapshot_of

from kihachi_mcp.models.live_contract import (
    OP_SET_DEVICE_PARAMETER,
    managed_track_name,
)
from kihachi_mcp.services.live_transport_fake import FakeLiveSet, FakeLiveTransport
from kihachi_mcp.services.vital_parameters import create_vital_parameter_plan


def _snapshot(*, managed: bool = True):
    live = FakeLiveSet(live_version="12.4.3")
    name = managed_track_name("KIHACHI Bass deadbeef") if managed else "User Bass"
    track = live.add_track(name)
    track["device_names"] = ["Vital"]
    return snapshot_of(FakeLiveTransport(live))


def _cutoff() -> dict:
    return {
        "name": "Filter 1 Cutoff",
        "min": 0.0,
        "max": 1.0,
        "value": 0.25,
        "is_quantized": False,
        "value_items": [],
    }


def test_continuous_parameter_plan_targets_one_managed_vital() -> None:
    plan = create_vital_parameter_plan(
        _snapshot(),
        track_index=0,
        device_index=0,
        device_name="Vital",
        parameter=_cutoff(),
        value=0.75,
    )

    assert plan.status == "approval_required"
    assert len(plan.operations) == 1
    operation = plan.operations[0]
    assert operation.op == OP_SET_DEVICE_PARAMETER
    assert operation.target == {"track_index": 0, "device_index": 0}
    assert operation.arguments == {
        "device_name": "Vital",
        "parameter_name": "Filter 1 Cutoff",
        "value": 0.75,
    }
    assert operation.expected_readback["normalized_value"] == 0.75
    assert {row.kind for row in operation.preconditions} == {
        "not_recording",
        "track_name_at_index",
        "device_name_at_index",
    }


def test_quantized_parameter_plan_accepts_only_reported_items() -> None:
    parameter = {
        "name": "Oscillator Mode",
        "min": 0.0,
        "max": 2.0,
        "value": 0.0,
        "is_quantized": True,
        "value_items": ["Classic", "Wavetable", "Sample"],
    }
    accepted = create_vital_parameter_plan(
        _snapshot(),
        track_index=0,
        device_index=0,
        device_name="Vital",
        parameter=parameter,
        item="Wavetable",
    )
    rejected = create_vital_parameter_plan(
        _snapshot(),
        track_index=0,
        device_index=0,
        device_name="Vital",
        parameter=parameter,
        item="Unknown",
    )

    assert accepted.operations[0].arguments["item"] == "Wavetable"
    assert accepted.operations[0].expected_readback["item"] == "Wavetable"
    assert rejected.status == "blocked"
    assert rejected.conflicts[0].kind == "invalid_item"


def test_plan_rejects_unmanaged_track_non_vital_and_invalid_value() -> None:
    unmanaged = create_vital_parameter_plan(
        _snapshot(managed=False),
        track_index=0,
        device_index=0,
        device_name="Vital",
        parameter=_cutoff(),
        value=0.75,
    )
    non_vital = create_vital_parameter_plan(
        _snapshot(),
        track_index=0,
        device_index=0,
        device_name="Operator",
        parameter=_cutoff(),
        value=0.75,
    )
    invalid = create_vital_parameter_plan(
        _snapshot(),
        track_index=0,
        device_index=0,
        device_name="Vital",
        parameter=_cutoff(),
        value=float("nan"),
    )
    invalid_current = _cutoff()
    invalid_current["value"] = 2.0
    invalid_observation = create_vital_parameter_plan(
        _snapshot(),
        track_index=0,
        device_index=0,
        device_name="Vital",
        parameter=invalid_current,
        value=0.75,
    )

    assert {row.kind for row in unmanaged.conflicts} >= {"unmanaged_track"}
    assert {row.kind for row in non_vital.conflicts} >= {"not_vital"}
    assert {row.kind for row in invalid.conflicts} >= {"invalid_value"}
    assert {row.kind for row in invalid_observation.conflicts} >= {
        "invalid_parameter"
    }


def test_plan_rejects_current_value_as_noop() -> None:
    plan = create_vital_parameter_plan(
        _snapshot(),
        track_index=0,
        device_index=0,
        device_name="Vital",
        parameter=_cutoff(),
        value=0.25,
    )

    assert plan.status == "blocked"
    assert plan.conflicts[0].kind == "unchanged_parameter"
