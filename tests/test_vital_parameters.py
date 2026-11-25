"""Vitalホスト公開パラメータの安全な変更計画を検証する。"""

from live_fixtures import snapshot_of

from kihachi_mcp.knowledge.vital_macros import (
    VITAL_MACRO_NAMES,
    estimate_vital_macros,
)
from kihachi_mcp.models.live_contract import (
    OP_SET_DEVICE_PARAMETER,
    managed_track_name,
)
from kihachi_mcp.services.live_transport_fake import FakeLiveSet, FakeLiveTransport
from kihachi_mcp.services.vital_parameters import (
    create_vital_batch_plan,
    create_vital_parameter_plan,
)


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


def _macro_params(values: dict[str, float]) -> list[dict]:
    return [
        {
            "name": name,
            "min": 0.0,
            "max": 1.0,
            "value": val,
            "is_quantized": False,
            "value_items": [],
        }
        for name, val in values.items()
    ]


def test_estimate_vital_macros_combines_all_five_axes_and_clamps() -> None:
    base = estimate_vital_macros(genre="mutation_funk", part="Bass")
    assert set(base["values"].keys()) == set(VITAL_MACRO_NAMES)
    assert all(0.0 <= v <= 1.0 for v in base["values"].values())

    bright_slap = estimate_vital_macros(
        genre="mutation_funk",
        part="Bass",
        mood="aggressive",
        tone={"brightness": 2, "grit": 2, "length": -2},
        elements={"slap": True, "octave_pops": True},
    )
    assert bright_slap["values"]["Macro 1"] > base["values"]["Macro 1"]
    assert bright_slap["values"]["Macro 4"] > base["values"]["Macro 4"]
    assert bright_slap["values"]["Macro 2"] < base["values"]["Macro 2"]
    assert all(0.0 <= v <= 1.0 for v in bright_slap["values"].values())
    assert any("elements.slap" in r for r in bright_slap["reasons"])
    assert any("tone.brightness=+2" in r for r in bright_slap["reasons"])

    # 日本語ムードと未知入力でも安全にフォールバックする
    dark_pad = estimate_vital_macros(
        genre="unknown_genre",
        part="Pad",
        mood="暗い",
        elements={"pad": {"sustain_stabs": True}},
    )
    assert dark_pad["values"]["Macro 1"] < 0.45
    assert dark_pad["values"]["Macro 2"] >= 0.9


def test_batch_plan_updates_exposed_macros_and_skips_unexposed() -> None:
    params = _macro_params({"Device On": 1.0, "Macro 1": 0.1, "Macro 3": 0.2})
    plan, details = create_vital_batch_plan(
        _snapshot(),
        track_index=0,
        device_index=0,
        device_name="Vital",
        parameters=params,
        target_values={"Macro 1": 0.72, "Macro 2": 0.40, "Macro 3": 0.55, "Macro 4": 0.80},
    )

    assert plan.status == "approval_required"
    assert len(plan.operations) == 2
    assert [op.arguments["parameter_name"] for op in plan.operations] == [
        "Macro 1",
        "Macro 3",
    ]
    assert details["skipped_unexposed"] == ["Macro 2", "Macro 4"]
    assert any("未公開のためスキップ" in w for w in plan.warnings)


def test_batch_plan_blocks_when_no_macros_exposed_or_all_unchanged() -> None:
    unexposed_plan, _ = create_vital_batch_plan(
        _snapshot(),
        track_index=0,
        device_index=0,
        device_name="Vital",
        parameters=_macro_params({"Device On": 1.0}),
        target_values={"Macro 1": 0.5, "Macro 2": 0.5},
    )
    assert unexposed_plan.status == "blocked"
    assert unexposed_plan.conflicts[0].kind == "no_exposed_macros"

    noop_plan, details = create_vital_batch_plan(
        _snapshot(),
        track_index=0,
        device_index=0,
        device_name="Vital",
        parameters=_macro_params({"Macro 1": 0.5, "Macro 2": 0.6}),
        target_values={"Macro 1": 0.5, "Macro 2": 0.6},
    )
    assert noop_plan.status == "blocked"
    assert noop_plan.conflicts[0].kind == "unchanged_parameter"
    assert len(details["skipped_unchanged"]) == 2


def test_batch_plan_rejects_unmanaged_track_and_duplicate_parameters() -> None:
    unmanaged, _ = create_vital_batch_plan(
        _snapshot(managed=False),
        track_index=0,
        device_index=0,
        device_name="Vital",
        parameters=_macro_params({"Macro 1": 0.1}),
        target_values={"Macro 1": 0.8},
    )
    assert unmanaged.status == "blocked"
    assert {c.kind for c in unmanaged.conflicts} >= {"unmanaged_track"}

    dup_params = _macro_params({"Macro 1": 0.1}) + _macro_params({"Macro 1": 0.2})
    dup_plan, _ = create_vital_batch_plan(
        _snapshot(),
        track_index=0,
        device_index=0,
        device_name="Vital",
        parameters=dup_params,
        target_values={"Macro 1": 0.8},
    )
    assert dup_plan.status == "blocked"
    assert {c.kind for c in dup_plan.conflicts} >= {"duplicate_parameter"}
