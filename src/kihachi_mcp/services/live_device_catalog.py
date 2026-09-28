"""Catalogue of the Live stock devices KIHACHI is allowed to load.

Scope is deliberately narrow. Only devices shipped with Ableton Live are
candidates, because they are the only ones that behave the same on macOS and
Windows without extra installation.

The catalogue is a candidate list, never an availability claim. Live editions
(Intro, Standard, Suite) and versions ship different device sets, so the only
authoritative source is the availability list read back from the running Live
instance. ``resolve`` therefore refuses unknown or unavailable devices instead
of substituting a similar one.

External plugins (VST3, AU, CLAP) are out of scope for this milestone. The
future design is an explicit allow list, documented in
``docs/adr/0006-ableton-live-automation.md``.
"""

from dataclasses import dataclass
from typing import Any

CATEGORY_INSTRUMENT = "instrument"
CATEGORY_EFFECT = "audio_effect"


@dataclass(frozen=True)
class LiveStockDevice:
    """One Live stock device KIHACHI may request."""

    name: str
    category: str
    role: str

    def to_dict(self) -> dict[str, Any]:
        """Serialize the catalogue entry to JSON-compatible data."""
        return {"name": self.name, "category": self.category, "role": self.role}


STOCK_DEVICES: tuple[LiveStockDevice, ...] = (
    LiveStockDevice("Drum Rack", CATEGORY_INSTRUMENT, "drums"),
    LiveStockDevice("Simpler", CATEGORY_INSTRUMENT, "sampler"),
    LiveStockDevice("Operator", CATEGORY_INSTRUMENT, "fm_synth"),
    LiveStockDevice("Wavetable", CATEGORY_INSTRUMENT, "wavetable_synth"),
    LiveStockDevice("Drift", CATEGORY_INSTRUMENT, "analog_synth"),
    # Suite only. The availability list read from Live decides whether it loads.
    LiveStockDevice("Analog", CATEGORY_INSTRUMENT, "analog_synth"),
    LiveStockDevice("Auto Filter", CATEGORY_EFFECT, "filter"),
    LiveStockDevice("EQ Eight", CATEGORY_EFFECT, "equaliser"),
    LiveStockDevice("Compressor", CATEGORY_EFFECT, "dynamics"),
    LiveStockDevice("Saturator", CATEGORY_EFFECT, "saturation"),
    LiveStockDevice("Echo", CATEGORY_EFFECT, "delay"),
    LiveStockDevice("Hybrid Reverb", CATEGORY_EFFECT, "reverb"),
)

STOCK_DEVICE_NAMES = frozenset(device.name for device in STOCK_DEVICES)

DEFAULT_SUITE_DEVICES: tuple[str, ...] = tuple(
    device.name for device in STOCK_DEVICES
)

_ROLE_BY_TRACK_KEYWORD: tuple[tuple[str, str], ...] = (
    ("kick", "Drum Rack"),
    ("drum", "Drum Rack"),
    ("perc", "Drum Rack"),
    ("hat", "Drum Rack"),
    ("clap", "Drum Rack"),
    ("snare", "Drum Rack"),
    ("fx", "Drum Rack"),
    ("bass", "Operator"),
    ("sub", "Operator"),
    ("pad", "Wavetable"),
    ("chord", "Wavetable"),
    ("key", "Wavetable"),
    ("stab", "Wavetable"),
    ("lead", "Drift"),
    ("arp", "Drift"),
    ("pluck", "Drift"),
    ("vocal", "Wavetable"),
    ("guitar", "Drift"),
    ("horn", "Analog"),
)

DEFAULT_INSTRUMENT = "Simpler"


class LiveDeviceUnavailableError(Exception):
    """Raised when a requested device is not loadable in this Live install."""


def suggest_instrument(track_name: str) -> str:
    """Return the stock instrument KIHACHI would request for a track name.

    This is a naming heuristic for planning only. It never bypasses the
    availability check performed against the live Set.
    """
    lowered = track_name.lower()
    for keyword, device_name in _ROLE_BY_TRACK_KEYWORD:
        if keyword in lowered:
            return device_name
    return DEFAULT_INSTRUMENT


def resolve(device_name: str, available: frozenset[str]) -> str:
    """Return the device name only if it is a stock device Live can load.

    Raises instead of falling back, so a missing device surfaces as a blocked
    plan rather than a silently different sound.
    """
    if device_name not in STOCK_DEVICE_NAMES:
        raise LiveDeviceUnavailableError(
            f"'{device_name}' is not in the KIHACHI stock device catalogue; "
            "external plugins are out of scope"
        )
    if device_name not in available:
        raise LiveDeviceUnavailableError(
            f"'{device_name}' is not available in this Ableton Live installation; "
            "no substitute will be chosen automatically"
        )
    return device_name


def catalogue() -> list[dict[str, Any]]:
    """Return the candidate catalogue as JSON-compatible data."""
    return [device.to_dict() for device in STOCK_DEVICES]
