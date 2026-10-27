"""Turn one Japanese utterance into a structured intent. No Live calls."""

from __future__ import annotations

import json
import re
import unicodedata
from typing import Any

_RANGE = re.compile(r"(\d+)\s*(?:から|[-–—〜~])\s*(\d+)")
_CLIP_MENTION = re.compile(r"(?:クリップ|スロット|\bclip\b|\bslot\b)", re.IGNORECASE)
_TRACK_INDEX = re.compile(r"(?:トラック|track)\s*#?\s*(-?\d+)|(-?\d+)\s*番目?の(?:MIDI\s*)?トラック", re.IGNORECASE)
_CLIP_INDEX = re.compile(
    r"(?:クリップ|スロット|clip|slot)\s*#?\s*(-?\d+)|(-?\d+)\s*番目?の(?:MIDI\s*)?(?:クリップ|スロット)",
    re.IGNORECASE,
)
_MULTI_TRACK = re.compile(
    r"(?:トラック|track)\s*#?\s*-?\d+\s*(?:から|[-–—〜~]|と|や|、|,|または|or)\s*(?:トラック|track)?\s*#?\s*-?\d+",
    re.IGNORECASE,
)
_MULTI_CLIP = re.compile(
    r"(?:クリップ|スロット|clip|slot)\s*#?\s*-?\d+\s*(?:から|[-–—〜~]|と|や|、|,|または|or)\s*(?:クリップ|スロット|clip|slot)?\s*#?\s*-?\d+",
    re.IGNORECASE,
)
_QUOTED_TRACK_NAME = re.compile(
    r"(?:トラック(?:名)?(?:\s*#?\s*-?\d+)?\s*[「『\"']([^」』\"']+)[」』\"']|[「『\"']([^」』\"']+)[」』\"']\s*トラック)"
)
_EXPLICIT_TRACK_NAME = re.compile(r"トラック名\s*[:：=]\s*([^\s、。の「『\"']+)")
_PREFIX_TRACK_NAME = re.compile(r"([A-Za-z0-9][A-Za-z0-9 _-]{0,40}?)\s*トラック")
_TARGETS = (
    ("スラップ", "slap"),
    ("サブ", "sub"),
    ("ベース", "bass"),
    ("キック", "kick"),
    ("オープンハット", "openhat"),
    ("ハット", "hats"),
    ("スネア", "snare"),
    ("パーカッション", "perc"),
    ("パーカス", "perc"),
    ("スタブ", "stab"),
    ("パッド", "pad"),
    ("アルペジオ", "arp"),
    ("アルペ", "arp"),
    ("リード", "lead"),
    ("ギター", "guitar"),
    ("ホーン", "horn"),
    ("ボーカル", "vocal"),
    ("コード", "chords"),
    ("slap", "slap"),
    ("sub", "sub"),
    ("bass", "bass"),
    ("snare", "snare"),
)


def parse_intent(utterance: str) -> dict[str, Any]:
    """Return a structured intent. Unrecognized text stays write_requested false."""
    text = unicodedata.normalize("NFKC", utterance or "").strip()
    if not text or len(text) > 500:
        return _unknown()
    if _CLIP_MENTION.search(text):
        return _parse_clip_intent(text)
    target = _target(text)
    scope = _scope(text)
    span = _range(text)
    write = _write_requested(text)
    if write and target:
        intent = {
            "type": "modify_arrangement",
            "target": target,
            "scope": scope,
            "range": span,
            "direction": _direction(text),
            "requested_action": "modify",
            "write_requested": True,
            "recognized": True,
        }
    elif _is_status(text) and target is None:
        intent = {
            "type": "inspect_project",
            "target": None,
            "scope": None,
            "range": None,
            "direction": None,
            "requested_action": "inspect",
            "write_requested": False,
            "recognized": True,
        }
    elif _is_musical_judgment(text):
        intent = {
            "type": "musical_judgment",
            "target": target,
            "scope": scope,
            "range": span,
            "direction": None,
            "requested_action": "evaluate",
            "write_requested": False,
            "recognized": True,
        }
    elif target and not write:
        intent = {
            "type": "evaluate_arrangement",
            "target": target,
            "scope": scope,
            "range": span,
            "direction": None,
            "requested_action": "evaluate",
            "write_requested": False,
            "recognized": True,
        }
    else:
        return _unknown()
    if span is not None and span["start"] >= span["end"]:
        return _unknown()
    validate_intent(intent)
    return intent


def intent_from_model_json(content: str) -> dict[str, Any]:
    """Accept one model JSON object. Broken JSON stays unrecognized and writes nothing."""
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        return _unknown()
    if not isinstance(payload, dict):
        return _unknown()
    kind = payload.get("type")
    if kind not in {
        "inspect_project",
        "evaluate_arrangement",
        "modify_arrangement",
        "evaluate_midi_clip",
        "modify_midi_clip",
        "musical_judgment",
        "unknown",
    }:
        return _unknown()
    span = payload.get("range")
    intent: dict[str, Any] = {
        "type": kind,
        "target": payload.get("target"),
        "scope": payload.get("scope"),
        "range": span if isinstance(span, dict) else None,
        "direction": payload.get("direction"),
        "requested_action": str(payload.get("requested_action") or "unknown"),
        "write_requested": payload.get("write_requested") is True,
        "recognized": kind != "unknown",
    }
    if kind in {"evaluate_midi_clip", "modify_midi_clip"}:
        clip_target = payload.get("clip_target")
        if not isinstance(clip_target, dict):
            return _unknown()
        intent["clip_target"] = clip_target
    try:
        validate_intent(intent)
    except (TypeError, ValueError):
        return _unknown()
    return intent


def validate_intent(intent: dict[str, Any]) -> None:
    """Reject a shape that would let a read look like a write, or the reverse."""
    kind = intent.get("type")
    write = intent.get("write_requested")
    if kind in {"modify_arrangement", "modify_midi_clip"} and write is not True:
        raise ValueError("a modification intent must request a write")
    if (
        kind
        in {
            "inspect_project",
            "evaluate_arrangement",
            "evaluate_midi_clip",
            "musical_judgment",
            "unknown",
        }
        and write is True
    ):
        raise ValueError("a read intent must not request a write")
    span = intent.get("range")
    if span is not None and span["start"] >= span["end"]:
        raise ValueError("range start must be before end")


def _parse_clip_intent(text: str) -> dict[str, Any]:
    clip_target = _extract_clip_target(text)
    if not clip_target["ok"]:
        unknown = _unknown()
        unknown["error"] = clip_target["error"]
        unknown["clip_target"] = clip_target
        return unknown
    scope = _scope(text)
    span = _range_outside_identifiers(text)
    if span is not None and span["start"] >= span["end"]:
        unknown = _unknown()
        unknown["error"] = "小節範囲の開始は終了より前にしてください。Liveは変更していません。"
        unknown["clip_target"] = clip_target
        return unknown
    write = _write_requested(text)
    target_label = (
        clip_target["track_name"]
        if clip_target["track_name"] is not None
        else f"track_{clip_target['track_index']}"
    )
    intent: dict[str, Any] = {
        "type": "modify_midi_clip" if write else "evaluate_midi_clip",
        "target": target_label,
        "scope": scope,
        "range": span,
        "direction": _direction(text) if write else None,
        "requested_action": "modify" if write else "evaluate",
        "write_requested": write,
        "recognized": True,
        "clip_target": {
            "track_index": clip_target["track_index"],
            "track_name": clip_target["track_name"],
            "clip_index": clip_target["clip_index"],
        },
    }
    validate_intent(intent)
    return intent


def _extract_clip_target(text: str) -> dict[str, Any]:
    if _MULTI_TRACK.search(text) or _MULTI_CLIP.search(text):
        return {
            "ok": False,
            "ambiguous": True,
            "track_index": None,
            "track_name": None,
            "clip_index": None,
            "error": "トラックまたはクリップの指定が複数あり曖昧です。1つのトラックと1つのクリップを指定してください。Liveは変更していません。",
        }
    track_indices = [
        int(m.group(1) if m.group(1) is not None else m.group(2))
        for m in _TRACK_INDEX.finditer(text)
    ]
    clip_indices = [
        int(m.group(1) if m.group(1) is not None else m.group(2))
        for m in _CLIP_INDEX.finditer(text)
    ]
    track_names = _extract_track_names(text)

    if len(track_indices) > 1 or len(track_names) > 1 or len(clip_indices) > 1:
        return {
            "ok": False,
            "ambiguous": True,
            "track_index": None,
            "track_name": None,
            "clip_index": None,
            "error": "トラックまたはクリップの指定が重複しています。1つのトラックと1つのクリップを指定してください。Liveは変更していません。",
        }
    if track_indices and track_names:
        return {
            "ok": False,
            "ambiguous": True,
            "track_index": None,
            "track_name": None,
            "clip_index": None,
            "error": "track_index と track_name は片方だけ指定してください。Liveは変更していません。",
        }
    if not track_indices and not track_names:
        return {
            "ok": False,
            "ambiguous": True,
            "track_index": None,
            "track_name": None,
            "clip_index": None,
            "error": "読み取るトラック（トラック番号またはトラック名）を指定してください。推測では選びません。Liveは変更していません。",
        }
    if not clip_indices:
        return {
            "ok": False,
            "ambiguous": True,
            "track_index": track_indices[0] if track_indices else None,
            "track_name": track_names[0] if track_names else None,
            "clip_index": None,
            "error": "読み取る Session クリップ番号（clip_index、0以上の整数）を指定してください。推測では選びません。Liveは変更していません。",
        }
    track_index = track_indices[0] if track_indices else None
    track_name = track_names[0] if track_names else None
    clip_index = clip_indices[0]
    if (track_index is not None and track_index < 0) or clip_index < 0:
        return {
            "ok": False,
            "ambiguous": False,
            "track_index": track_index,
            "track_name": track_name,
            "clip_index": clip_index,
            "error": "トラック番号とクリップ番号は 0 以上の整数で指定してください。Liveは変更していません。",
        }
    return {
        "ok": True,
        "ambiguous": False,
        "track_index": track_index,
        "track_name": track_name,
        "clip_index": clip_index,
        "error": None,
    }


def _extract_track_names(text: str) -> list[str]:
    names: list[str] = []
    for match in _QUOTED_TRACK_NAME.finditer(text):
        candidate = (match.group(1) or match.group(2) or "").strip()
        if candidate and candidate not in names:
            names.append(candidate)
    if names:
        return names
    for match in _EXPLICIT_TRACK_NAME.finditer(text):
        candidate = match.group(1).strip()
        if candidate and candidate not in names:
            names.append(candidate)
    if names:
        return names
    for match in _PREFIX_TRACK_NAME.finditer(text):
        candidate = match.group(1).strip()
        if not candidate or candidate.isdigit() or candidate.casefold() in {"midi", "audio", "session", "live"}:
            continue
        if candidate not in names:
            names.append(candidate)
    return names


def _range_outside_identifiers(text: str) -> dict[str, int] | None:
    cleaned = _TRACK_INDEX.sub("", _CLIP_INDEX.sub("", text))
    return _range(cleaned)


def _unknown() -> dict[str, Any]:
    return {
        "type": "unknown",
        "target": None,
        "scope": None,
        "range": None,
        "direction": None,
        "requested_action": "unknown",
        "write_requested": False,
        "recognized": False,
    }


def _target(text: str) -> str | None:
    folded = text.casefold()
    for label, name in _TARGETS:
        if label.casefold() in folded:
            return name
    return None


def _scope(text: str) -> str | None:
    folded = text.casefold()
    if "re-entry" in folded or "reentry" in folded or "リエントリー" in text:
        return "reentry"
    if any(word in text for word in ("後半", "後ろ", "終盤")):
        return "later_section"
    if any(word in text for word in ("前半", "序盤")):
        return "earlier_section"
    return None


def _range(text: str) -> dict[str, int] | None:
    match = _RANGE.search(text)
    if match is None:
        return None
    return {"start": int(match.group(1)), "end": int(match.group(2))}


def _direction(text: str) -> str:
    if any(word in text for word in ("消し", "削除", "クリア")):
        return "delete"
    if any(word in text for word in ("増や", "厚く")):
        return "increase_density"
    if any(word in text for word in ("ベロシティ", "強弱", "velocity")):
        return "velocity_contrast"
    return "reduce_density"


def _write_requested(text: str) -> bool:
    if re.search(r"[?？]", text) or text.endswith("ない"):
        return False
    return bool(re.search(r"(減らして|増やして|薄くして|厚くして|消して|削除|変えて|作って|調整して)", text))


def _is_musical_judgment(text: str) -> bool:
    folded = text.casefold()
    if "っぽく" in text or "っぽい" in text:
        return True
    return "もっと" in text and any(word in folded for word in ("dub", "ダーク", "暗", "軽", "重"))


def _is_status(text: str) -> bool:
    return any(
        phrase in text
        for phrase in ("どうなって", "どうですか", "状態", "今の曲", "今のプロジェクト")
    )
