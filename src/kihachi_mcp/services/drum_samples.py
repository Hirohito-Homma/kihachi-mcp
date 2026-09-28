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
SIDE_STICK_NOTE = 37
SNARE_NOTE = 38
CLAP_NOTE = 39
HAT_NOTE = 42
OPEN_HAT_NOTE = 46
RIDE_NOTE = 51
CONGA_HIGH_NOTE = 63
CONGA_LOW_NOTE = 64
SHAKER_NOTE = 70
RISER_NOTE = 48
IMPACT_NOTE = 49
#: The riser sweeps for this long, so the builder can end it on a downbeat.
RISER_SECONDS = 4.0
# Existing files are never rewritten: a Set that already loaded one keeps the
# sound it was made with. New voices get new file names.
_SAMPLES = {
    "kihachi-kick.wav": KICK_NOTE,
    "kihachi-clap.wav": CLAP_NOTE,
    "kihachi-hat.wav": HAT_NOTE,
    "kihachi-open-hat.wav": OPEN_HAT_NOTE,
    "kihachi-side-stick.wav": SIDE_STICK_NOTE,
    "kihachi-snare.wav": SNARE_NOTE,
    "kihachi-ride.wav": RIDE_NOTE,
    "kihachi-conga-high.wav": CONGA_HIGH_NOTE,
    "kihachi-conga-low.wav": CONGA_LOW_NOTE,
    "kihachi-shaker.wav": SHAKER_NOTE,
    "kihachi-riser.wav": RISER_NOTE,
    "kihachi-impact.wav": IMPACT_NOTE,
}
_MUTATION_KICK = "kihachi-kick-deep.wav"


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


def sample_path_for_note(note: int, genre: str = "") -> Path:
    """Return the bundled sample for one MIDI note."""
    if note == KICK_NOTE and genre == "mutation_funk":
        path = sample_dir() / _MUTATION_KICK
        if not path.is_file() or path.stat().st_size < 64:
            _write_wav(path, _deep_kick_frames())
        return path
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
    return resolved.name in {*_SAMPLES, _MUTATION_KICK} and resolved.is_file()


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


def _deep_kick_frames() -> list[int]:
    """Longer 52 Hz body with a brief downward pitch sweep and soft attack."""
    length = int(SAMPLE_RATE * 0.38)
    frames: list[int] = []
    for index in range(length):
        t = index / SAMPLE_RATE
        phase = 2 * math.pi * (52 * t + 65 * 0.025 * (1 - math.exp(-t / 0.025)))
        body = math.sin(phase) * math.exp(-8 * t) * 0.83
        attack = math.sin(2 * math.pi * 420 * t) * math.exp(-100 * t) * 0.07
        fade = min(1.0, (length - index) / (SAMPLE_RATE * 0.025))
        frames.append(_clamp((body + attack) * fade))
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


def _noise(seed: int):
    state = seed
    while True:
        state = (1103515245 * state + 12345) & 0x7FFFFFFF
        yield (state / 0x7FFFFFFF) * 2.0 - 1.0


def _snare_frames() -> list[int]:
    length = int(SAMPLE_RATE * 0.22)
    noise = _noise(0x1357)
    frames: list[int] = []
    for index in range(length):
        t = index / SAMPLE_RATE
        tone = math.sin(2 * math.pi * 185 * t) * math.exp(-30 * t) * 0.5
        rattle = next(noise) * math.exp(-18 * t) * 0.45
        frames.append(_clamp(tone + rattle))
    return frames


def _side_stick_frames() -> list[int]:
    length = int(SAMPLE_RATE * 0.06)
    noise = _noise(0x2468)
    frames: list[int] = []
    for index in range(length):
        t = index / SAMPLE_RATE
        knock = math.sin(2 * math.pi * 420 * t) * math.exp(-70 * t) * 0.6
        click = next(noise) * math.exp(-250 * t) * 0.3
        frames.append(_clamp(knock + click))
    return frames


def _ride_frames() -> list[int]:
    """A few inharmonic partials over hiss: enough to read as a cymbal."""
    length = int(SAMPLE_RATE * 0.9)
    noise = _noise(0x4242)
    partials = (3150.0, 4290.0, 5810.0, 7370.0)
    frames: list[int] = []
    previous = 0.0
    for index in range(length):
        t = index / SAMPLE_RATE
        bell = sum(math.sin(2 * math.pi * f * t) for f in partials) / len(partials)
        hiss = next(noise)
        highpass = hiss - previous
        previous = hiss
        frames.append(_clamp((bell * 0.35 + highpass * 0.15) * math.exp(-4.5 * t)))
    return frames


def _shaker_frames() -> list[int]:
    """Bright hiss with a soft 6 ms attack, the way beads land in a shell."""
    length = int(SAMPLE_RATE * 0.09)
    noise = _noise(0x7171)
    frames: list[int] = []
    previous = 0.0
    for index in range(length):
        t = index / SAMPLE_RATE
        hiss = next(noise)
        highpass = hiss - previous
        previous = hiss
        attack = min(1.0, t / 0.006)
        frames.append(_clamp(highpass * attack * math.exp(-40 * t) * 0.5))
    return frames


def _conga_frames(pitch_hz: float) -> list[int]:
    """A skin tone that settles slightly flat, over a short palm slap."""
    length = int(SAMPLE_RATE * 0.3)
    noise = _noise(0x6363 + int(pitch_hz))
    frames: list[int] = []
    for index in range(length):
        t = index / SAMPLE_RATE
        phase = 2 * math.pi * (pitch_hz * t + pitch_hz * 0.08 * 0.02 * math.exp(-t / 0.02))
        tone = math.sin(phase) * math.exp(-13 * t) * 0.7
        slap = next(noise) * math.exp(-180 * t) * 0.25
        frames.append(_clamp(tone + slap))
    return frames


def _riser_frames() -> list[int]:
    """Noise and a tone that both climb for RISER_SECONDS, getting louder."""
    length = int(SAMPLE_RATE * RISER_SECONDS)
    noise = _noise(0x4848)
    frames: list[int] = []
    band = 0.0
    phase = 0.0
    for index in range(length):
        progress = index / length
        hiss = next(noise)
        # A one-pole low-pass whose corner opens as the riser climbs.
        band += (0.03 + 0.6 * progress * progress) * (hiss - band)
        phase += 2 * math.pi * (180 * 8 ** progress) / SAMPLE_RATE
        tone = math.sin(phase) * 0.25
        level = progress**1.6
        fade = min(1.0, (length - index) / (SAMPLE_RATE * 0.01))
        frames.append(_clamp((band * 0.8 + tone) * level * fade))
    return frames


def _impact_frames() -> list[int]:
    """A falling sub boom with a burst of dark noise on top."""
    length = int(SAMPLE_RATE * 1.6)
    noise = _noise(0x4949)
    frames: list[int] = []
    band = 0.0
    for index in range(length):
        t = index / SAMPLE_RATE
        phase = 2 * math.pi * (38 * t + 40 * 0.12 * (1 - math.exp(-t / 0.12)))
        boom = math.sin(phase) * math.exp(-2.4 * t) * 0.8
        band += 0.12 * (next(noise) - band)
        burst = band * math.exp(-7 * t) * 1.4
        fade = min(1.0, (length - index) / (SAMPLE_RATE * 0.05))
        frames.append(_clamp((boom + burst) * fade))
    return frames


_FRAMES = {
    CONGA_HIGH_NOTE: lambda: _conga_frames(330.0),
    CONGA_LOW_NOTE: lambda: _conga_frames(220.0),
    SHAKER_NOTE: _shaker_frames,
    RISER_NOTE: _riser_frames,
    IMPACT_NOTE: _impact_frames,
    SIDE_STICK_NOTE: _side_stick_frames,
    SNARE_NOTE: _snare_frames,
    RIDE_NOTE: _ride_frames,
    KICK_NOTE: _kick_frames,
    CLAP_NOTE: _clap_frames,
    HAT_NOTE: _hat_frames,
    OPEN_HAT_NOTE: _open_hat_frames,
}


def _clamp(value: float) -> int:
    return max(-32767, min(32767, int(value * 30000)))
