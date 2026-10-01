"""Render an eight-bar MIDI comparison using fixed local drum and synth sounds."""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import soundfile as sf
from render_midi_lead_preview import SAMPLE_RATE, TICKS_PER_BEAT, read_tracks

SAMPLE_FILES = {
    36: "kihachi-kick.wav", 37: "kihachi-side-stick.wav",
    38: "kihachi-snare.wav", 39: "kihachi-clap.wav",
    42: "kihachi-hat.wav", 46: "kihachi-open-hat.wav", 51: "kihachi-ride.wav",
    63: "kihachi-conga-high.wav", 64: "kihachi-conga-low.wav",
    70: "kihachi-shaker.wav",
}
DRUM_PARTS = {"Kick", "Snare", "Hats", "OpenHat", "Perc"}
TONE_GAIN = {"Bass": 0.09, "Sub": 0.045, "Stab": 0.025,
             "Pad": 0.015, "Arp": 0.02, "Lead": 0.13}


def _tone(pitch: int, length: int, part: str) -> np.ndarray:
    t = np.arange(length, dtype=np.float32) / SAMPLE_RATE
    hz = 440.0 * 2 ** ((pitch - 69) / 12)
    phase = 2 * np.pi * hz * t
    if part in {"Bass", "Sub"}:
        wave = np.sin(phase) + 0.12 * np.sin(2 * phase)
    elif part == "Lead":
        wave = np.sin(phase) + 0.22 * np.sin(2 * phase)
    else:
        wave = np.sin(phase) + 0.14 * np.sin(3 * phase)
    attack = np.minimum(1.0, t / 0.008)
    release = np.minimum(1.0, (length / SAMPLE_RATE - t) / 0.045)
    return wave * attack * release


def render(midi: Path, wav: Path, start_bar: int = 17, bars: int = 8) -> dict[str, int]:
    """Write a new WAV without changing MIDI, samples, or existing output."""
    if wav.exists():
        raise FileExistsError(wav)
    if start_bar < 1 or bars < 1:
        raise ValueError("start_bar and bars must be positive")
    tempo, tracks = read_tracks(midi)
    start_tick = (start_bar - 1) * 4 * TICKS_PER_BEAT
    end_tick = start_tick + bars * 4 * TICKS_PER_BEAT
    seconds_per_tick = tempo / 1_000_000 / TICKS_PER_BEAT
    audio = np.zeros(round((end_tick - start_tick) * seconds_per_tick * SAMPLE_RATE), dtype=np.float32)
    sample_root = Path(__file__).resolve().parents[1] / "src/kihachi_mcp/assets/drums"
    samples: dict[int, np.ndarray] = {}
    counts: dict[str, int] = {}
    for part, notes in tracks.items():
        if part not in DRUM_PARTS | TONE_GAIN.keys():
            continue
        for start, end, pitch, velocity in notes:
            if start >= end_tick or end <= start_tick:
                continue
            first = round((start - start_tick) * seconds_per_tick * SAMPLE_RATE)
            if first < 0:
                continue
            if part in DRUM_PARTS:
                filename = SAMPLE_FILES.get(pitch)
                if filename is None:
                    continue
                if pitch not in samples:
                    sample, sample_rate = sf.read(sample_root / filename, dtype="float32")
                    if sample_rate != SAMPLE_RATE * 2:
                        raise ValueError(f"Unexpected sample rate for {filename}")
                    samples[pitch] = sample[::2]
                rendered = samples[pitch] * (velocity / 127) * 0.22
            else:
                length = round((end - start) * seconds_per_tick * SAMPLE_RATE)
                if length <= 0:
                    continue
                rendered = _tone(pitch, length, part) * (velocity / 127) * TONE_GAIN[part]
            last = min(len(audio), first + len(rendered))
            if last <= first:
                continue
            audio[first:last] += rendered[:last - first]
            counts[part] = counts.get(part, 0) + 1
    if not counts.get("Lead"):
        raise ValueError("No Lead notes in the selected bars")
    peak = float(np.max(np.abs(audio)))
    if peak > 0.9:
        audio *= 0.9 / peak
    wav.parent.mkdir(parents=True, exist_ok=True)
    sf.write(wav, audio, SAMPLE_RATE, subtype="PCM_16")
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("midi", type=Path)
    parser.add_argument("wav", type=Path)
    parser.add_argument("--start-bar", type=int, default=17)
    parser.add_argument("--bars", type=int, default=8)
    args = parser.parse_args()
    print(render(args.midi, args.wav, args.start_bar, args.bars))


if __name__ == "__main__":
    main()
