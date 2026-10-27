"""Tests for AnalysisCard and AnalysisCardStore."""

from kihachi_mcp.services.reference_analysis_card import (
    AnalysisCard,
    AnalysisCardStore,
)


def test_analysis_card_create_and_serialize(tmp_path):
    store = AnalysisCardStore(tmp_path)

    sample_analysis = {
        "ok": True,
        "segment": {
            "start_seconds": 0.0,
            "duration_seconds": 16.0,
            "sample_rate": 22050,
        },
        "bpm": {"value": 124.0, "confidence": 0.85, "unit": "BPM", "method": "autocorrelation", "status": "verified"},
        "kick_pattern": {"hit_count": 16, "interval_summary": "4-beat regular", "confidence": 0.9, "is_four_on_the_floor": True},
        "bass_groove": {"hit_count": 12, "rest_ratio": 0.65, "syncopation_ratio": 0.5, "density_notes_per_bar": 2.0, "confidence": 0.75},
        "energy_dynamics": {"trend": "stable", "confidence": 0.7, "method": "RMS"},
        "section": {"candidate": "groove_core", "confidence": 0.4, "status": "unverified"},
        "harmonic_analysis": {"key": "unknown", "confidence": 0.0, "status": "unverified"},
        "theory_interpretation": {
            "summary": "安定した4つ打ちと裏拍ベース",
            "detailed_explanations": ["キックとベースがマスキングを回避しています。"],
        },
        "generation_rules": {
            "bpm_target": 124.0,
            "kick_style": "four_on_the_floor",
            "bass_syncopation": 0.5,
            "bass_rest_ratio": 0.65,
        },
    }

    card = AnalysisCard.from_analysis(
        analysis_result=sample_analysis,
        user_title="Test Dub Groove",
        audio_filename="track01.wav",
    )

    assert card.user_title == "Test Dub Groove"
    assert card.audio_filename == "track01.wav"
    assert card.card_id is not None
    assert card.generation_rules["kick_style"] == "four_on_the_floor"

    # Save to store
    store.save(card)

    # Retrieve from store
    loaded = store.get(card.card_id)
    assert loaded is not None
    assert loaded.user_title == card.user_title
    assert loaded.card_id == card.card_id

    # List cards
    cards_list = store.list_cards()
    assert len(cards_list) == 1
    assert cards_list[0]["card_id"] == card.card_id
    assert cards_list[0]["user_title"] == "Test Dub Groove"

    # Update user feedback
    loaded.apply_user_feedback(
        rating="5",
        notes="Very authentic groove, excellent space.",
        adopted=True,
        rejection_reason=None,
        manual_overrides={"tempo": 125.0, "key": "G minor"},
    )
    store.save(loaded)

    reloaded = store.get(card.card_id)
    assert reloaded.user_feedback["human_rating"] == "5"
    assert reloaded.user_feedback["adopted"] is True
    assert reloaded.user_feedback["manual_overrides"]["tempo"] == 125.0
