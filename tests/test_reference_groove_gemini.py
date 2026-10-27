"""Tests for reference groove Gemini enhancement service."""

from unittest.mock import MagicMock

import numpy as np
import soundfile as sf

from kihachi_mcp.services.ai_provider import InterpretationError
from kihachi_mcp.services.reference_analysis_card import AnalysisCard
from kihachi_mcp.services.reference_groove_gemini import (
    analyze_groove_audio_with_gemini,
    enhance_groove_with_gemini,
)


def _sample_mechanical_data() -> dict:
    return {
        "bpm": {"value": 126.0, "confidence": 0.85},
        "kick_pattern": {"hit_count": 16, "interval_summary": "4-beat regular", "is_four_on_the_floor": True},
        "bass_groove": {"density_notes_per_bar": 2.5, "rest_ratio": 0.60, "syncopation_ratio": 0.55},
        "energy_dynamics": {"trend": "stable"},
    }


def test_enhance_groove_with_gemini_success():
    mock_provider = MagicMock()
    mock_provider.health.return_value = {"ok": True}
    mock_provider.model = "gemini-2.5-flash"
    mock_provider.generate_structured.return_value = {
        "theory_interpretation": {
            "summary": "キックとベースの帯域・時間的分離によるディープなグルーヴ",
            "groove_structure": "安定した4つ打ちのアンカー",
            "kick_bass_relationship": "キック裏に配置されたサブベースが低域マスキングを回避",
            "space_and_rests": "60%の休符によりDub空間が確保されている",
            "energy_and_arrangement": "ミニマルな展開",
        },
        "generation_rules": {
            "bpm_target": 126.0,
            "kick_style": "four_on_the_floor",
            "bass_syncopation": 0.55,
            "bass_rest_ratio": 0.60,
            "kick_bass_interlock": True,
            "recommended_key": "Fm",  # Will be sanitized to unknown / unverified
        },
        "audio_confidence_notes": {
            "rhythm_confidence": 0.9,
            "harmonic_status": "unverified",
            "explanation": "キー・和声は推測による誤判定を避けるため未検証です。",
        },
    }

    result = enhance_groove_with_gemini(
        mechanical_analysis=_sample_mechanical_data(),
        genre="dub_techno",
        title="Dub Minimal 01",
        provider=mock_provider,
    )

    assert result["ok"] is True
    assert result["enhanced_by"] == "gemini"
    assert result["model"] == "gemini-2.5-flash"

    # Verify music theory interpretation
    theory = result["theory_interpretation"]
    assert "Dub空間" in theory["space_and_rests"]
    assert "マスキング" in theory["kick_bass_relationship"]

    # Verify generation rules
    rules = result["generation_rules"]
    assert rules["bpm_target"] == 126.0
    assert rules["kick_style"] == "four_on_the_floor"
    assert rules["kick_bass_interlock"] is True

    # Critical requirement: harmonic status must remain unverified/unknown
    assert result["audio_confidence_notes"]["harmonic_status"] == "unverified"

    # Apply to AnalysisCard
    card = AnalysisCard.from_analysis(
        analysis_result=_sample_mechanical_data(),
        user_title="Dub Minimal 01",
        audio_filename="dub.wav",
    )
    card.apply_gemini_enhancement(result)
    assert card.theory_interpretation["enhanced_by"] == "gemini"
    assert card.generation_rules["kick_bass_interlock"] is True


def test_enhance_groove_with_gemini_missing_key_fallback():
    mock_provider = MagicMock()
    mock_provider.health.return_value = {
        "ok": False,
        "state": "api_key_missing",
        "message": "GEMINI_API_KEY または GOOGLE_API_KEY が設定されていません",
    }

    result = enhance_groove_with_gemini(
        mechanical_analysis=_sample_mechanical_data(),
        provider=mock_provider,
    )

    # Must NOT crash, must report safe reason and fallback_used
    assert result["ok"] is False
    assert result["fallback_used"] is True
    assert "GEMINI_API_KEY" in result["error"]
    mock_provider.generate_structured.assert_not_called()


def test_enhance_groove_with_gemini_api_error_fallback():
    mock_provider = MagicMock()
    mock_provider.health.return_value = {"ok": True}
    mock_provider.generate_structured.side_effect = InterpretationError(
        "Gemini APIのレート制限・割当上限（HTTP 429: RESOURCE_EXHAUSTED）に達しました。"
    )

    result = enhance_groove_with_gemini(
        mechanical_analysis=_sample_mechanical_data(),
        provider=mock_provider,
    )

    # Must gracefully return error without raising
    assert result["ok"] is False
    assert result["fallback_used"] is True
    assert "429" in result["error"] or "レート制限" in result["error"]


def test_analyze_groove_audio_with_gemini_mode_b(tmp_path):
    wav_path = tmp_path / "test_slice.wav"
    samples = np.zeros((22050 * 4, 1), dtype=np.float32)
    sf.write(str(wav_path), samples, 22050, format="WAV")

    mock_provider = MagicMock()
    mock_provider.health.return_value = {"ok": True}
    mock_provider.model = "gemini-2.5-flash"
    mock_provider.generate_with_audio.return_value = {
        "theory_interpretation": {
            "summary": "Audio Multimodal Groove Analysis",
            "groove_structure": "4つ打ちとスウィング",
            "kick_bass_relationship": "周波数分離とオフビート配置",
            "space_and_rests": "十分な減衰空間",
            "energy_and_arrangement": "安定したビルドアップ",
        },
        "generation_rules": {
            "bpm_target": 125.0,
            "kick_style": "four_on_the_floor",
            "bass_syncopation": 0.65,
            "bass_rest_ratio": 0.50,
            "kick_bass_interlock": True,
            "recommended_key": "unknown",
        },
        "audio_confidence_notes": {
            "rhythm_confidence": 0.95,
            "harmonic_status": "unverified",
            "explanation": "キー・和声は推測による誤判定を避けるため未検証です。",
        },
    }

    result = analyze_groove_audio_with_gemini(
        audio_path=wav_path,
        start_sec=0.0,
        duration_sec=4.0,
        genre="minimal_techno",
        title="Test Minimal",
        provider=mock_provider,
    )

    assert result["ok"] is True
    assert result["analysis_mode"] == "gemini_mode_b"
    mock_provider.generate_with_audio.assert_called_once()

    card = AnalysisCard.from_analysis(
        analysis_result=_sample_mechanical_data(),
        user_title="Test Minimal",
        audio_filename="test_slice.wav",
    )
    card.apply_gemini_enhancement(result)
    assert card.gemini_status == "success"
    assert card.analysis_mode == "gemini_mode_b"
