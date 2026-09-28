from live_fixtures import executor, gate, snapshot_of
from test_candidate_live_planner import _candidate

from kihachi_mcp.knowledge.part_sounds import PART_EFFECTS, PART_INSTRUMENTS, at
from kihachi_mcp.models.live_contract import (
    OP_LOAD_LIVE_DEVICE,
    OP_SET_DEVICE_PARAMETER,
)
from kihachi_mcp.models.production_brief import PART_ORDER
from kihachi_mcp.services import live_device_catalog
from kihachi_mcp.services.candidate_live_planner import CandidateLivePlanner
from kihachi_mcp.services.device_probe import load_parameters
from kihachi_mcp.services.live_transport_fake import FakeLiveSet, FakeLiveTransport


def _run(transport, plan):
    approval = gate()
    token = approval.approve(plan)
    return executor(transport, approval).execute(plan, approved=True, approval_token=token)


def test_every_setting_names_a_parameter_live_reported() -> None:
    devices = load_parameters()["devices"]
    recipes = [device for chain in PART_EFFECTS.values() for device in chain]
    recipes += list(PART_INSTRUMENTS.values())
    for recipe in recipes:
        known = {item["name"]: item for item in devices[recipe.device]["parameters"]}
        for setting in recipe.settings:
            parameter = known[setting.parameter]
            if setting.item is not None:
                assert setting.item in parameter["value_items"]
            else:
                assert 0.0 <= setting.value <= 1.0


def test_dial_readings_convert_to_the_positions_live_showed() -> None:
    assert at("Compressor", "Threshold", -18).value == 0.4
    assert at("Hybrid Reverb", "Decay", 2320).value == 0.4
    # 30 Hz on the logarithmic EQ dial falls between 21.6 Hz (0.1) and 46.6 Hz (0.2).
    assert 0.14 < at("EQ Eight", "1 Frequency A", 30).value < 0.15


def test_every_part_has_an_effect_chain_and_patches_match_the_instrument() -> None:
    assert set(PART_EFFECTS) == set(PART_ORDER)
    for part, patch in PART_INSTRUMENTS.items():
        assert patch.device == live_device_catalog.suggest_instrument(part)


def test_effects_append_after_the_instrument_and_a_second_run_adds_nothing() -> None:
    candidate = _candidate()
    transport = FakeLiveTransport(FakeLiveSet(live_version="12.4.5", tempo=125))
    planner = CandidateLivePlanner()
    assert _run(transport, planner.create_plan(candidate, snapshot_of(transport))).status == "verified"
    before = {track["name"]: list(track["device_names"]) for track in transport.live_set.tracks}

    plan = planner.create_effects_plan(candidate, snapshot_of(transport))
    assert plan.conflicts == []
    assert {op.op for op in plan.operations} == {OP_LOAD_LIVE_DEVICE, OP_SET_DEVICE_PARAMETER}
    assert _run(transport, plan).status == "verified"

    for track in transport.live_set.tracks:
        part = track["name"].split()[1]
        kept = before[track["name"]]
        assert track["device_names"][: len(kept)] == kept
        added = track["device_names"][len(kept):]
        assert added == [d.device for d in PART_EFFECTS[part] if d.device not in kept]

    again = planner.create_effects_plan(candidate, snapshot_of(transport))
    assert again.operations == []


def test_effects_are_refused_while_live_plays() -> None:
    candidate = _candidate()
    transport = FakeLiveTransport(FakeLiveSet(live_version="12.4.5", is_playing=True))
    plan = CandidateLivePlanner().create_effects_plan(candidate, snapshot_of(transport))
    assert plan.status == "blocked"
    assert plan.operations == []
