"""Read-only, approximate audio preview of a saved MIDI candidate."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf

from kihachi_mcp.models.midi_candidate import MidiCandidate
from kihachi_mcp.services.drum_samples import sample_dir

SAMPLE_RATE = 22_050
DRUM_SAMPLES = {
    36: "kihachi-kick.wav", 37: "kihachi-side-stick.wav",
    38: "kihachi-snare.wav", 39: "kihachi-clap.wav",
    42: "kihachi-hat.wav", 46: "kihachi-open-hat.wav", 51: "kihachi-ride.wav",
    63: "kihachi-conga-high.wav", 64: "kihachi-conga-low.wav",
    70: "kihachi-shaker.wav",
}
DRUM_PARTS = frozenset({"Kick", "Snare", "Hats", "OpenHat", "Perc"})
TONE_GAIN = {"Bass": 0.09, "Sub": 0.045, "Stab": 0.025,
             "Pad": 0.015, "Arp": 0.02, "Lead": 0.13}


PART_GROUPS = {
    "drums": frozenset({"Kick", "Snare", "Hats", "OpenHat", "Perc"}),
    "bass": frozenset({"Bass", "Sub"}),
    "chords": frozenset({"Stab", "Pad", "Guitar"}),
}


def preview_window(candidate: MidiCandidate) -> tuple[int, int, str]:
    """Choose the first peak, or the opening when a short song has no peak."""
    section = next(
        (item for item in candidate.brief.sections if item.name in {"Drop", "ChorusA"}),
        candidate.brief.sections[0],
    )
    return section.start_bar, min(8, section.length_bars), section.name


def _tone(pitch: int, length: int, part: str) -> np.ndarray:
    time = np.arange(length, dtype=np.float32) / SAMPLE_RATE
    phase = 2 * np.pi * 440 * 2 ** ((pitch - 69) / 12) * time
    second = 0.12 if part in {"Bass", "Sub"} else 0.22 if part == "Lead" else 0.14
    harmonic = 2 if part in {"Bass", "Sub", "Lead"} else 3
    attack = np.minimum(1.0, time / 0.008)
    release = np.minimum(1.0, (length / SAMPLE_RATE - time) / 0.045)
    return (np.sin(phase) + second * np.sin(harmonic * phase)) * attack * release


def candidate_preview_wav(
    candidate: MidiCandidate,
    part_filter: str | None = None,
    swing_override: float | None = None,
) -> bytes:
    """Render candidate note positions with uniform peak loudness and optional part filtering."""
    from kihachi_mcp.services.midi_composer import _apply_swing

    start_bar, bars, _name = preview_window(candidate)
    beats = candidate.brief.beats_per_bar
    start_beat = (start_bar - 1) * beats
    end_beat = start_beat + bars * beats
    seconds_per_beat = 60 / int(candidate.brief.tempo.value)
    audio = np.zeros(round(bars * beats * seconds_per_beat * SAMPLE_RATE), dtype=np.float32)
    samples: dict[int, np.ndarray] = {}

    allowed_parts: frozenset[str] | None = None
    if part_filter and part_filter.lower() in PART_GROUPS:
        allowed_parts = PART_GROUPS[part_filter.lower()]

    for clip in candidate.clips:
        if allowed_parts is not None and clip.part not in allowed_parts:
            continue
        if clip.part not in DRUM_PARTS | TONE_GAIN.keys():
            continue
        for note in clip.notes:
            note_start = note.start_beats
            if swing_override is not None:
                # Re-warp offbeats to the requested swing variant (e.g. 0.50 straight vs 0.54 groove)
                note_start = _apply_swing(note.start_beats, swing_override)

            absolute = (clip.start_bar - 1) * beats + note_start
            if not start_beat <= absolute < end_beat:
                continue
            first = round((absolute - start_beat) * seconds_per_beat * SAMPLE_RATE)
            if clip.part in DRUM_PARTS:
                filename = DRUM_SAMPLES.get(note.pitch)
                if filename is None:
                    continue
                if note.pitch not in samples:
                    sample, rate = sf.read(sample_dir() / filename, dtype="float32")
                    if rate != SAMPLE_RATE * 2:
                        raise ValueError(f"Unexpected sample rate: {filename}")
                    samples[note.pitch] = sample[::2]
                rendered = samples[note.pitch] * (note.velocity / 127) * 0.22
            else:
                length = round(note.duration_beats * seconds_per_beat * SAMPLE_RATE)
                if length <= 0:
                    continue
                rendered = _tone(note.pitch, length, clip.part) * (
                    note.velocity / 127
                ) * TONE_GAIN[clip.part]
            last = min(len(audio), first + len(rendered))
            if last > first:
                audio[first:last] += rendered[:last - first]

    peak = float(np.max(np.abs(audio)))
    if peak > 1e-4:
        # Uniform loudness normalization (peak 0.85 / -1.4 dBFS) across all variants and parts
        audio = audio * (0.85 / peak)

    output = BytesIO()
    sf.write(output, audio, SAMPLE_RATE, format="WAV", subtype="PCM_16")
    return output.getvalue()


def measure_preview_audio(audio_bytes: bytes) -> dict[str, Any]:
    """Measure loudness (LUFS), sample peak, clipping and silence of a preview WAV."""
    import math
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp.write(audio_bytes)
        tmp_path = Path(tmp.name)

    try:
        from kihachi_mcp.services.loudness import measure
        report = measure(tmp_path)
        integrated_lufs = report.get("integrated_lufs")
        sample_peak = report.get("sample_peak_dbfs")
        true_peak = report.get("true_peak_dbtp")
        has_lufs = integrated_lufs is not None and not math.isinf(integrated_lufs)
    except (OSError, RuntimeError, ValueError, ArithmeticError):
        # Fallback if audio is too short or measurement fails
        data, _rate = sf.read(BytesIO(audio_bytes), dtype="float32")
        sample_peak = round(20 * math.log10(max(float(np.max(np.abs(data))), 1e-9)), 2)
        true_peak = sample_peak
        integrated_lufs = None
        has_lufs = False
    finally:
        tmp_path.unlink(missing_ok=True)

    is_clipped = (sample_peak is not None and sample_peak >= -0.05) or (true_peak is not None and true_peak > 0.0)
    is_silent = (sample_peak is not None and sample_peak <= -60.0) or (has_lufs and integrated_lufs <= -70.0)

    return {
        "ok": True,
        "has_lufs": has_lufs,
        "integrated_lufs": integrated_lufs,
        "sample_peak_dbfs": sample_peak,
        "true_peak_dbtp": true_peak,
        "is_clipped": is_clipped,
        "is_silent": is_silent,
        "normalization": "lufs_measured_and_peak_matched" if has_lufs else "peak_matched_only",
        "description": f"{integrated_lufs} LUFS / {sample_peak} dBFS Peak" if has_lufs else f"ピーク一致のみ ({sample_peak} dBFS)",
    }
