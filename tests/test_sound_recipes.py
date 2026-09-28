from pathlib import Path

import pytest
from live_fixtures import gate

from kihachi_mcp.knowledge.sound_recipes import (
    DUB_TECHNO,
    MUTATION_FUNK,
    RECIPES,
    Setting,
    recipe_for,
    tuned,
)
from kihachi_mcp.services.brief_parser import extract_explicit
from kihachi_mcp.services.live_device_catalog import STOCK_DEVICE_NAMES
from kihachi_mcp.services.live_execution_service import LiveExecutionService
from kihachi_mcp.services.live_state_inspector import LiveStateInspector
from kihachi_mcp.services.live_transport_fake import FakeLiveSet, FakeLiveTransport
from kihachi_mcp.services.midi_candidate_builder import build_candidate
from kihachi_mcp.services.studio_interpreter import assemble_brief
from kihachi_mcp.services.studio_runtime import StudioRuntime


def _intent():
    return {
        "genre": "tech_house",
        "tempo": 120,
        "bars": 32,
        "key": "Dm",
        "mood": "",
        "hats_first_half": "normal",
        "hats_second_half": "normal",
        "bass_register": "mid",
        "note_density": "normal",
        "drop_start_bar": 0,
        "unhandled": [],
        "ambiguous": [],
    }


def _apply(tmp_path: Path, text: str):
    transport = FakeLiveTransport(FakeLiveSet(live_version="12.4.5", tempo=120))
    inspector = LiveStateInspector(transport)
    approvals = gate(tmp_path)
    runtime = StudioRuntime(
        transport=transport,
        inspector=inspector,
        executor=LiveExecutionService(
            transport=transport, inspector=inspector, approval_gate=approvals
        ),
        gate=approvals,
    )
    candidate = build_candidate(assemble_brief(extract_explicit(text), _intent()), seed=4)
    runtime._store(candidate)
    return runtime.apply(candidate.candidate_id, confirmed=True), transport


def _track(transport, part):
    return next(t for t in transport.live_set.tracks if f"KIHACHI {part} " in t["name"])


def test_every_recipe_device_is_a_catalogued_stock_device() -> None:
    for recipe in RECIPES.values():
        for part in recipe.parts.values():
            for device in part.chain:
                assert device.device in STOCK_DEVICE_NAMES


def test_every_tone_step_turns_a_knob_the_recipe_sets() -> None:
    for recipe in RECIPES.values():
        for steps in recipe.tone.values():
            for step in steps:
                device = next(
                    d for d in recipe.parts[step.part].chain if d.device == step.device
                )
                assert any(
                    s.parameter == step.parameter and s.value is not None
                    for s in device.settings
                ), step


def test_a_setting_is_either_a_value_or_an_item() -> None:
    with pytest.raises(ValueError):
        Setting("Flt 1 Freq")
    with pytest.raises(ValueError):
        Setting("Flt 1 Freq", value=0.5, item="On")
    with pytest.raises(ValueError):
        Setting("Flt 1 Freq", value=1.5)


def test_tone_steps_move_only_their_knobs_and_stay_in_range() -> None:
    brighter = tuned(DUB_TECHNO, {"brightness": 2})
    stab = {s.parameter: s for s in brighter.parts["Stab"].instrument.settings}
    assert stab["Flt 1 Freq"].value == pytest.approx(0.58 + 0.12)
    assert stab["Amp Decay"].value == pytest.approx(0.33)
    darkest = tuned(DUB_TECHNO, {"brightness": -9})  # clamped to -2 steps
    bass = {s.parameter: s for s in darkest.parts["Bass"].instrument.settings}
    assert bass["F1 Freq"].value == pytest.approx(0.4 - 0.08)
    wet = tuned(DUB_TECHNO, {"delay": 2}).parts["Stab"].effects[0].settings
    assert all(0.0 <= s.value <= 1.0 for s in wet if s.value is not None)
    with pytest.raises(ValueError):
        tuned(DUB_TECHNO, {"warmth": 1})


def test_dub_techno_applies_its_recipe_by_parameter_name(tmp_path: Path) -> None:
    result, transport = _apply(tmp_path, "ダブテクノ、32小節")
    assert result["ok"] is True, result.get("error")
    bass = _track(transport, "Bass")
    stab = _track(transport, "Stab")
    assert bass["device_names"] == ["Analog"]
    assert stab["device_names"] == ["Wavetable", "Echo"]
    assert bass["device_parameters"]["0"]["Voices"] == "Mono"
    assert stab["device_parameters"]["0"]["Flt 1 Freq"] == 0.58
    assert stab["device_parameters"]["1"]["Channel Mode"] == "Ping Pong"
    assert stab["device_parameters"]["1"]["Dry Wet"] == 0.35


def test_mutation_funk_uses_distinct_short_bass_and_stab_settings(tmp_path: Path) -> None:
    assert recipe_for("mutation_funk") is MUTATION_FUNK
    result, transport = _apply(tmp_path, "Mutation Funk、32小節")
    assert result["ok"] is True, result.get("error")
    bass = _track(transport, "Bass")
    stab = _track(transport, "Stab")
    lead = _track(transport, "Lead")
    kick = _track(transport, "Kick")
    assert bass["device_names"] == ["Analog"]
    assert stab["device_names"] == ["Wavetable"]
    assert lead["device_names"] == ["Wavetable"]
    assert lead["device_parameters"]["0"]["Flt 1 Freq"] == 0.78
    assert any(pad.get("name") == "kihachi-kick-deep" for pad in kick.get("occupied_pads", []))
    assert bass["device_parameters"]["0"]["F1 Freq"] == 0.52
    assert stab["device_parameters"]["0"]["Amp Decay"] == 0.22
    assert stab["device_parameters"]["0"]["Flt 1 Freq"] != 0.58


def test_a_genre_without_a_recipe_keeps_the_stock_instruments(tmp_path: Path) -> None:
    assert recipe_for("tech_house") is None
    result, transport = _apply(tmp_path, "テックハウス、32小節")
    assert result["ok"] is True
    assert _track(transport, "Bass")["device_names"] == ["Operator"]
    assert _track(transport, "Stab")["device_names"] == ["Wavetable"]
    assert "device_parameters" not in _track(transport, "Stab")


def test_tone_words_reach_the_recipe_knobs(tmp_path: Path) -> None:
    result, transport = _apply(tmp_path, "ダブテクノ、32小節。煌びやかな音で、ディレイ多めに")
    assert result["ok"] is True
    stab = _track(transport, "Stab")
    assert stab["device_parameters"]["0"]["Flt 1 Freq"] == pytest.approx(0.64)
    assert stab["device_parameters"]["1"]["Dry Wet"] == pytest.approx(0.43)


def test_an_older_model_reply_without_tone_fields_is_accepted() -> None:
    from kihachi_mcp.services.studio_interpreter import validate_model_intent

    intent = validate_model_intent(_intent())
    assert intent["tone_brightness"] == 0


def test_a_candidate_saved_before_tone_fields_still_loads() -> None:
    from kihachi_mcp.models.production_brief import ProductionBrief

    brief = assemble_brief(extract_explicit("ダブテクノ、32小節"), _intent())
    data = brief.to_dict()
    for name in ("tone_brightness", "tone_length", "tone_delay"):
        data.pop(name)
    assert ProductionBrief.from_dict(data).tone_brightness.value == 0


def test_tone_steps_without_a_recipe_are_reported_as_unused() -> None:
    brief = assemble_brief(extract_explicit("テックハウス、32小節、煌びやかに"), _intent())
    assert brief.tone_brightness.value == 1
    assert any("レシピがない" in note for note in brief.interpretations)
