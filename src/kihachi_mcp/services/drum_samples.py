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
TAMBOURINE_NOTE = 54
SPLASH_NOTE = 55
COWBELL_NOTE = 56
CRASH_NOTE = 57
VIBRASLAP_NOTE = 58
RIDE_BELL_NOTE = 59
BONGO_HIGH_NOTE = 60
BONGO_LOW_NOTE = 61
CONGA_MUTE_HIGH_NOTE = 62
TIMBALE_HIGH_NOTE = 65
TIMBALE_LOW_NOTE = 66
AGOGO_HIGH_NOTE = 67
AGOGO_LOW_NOTE = 68
CABASA_NOTE = 69
WHISTLE_SHORT_NOTE = 71
WHISTLE_LONG_NOTE = 72
GUIRO_SHORT_NOTE = 73
GUIRO_LONG_NOTE = 74
CLAVES_NOTE = 75
WOOD_HIGH_NOTE = 76
WOOD_LOW_NOTE = 77
CUICA_MUTE_NOTE = 78
CUICA_OPEN_NOTE = 79
TRIANGLE_MUTE_NOTE = 80
TRIANGLE_OPEN_NOTE = 81
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
    "kihachi-tambourine.wav": TAMBOURINE_NOTE,
    "kihachi-splash.wav": SPLASH_NOTE,
    "kihachi-cowbell.wav": COWBELL_NOTE,
    "kihachi-crash.wav": CRASH_NOTE,
    "kihachi-vibraslap.wav": VIBRASLAP_NOTE,
    "kihachi-ride-bell.wav": RIDE_BELL_NOTE,
    "kihachi-bongo-high.wav": BONGO_HIGH_NOTE,
    "kihachi-bongo-low.wav": BONGO_LOW_NOTE,
    "kihachi-conga-mute-high.wav": CONGA_MUTE_HIGH_NOTE,
    "kihachi-timbale-high.wav": TIMBALE_HIGH_NOTE,
    "kihachi-timbale-low.wav": TIMBALE_LOW_NOTE,
    "kihachi-agogo-high.wav": AGOGO_HIGH_NOTE,
    "kihachi-agogo-low.wav": AGOGO_LOW_NOTE,
    "kihachi-cabasa.wav": CABASA_NOTE,
    "kihachi-whistle-short.wav": WHISTLE_SHORT_NOTE,
    "kihachi-whistle-long.wav": WHISTLE_LONG_NOTE,
    "kihachi-guiro-short.wav": GUIRO_SHORT_NOTE,
    "kihachi-guiro-long.wav": GUIRO_LONG_NOTE,
    "kihachi-claves.wav": CLAVES_NOTE,
    "kihachi-wood-high.wav": WOOD_HIGH_NOTE,
    "kihachi-wood-low.wav": WOOD_LOW_NOTE,
    "kihachi-cuica-mute.wav": CUICA_MUTE_NOTE,
    "kihachi-cuica-open.wav": CUICA_OPEN_NOTE,
    "kihachi-triangle-mute.wav": TRIANGLE_MUTE_NOTE,
    "kihachi-triangle-open.wav": TRIANGLE_OPEN_NOTE,
}
_MUTATION_KICK = "kihachi-kick-deep.wav"
FAT_TIGHT_KICK = "kihachi-kick-fat-tight.wav"


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
    return resolved.name in {*_SAMPLES, _MUTATION_KICK, FAT_TIGHT_KICK} and resolved.is_file()


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


def _tambourine_frames() -> list[int]:
    """Metallic jingles over hiss, trembling the way a shaken tambourine does."""
    length = int(SAMPLE_RATE * 0.18)
    noise = _noise(0x5454)
    partials = (5230.0, 6710.0, 8390.0)
    frames: list[int] = []
    previous = 0.0
    for index in range(length):
        t = index / SAMPLE_RATE
        hiss = next(noise)
        highpass = hiss - previous
        previous = hiss
        jingle = sum(math.sin(2 * math.pi * f * t) for f in partials) / len(partials)
        tremble = 0.75 + 0.25 * math.sin(2 * math.pi * 32 * t)
        frames.append(_clamp((jingle * 0.3 + highpass * 0.25) * tremble * math.exp(-26 * t)))
    return frames


def _cowbell_frames() -> list[int]:
    """Two detuned metallic tones with a fast decay, after the 808 recipe."""
    length = int(SAMPLE_RATE * 0.28)
    frames: list[int] = []
    for index in range(length):
        t = index / SAMPLE_RATE
        tone = (
            math.sin(2 * math.pi * 587 * t)
            + math.sin(2 * math.pi * 845 * t) * 0.7
            + math.sin(2 * math.pi * 1244 * t) * 0.25
        )
        frames.append(_clamp(tone * 0.45 * math.exp(-11 * t)))
    return frames


def _conga_mute_frames() -> list[int]:
    """A high conga choked by the palm: same skin, a third of the ring."""
    length = int(SAMPLE_RATE * 0.12)
    noise = _noise(0x6262)
    frames: list[int] = []
    for index in range(length):
        t = index / SAMPLE_RATE
        phase = 2 * math.pi * (370.0 * t + 370.0 * 0.08 * 0.02 * math.exp(-t / 0.02))
        tone = math.sin(phase) * math.exp(-32 * t) * 0.7
        slap = next(noise) * math.exp(-180 * t) * 0.3
        frames.append(_clamp(tone + slap))
    return frames


def _timbale_low_frames() -> list[int]:
    """A low metal shell with a skin centre: stick click, ring, and body."""
    length = int(SAMPLE_RATE * 0.35)
    noise = _noise(0x6666)
    frames: list[int] = []
    for index in range(length):
        t = index / SAMPLE_RATE
        body = math.sin(2 * math.pi * 178 * t) * math.exp(-10 * t) * 0.55
        ring = (
            math.sin(2 * math.pi * 178 * 2.76 * t) * 0.2
            + math.sin(2 * math.pi * 178 * 5.4 * t) * 0.08
        ) * math.exp(-16 * t)
        click = next(noise) * math.exp(-220 * t) * 0.3
        frames.append(_clamp(body + ring + click))
    return frames


def _claves_frames() -> list[int]:
    """One resonant wood tick: a high sine that is gone almost at once."""
    length = int(SAMPLE_RATE * 0.09)
    frames: list[int] = []
    for index in range(length):
        t = index / SAMPLE_RATE
        tick = math.sin(2 * math.pi * 2500 * t) * math.exp(-85 * t)
        frames.append(_clamp(tick * 0.8))
    return frames


def _skin_frames(pitch_hz: float, decay: float, seconds: float, seed: int) -> list[int]:
    """A conga-family skin tone with a faster choke, for the smaller drums."""
    length = int(SAMPLE_RATE * seconds)
    noise = _noise(seed)
    frames: list[int] = []
    for index in range(length):
        t = index / SAMPLE_RATE
        phase = 2 * math.pi * (pitch_hz * t + pitch_hz * 0.08 * 0.02 * math.exp(-t / 0.02))
        tone = math.sin(phase) * math.exp(-decay * t) * 0.7
        slap = next(noise) * math.exp(-180 * t) * 0.25
        frames.append(_clamp(tone + slap))
    return frames


def _metal_frames(
    partials: tuple[float, ...],
    decay: float,
    seconds: float,
    seed: int,
    noise_gain: float = 0.15,
    tone_gain: float = 0.35,
) -> list[int]:
    """Inharmonic partials over high-passed hiss: the cymbal and bell family."""
    length = int(SAMPLE_RATE * seconds)
    noise = _noise(seed)
    frames: list[int] = []
    previous = 0.0
    for index in range(length):
        t = index / SAMPLE_RATE
        bell = sum(math.sin(2 * math.pi * f * t) for f in partials) / len(partials)
        hiss = next(noise)
        highpass = hiss - previous
        previous = hiss
        frames.append(_clamp((bell * tone_gain + highpass * noise_gain) * math.exp(-decay * t)))
    return frames


def _tick_frames(freq: float, decay: float, seconds: float, gain: float = 0.8) -> list[int]:
    """One resonant ping: claves, wood blocks and triangles live here."""
    length = int(SAMPLE_RATE * seconds)
    frames: list[int] = []
    for index in range(length):
        t = index / SAMPLE_RATE
        frames.append(_clamp(math.sin(2 * math.pi * freq * t) * math.exp(-decay * t) * gain))
    return frames


def _timbale_high_frames() -> list[int]:
    """The low timbale recipe on a higher shell: click, ring, and body."""
    length = int(SAMPLE_RATE * 0.3)
    noise = _noise(0x6565)
    frames: list[int] = []
    for index in range(length):
        t = index / SAMPLE_RATE
        body = math.sin(2 * math.pi * 262 * t) * math.exp(-12 * t) * 0.55
        ring = (
            math.sin(2 * math.pi * 262 * 2.76 * t) * 0.2
            + math.sin(2 * math.pi * 262 * 5.4 * t) * 0.08
        ) * math.exp(-18 * t)
        click = next(noise) * math.exp(-220 * t) * 0.3
        frames.append(_clamp(body + ring + click))
    return frames


def _vibraslap_frames() -> list[int]:
    """A metal rattle that shudders at a fixed rate while it dies away."""
    length = int(SAMPLE_RATE * 0.55)
    noise = _noise(0x5858)
    frames: list[int] = []
    previous = 0.0
    for index in range(length):
        t = index / SAMPLE_RATE
        hiss = next(noise)
        highpass = hiss - previous
        previous = hiss
        rattle = 0.55 + 0.45 * math.sin(2 * math.pi * 26 * t)
        frames.append(_clamp(highpass * rattle * math.exp(-5.5 * t) * 0.6))
    return frames


def _cabasa_frames() -> list[int]:
    """Beads dragging around a ribbed shell: gated hiss with a fast ripple."""
    length = int(SAMPLE_RATE * 0.22)
    noise = _noise(0x6969)
    frames: list[int] = []
    previous = 0.0
    for index in range(length):
        t = index / SAMPLE_RATE
        hiss = next(noise)
        highpass = hiss - previous
        previous = hiss
        ripple = 0.65 + 0.35 * math.sin(2 * math.pi * 42 * t)
        frames.append(_clamp(highpass * ripple * math.exp(-13 * t) * 0.55))
    return frames


def _whistle_frames(seconds: float, decay: float) -> list[int]:
    """A blown tone near 2.3 kHz with a breath wobble once it settles."""
    length = int(SAMPLE_RATE * seconds)
    noise = _noise(0x7172)
    frames: list[int] = []
    phase = 0.0
    for index in range(length):
        t = index / SAMPLE_RATE
        freq = 2300.0 + 45.0 * math.sin(2 * math.pi * 5.5 * t) * min(1.0, t / 0.05)
        phase += 2 * math.pi * freq / SAMPLE_RATE
        attack = min(1.0, t / 0.015)
        breath = next(noise) * 0.05
        frames.append(_clamp((math.sin(phase) * 0.55 + breath) * attack * math.exp(-decay * t)))
    return frames


def _guiro_frames(seconds: float, rate_hz: float, seed: int) -> list[int]:
    """A stick dragged over ridges: banded noise chopped at the ridge rate."""
    length = int(SAMPLE_RATE * seconds)
    noise = _noise(seed)
    frames: list[int] = []
    band = 0.0
    previous = 0.0
    for index in range(length):
        t = index / SAMPLE_RATE
        raw = next(noise)
        band += 0.4 * ((raw - previous) - band)
        previous = raw
        gate = (0.5 + 0.5 * math.sin(2 * math.pi * rate_hz * t)) ** 2
        envelope = min(1.0, t / 0.01) * min(1.0, (seconds - t) / 0.03)
        frames.append(_clamp(band * 2.0 * gate * envelope * 0.7))
    return frames


def _cuica_frames(start_hz: float, end_hz: float, seconds: float, decay: float) -> list[int]:
    """A rubbed stick bending the skin: a gliding tone with a fast rub."""
    length = int(SAMPLE_RATE * seconds)
    frames: list[int] = []
    phase = 0.0
    for index in range(length):
        t = index / SAMPLE_RATE
        freq = end_hz + (start_hz - end_hz) * math.exp(-t / 0.09)
        phase += 2 * math.pi * freq / SAMPLE_RATE
        rub = 0.75 + 0.25 * math.sin(2 * math.pi * 38 * t)
        frames.append(_clamp(math.sin(phase) * rub * math.exp(-decay * t) * 0.6))
    return frames


_FRAMES = {
    CONGA_HIGH_NOTE: lambda: _conga_frames(330.0),
    CONGA_LOW_NOTE: lambda: _conga_frames(220.0),
    TAMBOURINE_NOTE: _tambourine_frames,
    SPLASH_NOTE: lambda: _metal_frames((4200.0, 5600.0, 7300.0, 9100.0), 20.0, 0.3, 0x5555, 0.3, 0.3),
    COWBELL_NOTE: _cowbell_frames,
    CRASH_NOTE: lambda: _metal_frames((3100.0, 4300.0, 5900.0, 7600.0, 9800.0), 4.2, 1.2, 0x5757, 0.35, 0.3),
    VIBRASLAP_NOTE: _vibraslap_frames,
    RIDE_BELL_NOTE: lambda: _metal_frames((3400.0, 4700.0, 6300.0, 8100.0), 3.8, 1.0, 0x5959, 0.12, 0.4),
    BONGO_HIGH_NOTE: lambda: _skin_frames(440.0, 17.0, 0.22, 0x6060),
    BONGO_LOW_NOTE: lambda: _skin_frames(330.0, 15.0, 0.25, 0x6161),
    CONGA_MUTE_HIGH_NOTE: _conga_mute_frames,
    TIMBALE_HIGH_NOTE: _timbale_high_frames,
    TIMBALE_LOW_NOTE: _timbale_low_frames,
    AGOGO_HIGH_NOTE: lambda: _metal_frames((880.0, 1175.0, 1560.0), 26.0, 0.16, 0x6767, 0.05, 0.4),
    AGOGO_LOW_NOTE: lambda: _metal_frames((659.0, 880.0, 1175.0), 24.0, 0.18, 0x6868, 0.05, 0.4),
    CABASA_NOTE: _cabasa_frames,
    WHISTLE_SHORT_NOTE: lambda: _whistle_frames(0.18, 13.0),
    WHISTLE_LONG_NOTE: lambda: _whistle_frames(0.6, 2.8),
    GUIRO_SHORT_NOTE: lambda: _guiro_frames(0.18, 55.0, 0x7373),
    GUIRO_LONG_NOTE: lambda: _guiro_frames(0.45, 42.0, 0x7474),
    CLAVES_NOTE: _claves_frames,
    WOOD_HIGH_NOTE: lambda: _tick_frames(1560.0, 105.0, 0.08, 0.85),
    WOOD_LOW_NOTE: lambda: _tick_frames(1040.0, 95.0, 0.09, 0.85),
    CUICA_MUTE_NOTE: lambda: _cuica_frames(700.0, 450.0, 0.18, 17.0),
    CUICA_OPEN_NOTE: lambda: _cuica_frames(820.0, 500.0, 0.4, 7.5),
    TRIANGLE_MUTE_NOTE: lambda: _tick_frames(7900.0, 130.0, 0.09, 0.7),
    TRIANGLE_OPEN_NOTE: lambda: _tick_frames(7400.0, 6.5, 0.8, 0.6),
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
