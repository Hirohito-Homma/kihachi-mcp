"""Turn a Jarvis change request into the existing revision proposal. Live is not called."""

from __future__ import annotations

from itertools import pairwise
from typing import Any

_PARTS = {
    "hats": "Hats",
    "openhat": "OpenHat",
    "kick": "Kick",
    "snare": "Snare",
    "perc": "Perc",
    "bass": "Bass",
    "slap": "Bass",
    "sub": "Sub",
    "chords": "Stab",
    "stab": "Stab",
    "pad": "Pad",
    "arp": "Arp",
    "lead": "Lead",
    "guitar": "Guitar",
    "horn": "Horn",
    "vocal": "Vocal",
}
_SCOPES = {"reduce_density": "thin", "delete": "clear", "increase_density": "thicken"}
#: Adding notes is only defined for hats: a softer hit between two existing ones.
_THICKEN_PARTS = frozenset({"Hats"})


def revision_request(intent: dict[str, Any], song: dict[str, Any] | None = None) -> dict[str, Any]:
    """Map one change intent to revision scopes, bars and parts, or say why not.

    ``song`` holds the open candidate's ``bars`` and ``sections``; words such as
    後半 become bar ranges only from those.
    """
    if intent.get("type") != "modify_arrangement" or intent.get("write_requested") is not True:
        return {"ok": False, "error": "変更の依頼ではないので、修正案にはしません。Live は変えていません。"}
    scope = _SCOPES.get(str(intent.get("direction")))
    part = _PARTS.get(str(intent.get("target")))
    if scope is None or part is None:
        return {"ok": False, "error": "どのパートをどう変えるか分かりません。Live は変えていません。"}
    if scope == "thicken" and part not in _THICKEN_PARTS:
        return {"ok": False, "error": "増やす修正はハットだけです。Live は変えていません。"}
    bars = bars_for(intent, song or {})
    if bars is None:
        return {
            "ok": False,
            "error": "小節の範囲を 33-48 のように指定してください。Live は変えていません。",
        }
    return {"ok": True, "scopes": [scope], "parts": [part], "bars": bars}


def bars_for(intent: dict[str, Any], song: dict[str, Any]) -> tuple[int, int] | None:
    """The bar range a request names, or 前半/後半/リエントリー read from the song."""
    span = intent.get("range")
    if isinstance(span, dict):
        return int(span["start"]), int(span["end"])
    raw_total = song.get("bars")
    total = raw_total if isinstance(raw_total, int) else 0
    scope = intent.get("scope")
    if scope == "later_section" and total >= 2:
        return total // 2 + 1, total
    if scope == "earlier_section" and total >= 2:
        return 1, total // 2
    if scope == "reentry":
        listed = song.get("sections")
        sections = [item for item in listed if isinstance(item, dict)] if isinstance(listed, list) else []
        for previous, current in pairwise(sections):
            if str(previous.get("name")) == "Break" and current.get("start_bar") and current.get("end_bar"):
                return int(current["start_bar"]), int(current["end_bar"])
    return None
