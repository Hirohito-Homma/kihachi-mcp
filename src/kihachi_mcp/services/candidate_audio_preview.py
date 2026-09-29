"""Read-only, approximate audio preview of a saved MIDI candidate."""

from __future__ import annotations

from io import BytesIO

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


def candidate_preview_wav(candidate: MidiCandidate) -> bytes:
    """Render exact candidate note positions with fixed, non-production sounds."""
    start_bar, bars, _name = preview_window(candidate)
    beats = candidate.brief.beats_per_bar
    start_beat = (start_bar - 1) * beats
    end_beat = start_beat + bars * beats
    seconds_per_beat = 60 / int(candidate.brief.tempo.value)
    audio = np.zeros(round(bars * beats * seconds_per_beat * SAMPLE_RATE), dtype=np.float32)
    samples: dict[int, np.ndarray] = {}
    for clip in candidate.clips:
        if clip.part not in DRUM_PARTS | TONE_GAIN.keys():
            continue
        for note in clip.notes:
            absolute = (clip.start_bar - 1) * beats + note.start_beats
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
    if peak > 0.9:
        audio *= 0.9 / peak
    output = BytesIO()
    sf.write(output, audio, SAMPLE_RATE, format="WAV", subtype="PCM_16")
    return output.getvalue()
