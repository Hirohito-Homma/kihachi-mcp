"""Variety changes musical content while preserving explicit requests and replay."""

from itertools import pairwise

import pytest

from kihachi_mcp.models.production_brief import ProductionBrief
from kihachi_mcp.services.midi_candidate_builder import build_candidate
from kihachi_mcp.services.studio_interpreter import interpret_brief_offline
from kihachi_mcp.services.studio_runtime import StudioRuntime
from kihachi_mcp.services.studio_variety import diversify_brief, variety_signature


def musical_shape(candidate):
    return [
        (
            c.part,
            c.start_bar,
            [(n.pitch, n.start_beats, n.duration_beats) for n in c.notes],
        )
        for c in candidate.clips
    ]


def test_fixed_requests_survive_randomization():
    base = interpret_brief_offline(
        "Tech House 125 BPM Dm 32小節。17小節目からドロップにして。前半はハットを少なく。swing 54%"
    )
    for seed in range(12):
        varied = diversify_brief(base, seed)
        for field in (
            "genre",
            "tempo",
            "key",
            "bars",
            "drop_start_bar",
            "hats_first_half",
        ):
            assert getattr(varied, field) == getattr(base, field)
        assert varied.sections == base.sections
        from random import Random

        from kihachi_mcp.services.midi_candidate_builder import _SongPlan

        plan = _SongPlan(varied, 32, 4, Random(seed), (0, 3, 5, 4))
        assert plan.swing == 0.54


def test_same_genre_has_distinct_note_patterns_and_no_immediate_profile_repeat():
    base = interpret_brief_offline("Tech House 120 BPM Dm 32小節")
    history, shapes, profiles = [], set(), []
    for seed in range(12):
        varied = diversify_brief(base, seed, history)
        shape = repr(musical_shape(build_candidate(varied, seed=seed)))
        shapes.add(shape)
        profiles.append(varied.variation_profile)
        history.append(variety_signature(varied))
    assert len(shapes) == 12
    assert len(set(profiles)) >= 3
    assert all(a != b for a, b in pairwise(profiles))


def test_saved_brief_replays_exact_notes():
    varied = diversify_brief(interpret_brief_offline("32小節の曲"), 42)
    restored = ProductionBrief.from_dict(varied.to_dict())
    assert musical_shape(build_candidate(varied, seed=42)) == musical_shape(
        build_candidate(restored, seed=42)
    )
    assert varied == diversify_brief(interpret_brief_offline("32小節の曲"), 42)


@pytest.mark.parametrize("bars", [4, 12, 28, 32, 96, 256])
def test_form_exactly_covers_requested_length(bars):
    base = interpret_brief_offline(f"{bars}小節の曲")
    for seed in range(4):
        varied = diversify_brief(base, seed)
        assert varied.sections[0].start_bar == 1
        assert sum(s.length_bars for s in varied.sections) == bars
        assert varied.sections[-1].end_bar == bars


def test_runtime_auto_seed_history_restart_and_legacy_opt_out(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "kihachi_mcp.services.studio_runtime.provider_from_settings",
        lambda settings: object(),
    )
    runtime = StudioRuntime(candidate_dir=tmp_path, start_bridge=False)
    runtime._settings["ai_provider"] = "deterministic"
    first = runtime.generate("32小節の曲", wide_variety=True)
    assert first["ok"], first
    history = runtime._variety_history.copy()
    restarted = StudioRuntime(candidate_dir=tmp_path, start_bridge=False)
    restarted._settings["ai_provider"] = "deterministic"
    assert restarted._variety_history == history
    second = restarted.regenerate(first["candidate"]["candidate_id"], wide_variety=True)
    assert second["ok"], second
    assert first["candidate"]["seed"] != second["candidate"]["seed"]
    assert (
        first["candidate"]["brief"]["variation_profile"]
        != second["candidate"]["brief"]["variation_profile"]
    )
    fixed = restarted.generate("32小節の曲", seed=42, wide_variety=False)
    assert fixed["ok"], fixed
    assert "variation_profile" not in fixed["candidate"]["brief"]


def test_invalid_profile_rejected():
    data = interpret_brief_offline("32小節の曲").to_dict()
    data["variation_profile"] = "arbitrary"
    with pytest.raises(ValueError, match="variation_profile"):
        ProductionBrief.from_dict(data)
