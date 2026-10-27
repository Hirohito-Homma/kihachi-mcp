import json
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from kihachi_mcp.services.music_training_service import (
    TRAINING_THEMES,
    TrainingJournalEntry,
    TrainingJournalStore,
    generate_training_variants,
    promote_training_variant_to_candidate,
)
from kihachi_mcp.services.reference_analysis_card import (
    AnalysisCard,
    AnalysisCardStore,
)


def _make_dummy_card(card_id="card_techhouse_01") -> AnalysisCard:
    sample_analysis = {
        "ok": True,
        "segment": {
            "start_seconds": 0.0,
            "duration_seconds": 16.0,
            "sample_rate": 22050,
        },
        "bpm": {"value": 126.0, "confidence": 0.95, "unit": "BPM", "method": "autocorrelation", "status": "verified"},
        "kick_pattern": {"hit_count": 32, "interval_summary": "4-beat regular", "confidence": 0.98, "is_four_on_the_floor": True, "style": "four_on_the_floor"},
        "bass_groove": {"hit_count": 24, "rest_ratio": 0.42, "syncopation_ratio": 0.58, "density_notes_per_bar": 3.0, "confidence": 0.85},
        "energy_dynamics": {"trend": "stable", "confidence": 0.8, "method": "RMS"},
        "section": {"candidate": "groove_core", "confidence": 0.8, "status": "verified"},
        "harmonic_analysis": {"key": "D#m", "confidence": 0.88, "status": "inferred"},
        "theory_interpretation": {
            "summary": "安定した4つ打ちと裏拍ベース",
            "detailed_explanations": ["キックとベースがマスキングを回避しています。"],
        },
        "generation_rules": {
            "bpm_target": 126.0,
            "key": "D#m",
            "kick_style": "four_on_the_floor",
            "bass_syncopation": 0.58,
            "bass_rest_ratio": 0.42,
            "interlock_priority": True,
            "genre_profile": "tech_house",
        },
    }
    card = AnalysisCard.from_analysis(
        analysis_result=sample_analysis,
        user_title="Peak Tech House Groove",
        audio_filename="tech_house.wav",
    )
    card.card_id = card_id
    return card


def test_training_themes_catalog():
    assert len(TRAINING_THEMES) >= 6
    categories = {t.category for t in TRAINING_THEMES}
    assert "groove" in categories
    assert "composition" in categories
    assert "sound_design" in categories

    theme_ids = {t.theme_id for t in TRAINING_THEMES}
    assert "kick_bass_space" in theme_ids
    assert "swing_microtiming" in theme_ids
    assert "velocity_accents" in theme_ids
    assert "call_and_response" in theme_ids
    assert "motif_repetition_variation" in theme_ids
    assert "spatial_and_dynamics" in theme_ids

    for t in TRAINING_THEMES:
        assert len(t.listening_points) >= 3
        assert len(t.theory_explanation) > 10
        assert len(t.beginner_explanation) > 10


def test_generate_kick_bass_space_variants():
    card = _make_dummy_card()
    theme = next(t for t in TRAINING_THEMES if t.theme_id == "kick_bass_space")

    res = generate_training_variants(theme, card, key="D#m", bars=8)
    assert res["ok"] is True
    assert len(res["variants"]) == 3

    v_a = res["variants"][0]
    v_b = res["variants"][1]
    v_c = res["variants"][2]

    assert v_a["variant_id"] == "A"
    assert v_b["variant_id"] == "B"
    assert v_c["variant_id"] == "C"

    # Novice and technical explanations present
    for v in (v_a, v_b, v_c):
        assert "novice_summary" in v
        assert "technical_summary" in v
        assert "what_to_listen" in v
        assert "live_sound_design" in v
        assert "Kick" in v["notes"]
        assert "Bass" in v["notes"]

    # Variant B (interlocking) should have lower kick-bass overlap than A
    assert v_b["parameters"]["kick_bass_overlap"] == "zero"
    assert v_a["parameters"]["kick_bass_overlap"] == "high"


def test_generate_swing_variants():
    card = _make_dummy_card()
    theme = next(t for t in TRAINING_THEMES if t.theme_id == "swing_microtiming")

    res = generate_training_variants(theme, card, key="D#m", bars=8)
    assert res["ok"] is True
    variants = res["variants"]
    assert len(variants) == 3

    assert variants[0]["parameters"]["swing_ratio"] == 0.50
    assert variants[1]["parameters"]["swing_ratio"] == 0.56
    assert variants[2]["parameters"]["swing_ratio"] == 0.62


def test_promote_training_variant_to_candidate(tmp_path, monkeypatch):
    monkeypatch.setenv("KIHACHI_PROJECT_DIR", str(tmp_path / "candidates"))
    card = _make_dummy_card()
    theme = next(t for t in TRAINING_THEMES if t.theme_id == "kick_bass_space")

    candidate = promote_training_variant_to_candidate(
        card=card,
        theme=theme,
        variant_id="B",
        bars=64,
        key="D#m",
    )
    assert candidate.candidate_id is not None
    assert len(candidate.clips) > 0
    # Tagged in original text
    assert f"[Training:{theme.theme_id}:B]" in candidate.brief.original_text
    assert candidate.brief.tempo.value == 126
    assert candidate.brief.key.value == "D#m"

    # Persisted to disk
    cand_file = tmp_path / "candidates" / f"{candidate.candidate_id}.json"
    assert cand_file.is_file()
    data = json.loads(cand_file.read_text("utf-8"))
    assert data["candidate_id"] == candidate.candidate_id


def test_training_journal_store(tmp_path):
    journal_file = tmp_path / "journal.json"
    store = TrainingJournalStore(journal_file)
    assert store.list_entries() == []

    entry = TrainingJournalEntry(
        entry_id="j_001",
        theme_id="kick_bass_space",
        theme_title="キックとベースの住み分け",
        theme_category="groove",
        source_type="reference_card",
        source_id="card_01",
        source_title="Tech House Groove",
        chosen_variant_id="B",
        chosen_variant_name="裏拍インターロッキング型",
        user_feedback_tags=["低音がスッキリした", "ノリが出た"],
        user_notes="キックの頭を避けてベースを鳴らすと、サブの抜けが劇的に良くなった。",
        key_takeaway="キックとベースは同時に鳴らさず、16分裏で会話させると音が太くなる",
        next_action_tip="次回のTech Houseでもベースの休符率を40%以上確保する",
        created_at="2026-10-23T10:00:00Z",
        promoted_candidate_id="cand_1234",
    )
    store.save_entry(entry)

    entries = store.list_entries()
    assert len(entries) == 1
    assert entries[0].entry_id == "j_001"
    assert entries[0].chosen_variant_id == "B"

    # Tips extraction for subsequent production
    tips = store.get_recent_tips_for_production()
    assert len(tips) == 1
    assert tips[0]["theme_title"] == "キックとベースの住み分け"
    assert "ベースの休符率" in tips[0]["tip"]


def test_learning_profile_aggregates_explicit_choices_across_cards(tmp_path, monkeypatch):
    monkeypatch.setenv("KIHACHI_PROJECT_DIR", str(tmp_path / "candidates"))
    store = TrainingJournalStore(tmp_path / "journal.json")
    theme = next(t for t in TRAINING_THEMES if t.theme_id == "kick_bass_space")
    for index in range(5):
        candidate = promote_training_variant_to_candidate(
            card=_make_dummy_card(f"card_{index}"),
            theme=theme,
            variant_id="B",
            bars=64,
            key="D#m",
        )
        assert store.record_promoted_candidate(candidate) is not None
        assert store.record_promoted_candidate(candidate) is None

    profile = store.learning_profile(theme.theme_id, "unrelated_card", [])
    assert profile["observations"] == 5
    assert profile["preferred_variant"] == "B"
    assert profile["active"] is True
    assert profile["scope_type"] == "theme"


def test_unmarked_candidate_is_not_automatically_learned(tmp_path, monkeypatch):
    monkeypatch.setenv("KIHACHI_PROJECT_DIR", str(tmp_path / "candidates"))
    store = TrainingJournalStore(tmp_path / "journal.json")
    candidate = promote_training_variant_to_candidate(
        card=_make_dummy_card(),
        theme=next(t for t in TRAINING_THEMES if t.theme_id == "kick_bass_space"),
        variant_id="A",
        bars=64,
        key="D#m",
    )
    ordinary_candidate = replace(
        candidate,
        brief=replace(candidate.brief, original_text="ordinary studio candidate"),
    )
    assert store.record_promoted_candidate(ordinary_candidate) is None
    assert store.list_entries() == []


def test_learning_dashboard_explains_progress_and_activation(tmp_path):
    store = TrainingJournalStore(tmp_path / "journal.json")
    theme = next(t for t in TRAINING_THEMES if t.theme_id == "kick_bass_space")
    for index, variant in enumerate(("B", "B", "B", "B", "A")):
        entry = TrainingJournalEntry(
            entry_id=f"entry_{index}",
            theme_id=theme.theme_id,
            theme_title=theme.title,
            theme_category=theme.category,
            source_type="promoted_training_candidate",
            source_id=f"card_{index}",
            source_title="training",
            chosen_variant_id=variant,
            chosen_variant_name=f"案{variant}",
            user_feedback_tags=[],
            user_notes="",
            key_takeaway="",
            next_action_tip="",
            created_at="2026-10-23T10:00:00Z",
        )
        store.save_entry(entry)
    dashboard = store.learning_dashboard()
    assert dashboard["total_observations"] == 5
    assert dashboard["automatic_observations"] == 5
    learned = next(item for item in dashboard["themes"] if item["theme_id"] == theme.theme_id)
    assert learned["active"] is True
    assert learned["preferred_variant"] == "B"
    assert learned["choice_counts"] == {"A": 1, "B": 4, "C": 0}


def test_learning_dashboard_includes_reference_genre_counts(tmp_path):
    dashboard = TrainingJournalStore(tmp_path / "journal.json").learning_dashboard([
        {"genre": "house"},
        {"genre": "house"},
        {"genre": "dub techno"},
        {},
    ])
    assert dashboard["reference_genres"] == {
        "house": 2,
        "dub techno": 1,
        "未分類": 1,
    }


def test_studio_ab_evaluations_are_automatic_deduplicated_and_directional(tmp_path):
    store = TrainingJournalStore(tmp_path / "journal.json")

    assert store.record_studio_evaluation("candidate-equal", "EQUAL") is None
    for index, rating in enumerate(("B_BETTER", "B_BETTER", "B_BETTER", "A_BETTER", "A_BETTER")):
        store.record_studio_evaluation(f"candidate-{index}", rating)

    learned = store.learned_studio_swing()
    assert learned["active"] is True
    assert learned["swing_ratio"] == 0.54
    assert learned["choice_counts"] == {"A": 2, "B": 3}
    assert learned["observations"] == 5

    store.record_studio_evaluation("candidate-0", "A_BETTER")
    entries = [entry for entry in store.list_entries() if entry.source_type == "studio_ab_evaluation"]
    assert len(entries) == 5
    assert store.learned_studio_swing()["choice_counts"] == {"A": 3, "B": 2}
