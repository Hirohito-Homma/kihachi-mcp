"""Bundled one-shot drum samples KIHACHI can load into empty Drum Rack pads.

The WAV files are generated here so they are original, local, and free of
third-party sample licenses. Live 12.4+ can place them with replace_sample.
"""

from __future__ import annotations

import math
import wave
from pathlib import Path

SAMPLE_RATE = 44100
KICK_NOTE = 36
CLAP_NOTE = 39
HAT_NOTE = 42
OPEN_HAT_NOTE = 46
# Existing files are never rewritten: a Set that already loaded one keeps the
# sound it was made with. New voices get new file names.
_SAMPLES = {
    "kihachi-kick.wav": KICK_NOTE,
    "kihachi-clap.wav": CLAP_NOTE,
    "kihachi-hat.wav": HAT_NOTE,
    "kihachi-open-hat.wav": OPEN_HAT_NOTE,
}


def sample_dir() -> Path:
    """Return the directory that holds the bundled drum one-shots."""
    return Path(__file__).resolve().parents[1] / "assets" / "drums"


def ensure_drum_samples() -> dict[int, Path]:
    """Write missing bundled WAVs and return note -> absolute path."""
    folder = sample_dir()
    folder.mkdir(parents=True, exist_ok=True)
    paths: dict[int, Path] = {}
    for name, note in _SAMPLES.items():
        path = folder / name
        if not path.is_file() or path.stat().st_size < 64:
            _write_wav(path, _FRAMES[note]())
        paths[note] = path
    return paths


def sample_path_for_note(note: int) -> Path:
    """Return the bundled sample for one MIDI note."""
    paths = ensure_drum_samples()
    if note not in paths:
        raise ValueError(f"no bundled drum sample for note {note}")
    return paths[note]


def is_bundled_sample(path: str | Path) -> bool:
    """Report whether the path is one of the bundled drum one-shots."""
    resolved = Path(path).expanduser().resolve()
    try:
        resolved.relative_to(sample_dir().resolve())
    except ValueError:
        return False
    return resolved.name in _SAMPLES and resolved.is_file()


def _write_wav(path: Path, frames: list[int]) -> None:
    with wave.open(str(path), "w") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(SAMPLE_RATE)
        handle.writeframes(b"".join(int(sample).to_bytes(2, "little", signed=True) for sample in frames))


def _kick_frames() -> list[int]:
    length = int(SAMPLE_RATE * 0.2)
    frames: list[int] = []
    for index in range(length):
        t = index / SAMPLE_RATE
        body = math.sin(2 * math.pi * (58 - 28 * t) * t) * math.exp(-18 * t)
        click = math.sin(2 * math.pi * 1800 * t) * math.exp(-90 * t) * 0.35
        frames.append(_clamp(body + click))
    return frames


def _hat_frames() -> list[int]:
    length = int(SAMPLE_RATE * 0.07)
    frames: list[int] = []
    seed = 0xA5A5
    previous = 0.0
    for index in range(length):
        t = index / SAMPLE_RATE
        seed = (1103515245 * seed + 12345) & 0x7FFFFFFF
        noise = (seed / 0x7FFFFFFF) * 2.0 - 1.0
        highpass = noise - previous
        previous = noise
        frames.append(_clamp(highpass * math.exp(-55 * t)))
    return frames


def _open_hat_frames() -> list[int]:
    length = int(SAMPLE_RATE * 0.32)
    frames: list[int] = []
    seed = 0x5A5A
    previous = 0.0
    for index in range(length):
        t = index / SAMPLE_RATE
        seed = (1103515245 * seed + 12345) & 0x7FFFFFFF
        noise = (seed / 0x7FFFFFFF) * 2.0 - 1.0
        highpass = noise - previous
        previous = noise
        frames.append(_clamp(highpass * math.exp(-11 * t) * 0.45))
    return frames


def _clap_frames() -> list[int]:
    """Three quick noise bursts and a tail, the way a hand clap smears."""
    length = int(SAMPLE_RATE * 0.25)
    frames: list[int] = []
    seed = 0x3C3C
    band = 0.0
    previous = 0.0
    for index in range(length):
        t = index / SAMPLE_RATE
        seed = (1103515245 * seed + 12345) & 0x7FFFFFFF
        noise = (seed / 0x7FFFFFFF) * 2.0 - 1.0
        # A one-pole low-pass after a difference keeps roughly 1-3 kHz.
        band += 0.35 * ((noise - previous) - band)
        previous = noise
        bursts = sum(
            math.exp(-160 * (t - onset)) for onset in (0.0, 0.011, 0.022) if t >= onset
        )
        tail = math.exp(-20 * max(0.0, t - 0.022)) * 0.6 if t >= 0.022 else 0.0
        frames.append(_clamp(band * 2.2 * (bursts * 0.5 + tail)))
    return frames


_FRAMES = {
    KICK_NOTE: _kick_frames,
    CLAP_NOTE: _clap_frames,
    HAT_NOTE: _hat_frames,
    OPEN_HAT_NOTE: _open_hat_frames,
}


def _clamp(value: float) -> int:
    return max(-32767, min(32767, int(value * 30000)))
