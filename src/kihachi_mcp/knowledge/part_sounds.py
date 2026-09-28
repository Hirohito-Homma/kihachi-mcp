"""Tone and effect chains for every part, written in the units Live displays.

Genre recipes (sound_recipes.py) tune the instrument of a few parts by hand.
This module covers the rest: a playable patch for the new melodic parts, and
an effect chain for every part that goes on after the instrument.

Values are given as Live shows them (Hz, dB, ms, %), and converted to the 0..1
the device takes using the dial readings the probe saved from Live 12.4.5
(knowledge/live_device_parameters.json). A name or a switch item that is not
in that table raises at import, so a recipe cannot reach Live misspelt.
"""

from __future__ import annotations

import math
import re

from kihachi_mcp.knowledge.sound_recipes import DeviceRecipe, Setting
from kihachi_mcp.services.device_probe import load_parameters

_TABLE = load_parameters().get("devices") or {}
_NUMBER = re.compile(r"-?inf|-?\d+(?:\.\d+)?")


def _reading(text: str) -> float:
    """One dial reading as a number in base units: Hz, ms, dB, %, ratio."""
    match = _NUMBER.search(text)
    if match is None:
        raise ValueError(f"cannot read '{text}'")
    token = match.group(0)
    if token.endswith("inf"):
        return -math.inf if token.startswith("-") else math.inf
    value = float(token)
    if "kHz" in text or re.search(r"\d s$", text):
        value *= 1000
    return value


def _parameter(device: str, name: str) -> dict:
    for item in (_TABLE.get(device) or {}).get("parameters") or []:
        if item["name"] == name:
            return item
    raise KeyError(f"{device} has no parameter '{name}' in the probed table")


def at(device: str, name: str, target: float) -> Setting:
    """The setting that makes the dial show ``target``, between two readings."""
    readings = [_reading(text) for text in _parameter(device, name).get("displays") or []]
    if len(readings) < 2:
        raise ValueError(f"{device} {name} has no dial readings")
    steps = len(readings) - 1
    for index in range(steps):
        low, high = readings[index], readings[index + 1]
        if not low <= target <= high:
            continue
        if high == low or math.isinf(low):
            fraction = 1.0 if math.isinf(low) else 0.0
        elif low > 0 and high / low > 1.5:
            fraction = math.log(target / low) / math.log(high / low)
        else:
            fraction = (target - low) / (high - low)
        return Setting(name, value=round((index + fraction) / steps, 4))
    raise ValueError(f"{device} {name}: {target} is outside {readings[0]}..{readings[-1]}")


def item(device: str, name: str, choice: str) -> Setting:
    if choice not in _parameter(device, name)["value_items"]:
        raise ValueError(f"{device} {name} has no setting '{choice}'")
    return Setting(name, item=choice)


def value(device: str, name: str, amount: float) -> Setting:
    """A raw 0..1 position, for knobs the probe read without dial readings."""
    _parameter(device, name)
    return Setting(name, value=amount)


def step(device: str, name: str, index: int) -> Setting:
    """Step ``index`` of a knob that moves in whole steps over min..max.

    Live floors a stepped knob, and 5/6 in floating point lands just under
    step 5, so this aims at the middle of the step and expects its start.
    """
    parameter = _parameter(device, name)
    span = round(float(parameter["max"]) - float(parameter["min"]))
    if not 0 <= index <= span:
        raise ValueError(f"{device} {name} has steps 0..{span}")
    return Setting(
        name,
        value=min(1.0, round((index + 0.5) / span, 4)),
        lands_at=round(index / span, 4),
    )


def _eq(high_pass_hz: float, low_pass_hz: float | None = None) -> DeviceRecipe:
    """Band 1 as a 12 dB high-pass; band 8 as a 12 dB low-pass when asked."""
    eq = "EQ Eight"
    settings = [
        item(eq, "1 Filter Type A", "High Pass 12dB"),
        at(eq, "1 Frequency A", high_pass_hz),
    ]
    if low_pass_hz is not None:
        settings += [
            item(eq, "8 Filter On A", "On"),
            item(eq, "8 Filter Type A", "Low Pass 12dB"),
            at(eq, "8 Frequency A", low_pass_hz),
        ]
    return DeviceRecipe(eq, tuple(settings))


def _reverb(wet_percent: float, decay_ms: float, size_percent: float = 50) -> DeviceRecipe:
    rv = "Hybrid Reverb"
    return DeviceRecipe(
        rv,
        (
            at(rv, "Dry/Wet", wet_percent),
            at(rv, "Decay", decay_ms),
            at(rv, "Size", size_percent),
            # Keep reverb out of the low end, where the club system is loudest.
            at(rv, "EQ Lo Freq", 200),
        ),
    )


def _echo(wet_percent: float, feedback_percent: float) -> DeviceRecipe:
    ec = "Echo"
    return DeviceRecipe(
        ec,
        (
            at(ec, "Dry Wet", wet_percent),
            at(ec, "Feedback", feedback_percent),
            at(ec, "HP Freq", 300),
            at(ec, "LP Freq", 5000),
            item(ec, "Channel Mode", "Ping Pong"),
        ),
    )


def _compressor(threshold_db: float, ratio: float, attack_ms: float, release_ms: float) -> DeviceRecipe:
    cp = "Compressor"
    return DeviceRecipe(
        cp,
        (
            at(cp, "Threshold", threshold_db),
            at(cp, "Ratio", ratio),
            at(cp, "Attack", attack_ms),
            at(cp, "Release", release_ms),
        ),
    )


def _saturator(drive_db: float, kind: str, wet_percent: float = 100) -> DeviceRecipe:
    st = "Saturator"
    return DeviceRecipe(
        st,
        (
            item(st, "Type", kind),
            at(st, "Drive", drive_db),
            at(st, "Dry/Wet", wet_percent),
        ),
    )


#: Effects after each part's instrument, first to last.
PART_EFFECTS: dict[str, tuple[DeviceRecipe, ...]] = {
    "Kick": (_eq(30), _saturator(3, "Soft Sine")),
    "Snare": (_eq(150), _compressor(-18, 4, 10, 127), _reverb(15, 900, 30)),
    "Hats": (_eq(400),),
    "OpenHat": (_eq(300),),
    "Perc": (_eq(250), _reverb(15, 1380, 40)),
    "Sub": (_eq(25, low_pass_hz=120),),
    # The sub carries everything below 90 Hz; the bass keeps the bite above it.
    "Bass": (_eq(90), _compressor(-20, 4, 10, 50), _saturator(6, "Analog Clip", 60)),
    "Stab": (_eq(200), _reverb(20, 2320)),
    "Pad": (_eq(250, low_pass_hz=8000), _reverb(35, 3500, 70)),
    "Arp": (_eq(300), _echo(22, 30)),
    "Guitar": (
        _eq(200),
        DeviceRecipe(
            "Auto Filter",
            (
                # An envelope-following band-pass: the funk auto-wah.
                item("Auto Filter", "Filter Type", "Band-pass"),
                at("Auto Filter", "Frequency", 632),
                at("Auto Filter", "Resonance", 30),
                at("Auto Filter", "Env Amount", 40),
            ),
        ),
    ),
    "Horn": (_eq(150), _saturator(6, "Analog Clip", 50), _reverb(18, 1380, 40)),
    "Lead": (_eq(200), _echo(18, 30)),
    "Vocal": (_eq(250), _echo(25, 30), _reverb(25, 2320)),
    "FX": (_eq(40), _reverb(30, 4900, 80)),
}

#: Fader (dB) and pan (-1 left .. 1 right) per part, for a club mix. The kick
#: sits highest and everything below 120 Hz stays centred; the rest is set
#: under it so the master keeps about 6 dB of headroom for mastering.
PART_MIX: dict[str, tuple[float, float]] = {
    "Kick": (-6.0, 0.0),
    "Sub": (-12.0, 0.0),
    "Bass": (-13.0, 0.0),
    "Snare": (-10.0, 0.0),
    "Hats": (-16.0, 0.15),
    "OpenHat": (-18.0, -0.15),
    "Perc": (-18.0, -0.3),
    "Stab": (-14.0, 0.0),
    "Pad": (-18.0, 0.0),
    "Arp": (-18.0, 0.3),
    "Guitar": (-16.0, -0.35),
    "Horn": (-14.0, 0.25),
    "Lead": (-14.0, 0.0),
    "Vocal": (-15.0, 0.0),
    "FX": (-18.0, 0.0),
}

def _ducker(threshold_db: float, ratio: float, release_ms: float) -> DeviceRecipe:
    cp = "Compressor"
    return DeviceRecipe(
        cp,
        (
            item(cp, "S/C On", "On"),
            at(cp, "Threshold", threshold_db),
            at(cp, "Ratio", ratio),
            at(cp, "Attack", 1.0),
            at(cp, "Release", release_ms),
        ),
    )


#: A Compressor keyed from the kick, appended last on each of these tracks, so
#: the low end gets out of the kick's way. The key is the kick track after its
#: effects and before its fader, so MIX levels do not change the ducking.
SIDECHAIN_DUCKING: dict[str, DeviceRecipe] = {
    "Sub": _ducker(-20, 5, 127),
    "Bass": _ducker(-18, 3.33, 127),
    "Pad": _ducker(-14, 2, 258),
}

#: Club mastering on the master track, first to last. Glue Compressor's
#: Attack, Ratio and Release move in steps: Attack 0.01/0.1/0.3/1/3/10/30 ms,
#: Ratio 2/4/10, Release 0.1/0.2/0.4/0.6/0.8/1.2 s/Auto.
MASTER_CHAIN: tuple[DeviceRecipe, ...] = (
    DeviceRecipe(
        "EQ Eight",
        (
            item("EQ Eight", "1 Filter Type A", "High Pass 12dB"),
            at("EQ Eight", "1 Frequency A", 25),
        ),
    ),
    DeviceRecipe(
        "Glue Compressor",
        (
            at("Glue Compressor", "Threshold", -12),
            step("Glue Compressor", "Ratio", 0),
            step("Glue Compressor", "Attack", 5),
            step("Glue Compressor", "Release", 6),
        ),
    ),
    _saturator(2, "Soft Sine", 30),
    DeviceRecipe(
        "Utility",
        (
            # Everything under 120 Hz in mono: club systems sum the low end.
            item("Utility", "Bass Mono", "On"),
            at("Utility", "Bass Freq", 120),
        ),
    ),
    DeviceRecipe(
        "Limiter",
        (
            item("Limiter", "Mode", "True Peak"),
            at("Limiter", "Ceiling", -1.0),
            at("Limiter", "Input Gain", 4.8),
        ),
    ),
)

#: A playable patch for parts no genre recipe covers. The instrument named here
#: must be the one live_device_catalog.suggest_instrument picks for the part.
PART_INSTRUMENTS: dict[str, DeviceRecipe] = {
    "Pad": DeviceRecipe(
        "Wavetable",
        (
            value("Wavetable", "Amp Attack", 0.35),
            value("Wavetable", "Amp Sustain", 0.85),
            value("Wavetable", "Amp Release", 0.6),
            item("Wavetable", "Osc 2 On", "On"),
            value("Wavetable", "Osc 2 Detune", 0.54),
            value("Wavetable", "Flt 1 Freq", 0.6),
            value("Wavetable", "Unison Amount", 0.5),
        ),
    ),
    "Arp": DeviceRecipe(
        "Drift",
        (
            value("Drift", "Env 1 Decay", 0.25),
            value("Drift", "Env 1 Sustain", 0.2),
            value("Drift", "Env 1 Release", 0.25),
            value("Drift", "LP Freq", 0.7),
        ),
    ),
    "Guitar": DeviceRecipe(
        "Drift",
        (
            item("Drift", "Osc 1 Wave", "Pulse"),
            value("Drift", "Env 1 Decay", 0.2),
            value("Drift", "Env 1 Sustain", 0.0),
            value("Drift", "Env 1 Release", 0.15),
            value("Drift", "LP Freq", 0.65),
        ),
    ),
    "Horn": DeviceRecipe(
        "Analog",
        (
            value("Analog", "F1 Freq", 0.45),
            value("Analog", "F1 Freq < Env", 0.75),
            value("Analog", "FEG1 Attack", 0.25),
            value("Analog", "FEG1 Decay", 0.4),
        ),
    ),
    "Vocal": DeviceRecipe(
        "Wavetable",
        (
            value("Wavetable", "Osc 1 Pos", 0.7),
            item("Wavetable", "Flt 1 Type", "Bandpass"),
            value("Wavetable", "Flt 1 Freq", 0.55),
            value("Wavetable", "Amp Release", 0.3),
        ),
    ),
}
