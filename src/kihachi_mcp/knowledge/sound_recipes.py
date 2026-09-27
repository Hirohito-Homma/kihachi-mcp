"""Sound recipes: which stock device plays a part, and how its knobs are set.

A recipe is data, not a model's guess. The local model never picks a filter
cutoff; it can only move the few named tone controls below (brighter, shorter,
more delay) within fixed steps, and the recipe says what each step turns.

Parameters are named, never indexed. Live can reorder a device's parameter list
between versions; a name that no longer exists makes the apply stop with an
error instead of turning some other knob.

Values are what the device reads back: a switch by one of its ``value_items``,
a continuous knob as 0..1 of its min..max (which is not the dial's display
scale -- 0.58 of Wavetable's filter is 1.11 kHz).

The dub techno recipe is the one tuned by hand in Live 12.4.5 on 2026-09-27
(candidate f7e7ab86) and read back parameter by parameter.
"""

from __future__ import annotations

from dataclasses import dataclass, field

TONE_CONTROLS = ("brightness", "length", "delay")


@dataclass(frozen=True)
class Setting:
    parameter: str
    #: 0..1 of the parameter's range, for a continuous knob.
    value: float | None = None
    #: One of the parameter's value_items, for a switch.
    item: str | None = None

    def __post_init__(self) -> None:
        if (self.value is None) == (self.item is None):
            raise ValueError(f"{self.parameter}: give exactly one of value or item")
        if self.value is not None and not 0.0 <= self.value <= 1.0:
            raise ValueError(f"{self.parameter}: value must be within 0..1")


@dataclass(frozen=True)
class DeviceRecipe:
    device: str
    settings: tuple[Setting, ...] = ()


@dataclass(frozen=True)
class ToneStep:
    """How far one step of a tone control moves one continuous knob."""

    part: str
    device: str
    parameter: str
    per_step: float


@dataclass(frozen=True)
class PartRecipe:
    instrument: DeviceRecipe
    effects: tuple[DeviceRecipe, ...] = ()

    @property
    def chain(self) -> tuple[DeviceRecipe, ...]:
        """Devices in the order they are inserted on the track."""
        return (self.instrument, *self.effects)


@dataclass(frozen=True)
class SoundRecipe:
    name: str
    parts: dict[str, PartRecipe]
    tone: dict[str, tuple[ToneStep, ...]] = field(default_factory=dict)
    #: Core Library drum kits in order of preference, by drum part. Loading one
    #: needs Live's browser, so it goes through AbletonGPT's Remote Script.
    kits: dict[str, tuple[str, ...]] = field(default_factory=dict)

    def has_effect(self, part: str, device: str) -> bool:
        recipe = self.parts.get(part)
        return recipe is not None and any(item.device == device for item in recipe.effects)


def _s(parameter: str, value: float) -> Setting:
    return Setting(parameter, value=value)


def _i(parameter: str, item: str) -> Setting:
    return Setting(parameter, item=item)


DUB_TECHNO = SoundRecipe(
    name="dub_techno",
    parts={
        "Bass": PartRecipe(
            DeviceRecipe(
                "Analog",
                (
                    _i("Voices", "Mono"),
                    _i("OSC2 Shape", "Sine"),
                    _s("F1 Freq", 0.4),  # 429 Hz
                    _s("F1 Resonance", 0.2),
                    _s("F1 Freq < Env", 0.65),  # a light touch of the envelope
                    _s("FEG1 Decay", 0.45),  # 189 ms
                    _s("FEG1 Sustain", 0.1),
                    _s("AEG1 Decay", 0.5),  # 283 ms
                    _s("AEG1 Sustain", 0.35),
                    _s("AEG1 Rel", 0.4),  # 126 ms
                    _s("AMP1 Level", 0.75),  # -5.4 dB
                ),
            )
        ),
        "Stab": PartRecipe(
            DeviceRecipe(
                "Wavetable",
                (
                    _s("Osc 1 Pos", 0.35),
                    _i("Osc 2 On", "On"),
                    _s("Osc 2 Detune", 0.56),  # 6 ct
                    _s("Osc 2 Pos", 0.55),
                    _i("Flt 1 Slope", "24"),
                    _s("Flt 1 Freq", 0.58),  # 1.11 kHz
                    _s("Flt 1 Res", 0.3),
                    _s("Amp Decay", 0.33),  # 239 ms
                    _s("Amp Sustain", 0.15),  # -33 dB
                    _s("Amp Release", 0.33),
                ),
            ),
            effects=(
                DeviceRecipe(
                    "Echo",
                    (
                        # Echo already defaults to a dotted eighth, synced.
                        _s("Dry Wet", 0.35),
                        _s("Feedback", 0.4),  # 60 %
                        _s("LP Freq", 0.68),  # 2.19 kHz
                        _s("HP Freq", 0.3),  # 159 Hz
                        _i("Channel Mode", "Ping Pong"),
                        _s("Reverb Level", 0.25),
                        _s("Reverb Decay", 0.6),
                    ),
                ),
            ),
        ),
    },
    tone={
        "brightness": (
            ToneStep("Stab", "Wavetable", "Flt 1 Freq", 0.06),
            ToneStep("Bass", "Analog", "F1 Freq", 0.04),
        ),
        "length": (
            ToneStep("Stab", "Wavetable", "Amp Decay", 0.05),
            ToneStep("Stab", "Wavetable", "Amp Release", 0.05),
            ToneStep("Bass", "Analog", "AEG1 Decay", 0.04),
        ),
        "delay": (
            ToneStep("Stab", "Echo", "Dry Wet", 0.08),
            ToneStep("Stab", "Echo", "Feedback", 0.05),
        ),
    },
    kits={
        "Kick": ("909 Core Kit", "AG Techno Kit", "707 Core Kit", "606 Core Kit"),
        "Hats": ("909 Core Kit", "AG Techno Kit", "707 Core Kit", "606 Core Kit"),
    },
)

RECIPES: dict[str, SoundRecipe] = {"dub_techno": DUB_TECHNO}


def recipe_for(genre: str) -> SoundRecipe | None:
    """The recipe for a genre slug, or None to keep the stock defaults."""
    return RECIPES.get(genre)


def tuned(recipe: SoundRecipe, steps: dict[str, int]) -> SoundRecipe:
    """The recipe with its tone controls moved by -2..+2 steps each.

    Only knobs the recipe already sets move, and every value stays inside
    0..1: a tone control can shade a sound, never break the recipe.
    """
    offsets: dict[tuple[str, str, str], float] = {}
    for control, amount in steps.items():
        if control not in TONE_CONTROLS:
            raise ValueError(f"unknown tone control '{control}'")
        amount = max(-2, min(2, int(amount)))
        for step in recipe.tone.get(control, ()):
            key = (step.part, step.device, step.parameter)
            offsets[key] = offsets.get(key, 0.0) + step.per_step * amount
    if not offsets:
        return recipe
    parts: dict[str, PartRecipe] = {}
    for part, part_recipe in recipe.parts.items():
        chain = []
        for device in part_recipe.chain:
            settings = []
            for setting in device.settings:
                shift = offsets.get((part, device.device, setting.parameter))
                if shift and setting.value is not None:
                    value = round(max(0.0, min(1.0, setting.value + shift)), 3)
                    setting = Setting(setting.parameter, value=value)
                settings.append(setting)
            chain.append(DeviceRecipe(device.device, tuple(settings)))
        parts[part] = PartRecipe(chain[0], tuple(chain[1:]))
    return SoundRecipe(recipe.name, parts, recipe.tone, recipe.kits)
