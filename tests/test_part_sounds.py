from live_fixtures import executor, gate, snapshot_of
from test_candidate_live_planner import _candidate

from kihachi_mcp.knowledge.part_sounds import (
    MASTER_CHAIN,
    PART_EFFECTS,
    PART_INSTRUMENTS,
    PART_MIX,
    SIDECHAIN_DUCKING,
    at,
    step,
)
from kihachi_mcp.models.live_contract import (
    OP_LOAD_LIVE_DEVICE,
    OP_SET_DEVICE_PARAMETER,
    OP_SET_SIDECHAIN_SOURCE,
    OP_SET_TRACK_MIXER,
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


def test_mix_sets_only_fader_and_pan_on_the_candidate_tracks() -> None:
    candidate = _candidate()
    transport = FakeLiveTransport(FakeLiveSet(live_version="12.4.5", tempo=125))
    planner = CandidateLivePlanner()
    assert _run(transport, planner.create_plan(candidate, snapshot_of(transport))).status == "verified"
    devices = {track["name"]: list(track["device_names"]) for track in transport.live_set.tracks}

    plan = planner.create_mix_plan(candidate, snapshot_of(transport))
    assert {op.op for op in plan.operations} == {OP_SET_TRACK_MIXER}
    assert len(plan.operations) == len(candidate.parts)
    assert _run(transport, plan).status == "verified"

    for track in transport.live_set.tracks:
        part = track["name"].split()[1]
        volume_db, panning = PART_MIX[part]
        assert track["mixer"] == {"volume_db": volume_db, "panning": panning}
        assert track["device_names"] == devices[track["name"]]


def test_the_kick_leads_and_low_parts_stay_centred() -> None:
    assert max(PART_MIX.values())[0] == PART_MIX["Kick"][0]
    for part in ("Kick", "Sub", "Bass"):
        assert PART_MIX[part][1] == 0.0


def test_master_chain_goes_after_existing_master_devices_and_keeps_them() -> None:
    live = FakeLiveSet(live_version="12.4.5")
    live.master["device_names"] = ["Spectrum"]
    transport = FakeLiveTransport(live)
    planner = CandidateLivePlanner()

    plan = planner.create_master_plan(snapshot_of(transport))
    loads = [op for op in plan.operations if op.op == OP_LOAD_LIVE_DEVICE]
    assert [op.arguments["device_name"] for op in loads] == [r.device for r in MASTER_CHAIN]
    assert all(op.target == {"master": True} for op in loads)
    assert loads[0].expected_readback["device_index"] == 1
    assert _run(transport, plan).status == "verified"
    assert live.master["device_names"] == ["Spectrum", *[r.device for r in MASTER_CHAIN]]
    assert all(track.get("mixer") is None for track in live.tracks)

    assert planner.create_master_plan(snapshot_of(transport)).operations == []


def test_stepped_knobs_land_on_the_intended_step() -> None:
    """Live 12.4.5 floored Glue Attack 0.8333 (4.9998 of 0..6) to 3 ms, not 10 ms."""
    attack = step("Glue Compressor", "Attack", 5)
    assert attack.expected == 0.8333
    assert int(attack.value * 6) == 5
    assert step("Glue Compressor", "Release", 6).value == 1.0


def test_retune_resets_the_chain_it_added_and_refuses_anything_else() -> None:
    live = FakeLiveSet(live_version="12.4.5")
    transport = FakeLiveTransport(live)
    planner = CandidateLivePlanner()
    assert _run(transport, planner.create_master_plan(snapshot_of(transport))).status == "verified"
    retune = planner.create_master_plan(snapshot_of(transport), retune=True)
    assert {op.op for op in retune.operations} == {OP_SET_DEVICE_PARAMETER}
    assert _run(transport, retune).status == "verified"

    live.master["device_names"].append("Spectrum")
    refused = planner.create_master_plan(snapshot_of(transport), retune=True)
    assert refused.status == "blocked"


def test_part_retune_only_sets_knobs_on_the_chain_already_there() -> None:
    candidate = _candidate()
    transport = FakeLiveTransport(FakeLiveSet(live_version="12.4.5", tempo=125))
    planner = CandidateLivePlanner()
    assert _run(transport, planner.create_plan(candidate, snapshot_of(transport))).status == "verified"
    assert _run(transport, planner.create_effects_plan(candidate, snapshot_of(transport))).status == "verified"
    kick = next(t for t in transport.live_set.tracks if " Kick " in t["name"])
    devices = list(kick["device_names"])

    plan = planner.create_retune_plan(candidate, snapshot_of(transport), ("Kick",))
    assert {op.op for op in plan.operations} == {OP_SET_DEVICE_PARAMETER}
    assert "2 Gain A" in {op.arguments["parameter_name"] for op in plan.operations}
    assert _run(transport, plan).status == "verified"
    assert kick["device_names"] == devices

    kick["device_names"].remove("Saturator")
    refused = planner.create_retune_plan(candidate, snapshot_of(transport), ("Kick",))
    assert refused.status == "blocked"
    assert refused.operations == []


def test_sidechain_keys_a_new_compressor_from_the_kick_before_any_knob() -> None:
    candidate = _candidate()
    transport = FakeLiveTransport(FakeLiveSet(live_version="12.4.5", tempo=125))
    planner = CandidateLivePlanner()
    assert _run(transport, planner.create_plan(candidate, snapshot_of(transport))).status == "verified"
    before = {track["name"]: list(track["device_names"]) for track in transport.live_set.tracks}

    plan = planner.create_sidechain_plan(candidate, snapshot_of(transport))
    ducked = [p for p in candidate.parts if p in SIDECHAIN_DUCKING]
    assert ducked
    ops = [op.op for op in plan.operations]
    assert ops.count(OP_SET_SIDECHAIN_SOURCE) == len(ducked)
    for index, op in enumerate(ops):
        if op == OP_SET_SIDECHAIN_SOURCE:
            assert ops[index - 1] == OP_LOAD_LIVE_DEVICE
    assert _run(transport, plan).status == "verified"

    kick = next(t["name"] for t in transport.live_set.tracks if " Kick " in t["name"])
    for track in transport.live_set.tracks:
        part = track["name"].split()[1]
        kept = before[track["name"]]
        assert track["device_names"][: len(kept)] == kept
        if part in SIDECHAIN_DUCKING:
            assert track["device_names"][len(kept):] == ["Compressor"]
            assert track["sidechains"] == {str(len(kept)): kick}
        else:
            assert track["device_names"] == kept

    again = planner.create_sidechain_plan(candidate, snapshot_of(transport), frozenset(ducked))
    assert again.operations == []


def test_master_is_refused_while_live_records() -> None:
    transport = FakeLiveTransport(FakeLiveSet(live_version="12.4.5", is_recording=True))
    plan = CandidateLivePlanner().create_master_plan(snapshot_of(transport))
    assert plan.status == "blocked"
    assert plan.operations == []


def test_the_limiter_ends_the_chain_below_zero() -> None:
    limiter = MASTER_CHAIN[-1]
    assert limiter.device == "Limiter"
    ceiling = next(s for s in limiter.settings if s.parameter == "Ceiling")
    assert ceiling.value == 0.9  # -1.0 dB in Live's reading


def test_effects_are_refused_while_live_plays() -> None:
    candidate = _candidate()
    transport = FakeLiveTransport(FakeLiveSet(live_version="12.4.5", is_playing=True))
    plan = CandidateLivePlanner().create_effects_plan(candidate, snapshot_of(transport))
    assert plan.status == "blocked"
    assert plan.operations == []
