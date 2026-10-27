"""Tests for reference groove analysis (drum & bass relationship, theory, rules)."""

import math
import wave
from pathlib import Path

import numpy as np

from kihachi_mcp.services.reference_groove_analysis import (
    analyze_reference_groove,
)


def _create_synthetic_groove_wav(
    path: Path,
    bpm: float = 120.0,
    seconds: float = 8.0,
    sample_rate: int = 22050,
) -> Path:
    """Create a synthetic 4-on-the-floor kick + offbeat bass audio file."""
    total_samples = int(sample_rate * seconds)
    audio = np.zeros(total_samples, dtype=np.float32)

    beat_interval_samples = int(sample_rate * (60.0 / bpm))

    # Add kicks on every beat (50 Hz sine pulse decaying over 100ms)
    kick_len = int(sample_rate * 0.1)
    t_kick = np.arange(kick_len) / sample_rate
    kick_pulse = np.sin(2 * math.pi * 50 * t_kick) * np.exp(-t_kick * 30)

    # Add bass on offbeats (120 Hz sine pulse decaying over 150ms)
    bass_len = int(sample_rate * 0.15)
    t_bass = np.arange(bass_len) / sample_rate
    bass_pulse = np.sin(2 * math.pi * 120 * t_bass) * np.exp(-t_bass * 15)

    pos = 0
    while pos + kick_len < total_samples:
        audio[pos : pos + kick_len] += kick_pulse * 0.7
        # Offbeat bass (halfway between beats)
        offbeat_pos = pos + beat_interval_samples // 2
        if offbeat_pos + bass_len < total_samples:
            audio[offbeat_pos : offbeat_pos + bass_len] += bass_pulse * 0.5
        pos += beat_interval_samples

    # Normalize safely
    max_val = np.max(np.abs(audio))
    if max_val > 0:
        audio = audio / max_val * 0.85

    int_data = (audio * 32767).astype("<i2")
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(int_data.tobytes())
    return path


def test_analyze_reference_groove_synthetic(tmp_path):
    wav_file = tmp_path / "test_groove.wav"
    _create_synthetic_groove_wav(wav_file, bpm=120.0, seconds=8.0)

    result = analyze_reference_groove(wav_file, start_seconds=0.0, duration_seconds=8.0)
    assert result["ok"] is True

    # 1. Segment metadata
    seg = result["segment"]
    assert seg["start_seconds"] == 0.0
    assert 7.5 <= seg["duration_seconds"] <= 8.5

    # 2. BPM estimation
    bpm_info = result["bpm"]
    assert bpm_info["value"] is not None
    assert 110.0 <= bpm_info["value"] <= 130.0
    assert bpm_info["confidence"] > 0.0
    assert bpm_info["unit"] == "BPM"

    # 3. Kick analysis
    kick_info = result["kick_pattern"]
    assert kick_info["is_four_on_the_floor"] is True
    assert kick_info["confidence"] > 0.4

    # 4. Bass analysis
    bass_info = result["bass_groove"]
    assert bass_info["rest_ratio"] > 0.0
    assert bass_info["syncopation_ratio"] > 0.3  # Offbeats detected

    # 5. Harmonics / Key (Insecure metrics must be unverified / unknown)
    harmonic_info = result["harmonic_analysis"]
    assert harmonic_info["status"] == "unverified"
    assert harmonic_info["confidence"] == 0.0
    assert harmonic_info["key"] == "unknown"

    # 6. Theory interpretation
    theory = result["theory_interpretation"]
    assert "summary" in theory
    assert len(theory["detailed_explanations"]) > 0

    # 7. Generation rules (abstract, no raw note copying)
    rules = result["generation_rules"]
    assert rules["kick_style"] == "four_on_the_floor"
    assert "bass_syncopation" in rules
    assert "bass_rest_ratio" in rules


def test_analyze_reference_groove_silence(tmp_path):
    wav_file = tmp_path / "silent.wav"
    with wave.open(str(wav_file), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(22050)
        wav.writeframes(np.zeros(22050 * 2, dtype="<i2").tobytes())

    result = analyze_reference_groove(wav_file, start_seconds=0.0, duration_seconds=2.0)
    bpm_info = result["bpm"]
    assert bpm_info["value"] is None
    assert bpm_info["status"] in ("unknown", "unverified")
    assert bpm_info["confidence"] == 0.0
    assert result["kick_pattern"]["hit_count"] == 0
