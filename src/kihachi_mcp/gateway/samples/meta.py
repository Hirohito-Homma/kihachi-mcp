"""Filename metadata and audio headers. Analysis fields stay empty until measured."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

AUDIO_EXTENSIONS = frozenset({".wav", ".aiff", ".aif", ".flac"})
ROLES = (
    "kick",
    "snare",
    "clap",
    "closed_hat",
    "open_hat",
    "percussion",
    "top_loop",
    "drum_loop",
    "bass",
    "vocal",
    "vocal_chop",
    "fx",
    "riser",
    "downlifter",
    "impact",
    "texture",
    "dub",
    "synth",
    "other",
)
DRUM_ROLES = frozenset(
    {
        "kick",
        "snare",
        "clap",
        "closed_hat",
        "open_hat",
        "percussion",
        "top_loop",
        "drum_loop",
        "fx",
        "riser",
        "downlifter",
        "impact",
    }
)
TONAL_ROLES = frozenset({"bass", "vocal", "vocal_chop", "synth", "dub"})

_ROLE_PHRASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("vocal_chop", ("vocal chop", "vocalchop", "vox chop")),
    ("open_hat", ("open hat", "openhh")),
    ("closed_hat", ("closed hat", "closedhh", "chh", "hihat", "hi hat", "hat")),
    ("downlifter", ("downlifter", "down lifter")),
    ("riser", ("riser", "uplifter")),
    ("impact", ("impact",)),
    ("kick", ("kick",)),
    ("snare", ("snare",)),
    ("clap", ("clap",)),
    ("percussion", ("percussion", "perc", "shaker", "conga", "bongo", "tambourine")),
    ("top_loop", ("top loop", "tops")),
    ("drum_loop", ("drum loop", "drumloop", "breakbeat", "break")),
    ("bass", ("bass",)),
    ("vocal", ("vocal", "vox", "voice")),
    ("texture", ("texture", "atmos", "atmosphere")),
    ("dub", ("dub",)),
    ("synth", ("synth", "chord", "stab")),
    ("fx", ("fx", "sfx")),
)
_KEY_RE = re.compile(
    r"(?i)(?<![a-z0-9])([a-g])\s*([#b]|s)?\s*(min|minor|maj|major|m)(?![a-z])"
)
_BPM_LABELED = re.compile(r"(?i)(?<!\d)(\d{2,3})\s*bpm\b")
_BPM_PLAIN = re.compile(r"(?<!\d)(\d{2,3})(?!\d)")
_PITCH = {"c": 0, "d": 2, "e": 4, "f": 5, "g": 7, "a": 9, "b": 11}


def filename_metadata(filename: str) -> dict[str, Any]:
    """Read only what the filename spells. Unstated musical facts stay null."""
    stem = Path(filename).stem
    text = re.sub(r"[^a-z0-9#]+", " ", stem.lower()).strip()
    return {
        "role": _role(text),
        "bpm": _bpm(stem),
        "key": _key(stem),
        "kind": _kind(text),
        "tags": _tags(text),
    }


def header_metadata(path: Path) -> dict[str, Any] | None:
    """Read the audio header. The file is not opened for writing."""
    import soundfile as sf

    try:
        info = sf.info(str(path))
    except (OSError, RuntimeError, ValueError):
        return None
    duration = float(info.duration)
    if duration <= 0:
        return None
    return {
        "duration_seconds": round(duration, 6),
        "sample_rate": int(info.samplerate),
        "channels": int(info.channels),
    }


def public_sample(record: dict[str, Any]) -> dict[str, Any]:
    """Drop the local path. Callers outside this machine do not need it."""
    filename_meta = record.get("filename_metadata") if isinstance(record.get("filename_metadata"), dict) else {}
    analysis = record.get("analysis") if isinstance(record.get("analysis"), dict) else {}
    return {
        "sample_id": record.get("sample_id"),
        "filename": record.get("filename"),
        "extension": record.get("extension"),
        "duration_seconds": record.get("duration_seconds"),
        "sample_rate": record.get("sample_rate"),
        "channels": record.get("channels"),
        "file_size": record.get("file_size"),
        "role": filename_meta.get("role"),
        "kind": filename_meta.get("kind"),
        "loop_or_oneshot": filename_meta.get("kind"),
        "metadata_source": "filename",
        "bpm": filename_meta.get("bpm"),
        "bpm_source": "filename" if filename_meta.get("bpm") is not None else None,
        "analysis_bpm": analysis.get("bpm"),
        "key": filename_meta.get("key"),
        "key_source": "filename" if filename_meta.get("key") else None,
        "analysis_key": analysis.get("key"),
        "tags": list(filename_meta.get("tags") or []),
        "energy": analysis.get("energy"),
        "source": record.get("source"),
        "parent_folder": record.get("parent_folder"),
        "pack": None,
        "creator": None,
        "license": None,
    }


def _role(text: str) -> str:
    for role, phrases in _ROLE_PHRASES:
        if any(_has(text, phrase) for phrase in phrases):
            return role
    return "other"


def _has(text: str, phrase: str) -> bool:
    return re.search(rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])", text) is not None


def _bpm(stem: str) -> int | None:
    labeled = [int(match) for match in _BPM_LABELED.findall(stem) if 60 <= int(match) <= 200]
    if len(labeled) == 1:
        return labeled[0]
    if len(labeled) > 1:
        return None
    plain = [int(match) for match in _BPM_PLAIN.findall(stem) if 60 <= int(match) <= 200]
    if len(plain) == 1:
        return plain[0]
    return None


def _key(stem: str) -> str | None:
    match = _KEY_RE.search(stem)
    if match is None:
        return None
    letter, accidental, quality = match.group(1).lower(), (match.group(2) or "").lower(), match.group(3).lower()
    pitch = _PITCH[letter]
    if accidental in {"#", "s"}:
        pitch += 1
    elif accidental == "b":
        pitch -= 1
    pitch %= 12
    names = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
    mode = "minor" if quality in {"m", "min", "minor"} else "major"
    return f"{names[pitch]} {mode}"


def _kind(text: str) -> str | None:
    if _has(text, "oneshot") or _has(text, "one shot") or _has(text, "one-shot"):
        return "oneshot"
    if _has(text, "loop"):
        return "loop"
    return None


def _tags(text: str) -> list[str]:
    skip = {"wav", "aiff", "aif", "flac", "bpm", "loop", "oneshot", "one", "shot"}
    tags = []
    for token in text.split():
        if len(token) < 3 or token in skip or token.isdigit():
            continue
        if token not in tags:
            tags.append(token)
    return tags
