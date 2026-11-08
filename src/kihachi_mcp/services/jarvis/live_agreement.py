"""Compare two Live reads. Disagreement stays UNKNOWN and writes nothing."""

from __future__ import annotations

from typing import Any

_KEYS = ("tempo", "track_count")


def agree_live_reads(left: dict[str, Any] | None, right: dict[str, Any] | None) -> dict[str, Any]:
    """Report whether tempo and track count match. Missing values are not success."""
    if not isinstance(left, dict) or not isinstance(right, dict):
        return _unknown("片方の Live 読み取りが無いので UNKNOWN です。成功にはしていません。")
    for key in _KEYS:
        if left.get(key) in (None, "", "UNKNOWN") or right.get(key) in (None, "", "UNKNOWN"):
            return _unknown(f"{key} が UNKNOWN です。成功にはしていません。")
        if left.get(key) != right.get(key):
            return _unknown(f"{key} が一致しません。成功にはしていません。")
    return {
        "ok": True,
        "agreed": True,
        "state": "COMPLETED",
        "reply": "二つの読み取りはテンポとトラック数が一致しました。音の完成ではありません。",
        "executed": False,
        "live_changed": False,
        "musical_quality_claimed": False,
    }


def _unknown(message: str) -> dict[str, Any]:
    return {
        "ok": False,
        "agreed": False,
        "state": "UNKNOWN",
        "reply": message,
        "executed": False,
        "live_changed": False,
        "musical_quality_claimed": False,
    }
