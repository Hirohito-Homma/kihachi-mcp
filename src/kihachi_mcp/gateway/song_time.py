"""Beat clock shared by the song tools and AbletonGPT.

AbletonGPT already defines the unit. ``create_midi_clip`` stores each note as
``start_time`` and ``duration`` in beats, and ``copy_session_clip_to_arrangement``
places a clip at ``destination_time_beats``. Those beats are quarter notes.
This module only accepts a 4/4 bar, which is therefore 4 beats. Bar 1 starts
at beat 0. Nothing here converts seconds, samples, or ticks.
"""

from __future__ import annotations

import math
from typing import Any

BEATS_PER_BAR = 4
MAX_NOTES_PER_CLIP = 4096


class NoteError(ValueError):
    """A note is not usable as an AbletonGPT beat position."""


def swing_beat(start: float, swing: float) -> float:
    """Delay an off-16th. ``swing`` 0.50 leaves the grid alone.

    0.54 places the second 16th of each 8th at 54% of that 8th (0.27 beats
    instead of 0.25). Downbeats and straight 8ths stay put.
    """
    if swing <= 0.5:
        return start
    pair = math.floor((start + 1e-9) / 0.5)
    local = start - pair * 0.5
    if abs(local - 0.25) <= 0.001:
        return pair * 0.5 + 0.5 * swing
    return start


def bar_start_beat(bar: int) -> float:
    """1-based bar number to the beat where that bar begins."""
    if isinstance(bar, bool) or not isinstance(bar, int) or bar < 1:
        raise NoteError("小節番号は 1 から始まる整数です")
    return float((bar - 1) * BEATS_PER_BAR)


def validate_note(note: object, clip_length_beats: float | None = None) -> dict[str, Any]:
    """Check one note. ``start`` and ``start_time`` are both beats, not bars."""
    if not isinstance(note, dict):
        raise NoteError("note はオブジェクトです")
    pitch = _integer(note.get("pitch"), "pitch", 0, 127)
    velocity = _integer(note.get("velocity"), "velocity", 1, 127)
    start = _start_beat(note)
    duration = _number(note.get("duration"), "duration")
    if duration <= 0:
        raise NoteError("duration は 0 より大きいビートです")
    if start < 0:
        raise NoteError("start は 0 以上のビートです")
    if clip_length_beats is not None and start >= clip_length_beats:
        raise NoteError("start がクリップの長さを超えています")
    probability = note.get("probability", 1.0)
    if isinstance(probability, bool) or not isinstance(probability, (int, float)):
        raise NoteError("probability は数値です")
    if not 0.0 <= float(probability) <= 1.0:
        raise NoteError("probability は 0 から 1 です")
    return {
        "pitch": pitch,
        "start": start,
        "duration": duration,
        "velocity": velocity,
        "probability": float(probability),
    }


def live_note(note: dict[str, Any]) -> dict[str, Any]:
    """Rename ``start`` to the Remote Script field ``start_time``. Still beats."""
    checked = validate_note(note)
    return {
        "pitch": checked["pitch"],
        "start_time": checked["start"],
        "duration": checked["duration"],
        "velocity": checked["velocity"],
    }


def _start_beat(note: dict[str, Any]) -> float:
    has_start = "start" in note and note["start"] is not None
    has_remote = "start_time" in note and note["start_time"] is not None
    if has_start and has_remote:
        start = _number(note["start"], "start")
        remote = _number(note["start_time"], "start_time")
        if abs(start - remote) > 1e-6:
            raise NoteError("start と start_time が違います")
        return start
    if has_start:
        return _number(note["start"], "start")
    if has_remote:
        return _number(note["start_time"], "start_time")
    raise NoteError("start はビートで必要です")


def _integer(value: object, label: str, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise NoteError(f"{label} は整数です")
    if value < low or value > high:
        raise NoteError(f"{label} は {low} から {high} です")
    return value


def _number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise NoteError(f"{label} は数値です")
    return float(value)
