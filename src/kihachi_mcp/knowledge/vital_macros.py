"""ジャンル・パート・ムード・トーン・要素から Vital の Macro 1〜4 を決定論的に推定する。

Macro 1〜4 統一規約（2026-11-25 確定仕様）:
- Macro 1: Brightness / Cutoff（明るさ・フィルター開閉）
- Macro 2: Length / Decay（音の長さ・減衰）
- Macro 3: Thickness / Mod（厚み・デチューン・揺らぎ・空間感）
- Macro 4: Grit / Attack（歪み・アタックの強さ・倍音）
"""

from __future__ import annotations

import math
from typing import Any

VITAL_MACRO_NAMES: tuple[str, ...] = ("Macro 1", "Macro 2", "Macro 3", "Macro 4")

VITAL_MACRO_ROLES: dict[str, str] = {
    "Macro 1": "Brightness / Cutoff",
    "Macro 2": "Length / Decay",
    "Macro 3": "Thickness / Mod",
    "Macro 4": "Grit / Attack",
}

#: パート別の基本 Macro 値 (Macro 1, Macro 2, Macro 3, Macro 4)
_PART_BASE: dict[str, tuple[float, float, float, float]] = {
    "default": (0.50, 0.50, 0.45, 0.35),
    "bass": (0.42, 0.38, 0.35, 0.45),
    "sub": (0.20, 0.55, 0.20, 0.25),
    "lead": (0.68, 0.52, 0.50, 0.45),
    "pad": (0.45, 0.82, 0.72, 0.18),
    "stab": (0.62, 0.28, 0.52, 0.40),
    "arp": (0.64, 0.26, 0.40, 0.38),
    "guitar": (0.66, 0.34, 0.38, 0.50),
    "horn": (0.60, 0.48, 0.55, 0.48),
    "vocal": (0.58, 0.62, 0.60, 0.28),
}

_PART_ALIASES: dict[str, str] = {
    "chords": "stab",
    "melody": "lead",
    "synth": "lead",
    "keys": "stab",
    "pluck": "arp",
    "bassmut": "bass",
}

#: ジャンル別の Macro オフセット
_GENRE_OFFSETS: dict[str, tuple[float, float, float, float]] = {
    "mutation_funk": (0.14, -0.10, 0.04, 0.18),
    "funk": (0.12, -0.08, 0.02, 0.14),
    "dub_techno": (-0.18, 0.14, 0.20, -0.08),
    "techno": (-0.06, -0.06, 0.08, 0.16),
    "house": (0.06, 0.02, 0.08, 0.04),
    "ambient": (-0.12, 0.24, 0.22, -0.18),
    "synthwave": (0.10, 0.12, 0.18, 0.06),
    "dnb": (0.08, -0.12, 0.06, 0.22),
}

_GENRE_ALIASES: dict[str, str] = {
    "mutation funk": "mutation_funk",
    "mutation-funk": "mutation_funk",
    "ミューテーションファンク": "mutation_funk",
    "dub techno": "dub_techno",
    "dub-techno": "dub_techno",
    "ダブテクノ": "dub_techno",
    "drum and bass": "dnb",
    "drum_and_bass": "dnb",
}

#: ムード別の Macro オフセット（英語・日本語キーワード対応）
_MOOD_OFFSETS: dict[str, tuple[float, float, float, float]] = {
    "dark": (-0.16, 0.06, 0.08, 0.06),
    "bright": (0.18, -0.02, 0.04, -0.02),
    "aggressive": (0.12, -0.08, 0.06, 0.24),
    "warm": (-0.06, 0.10, 0.14, -0.10),
    "chill": (-0.10, 0.16, 0.12, -0.16),
    "energetic": (0.14, -0.06, 0.06, 0.14),
    "dreamy": (-0.04, 0.20, 0.22, -0.16),
    "minimal": (-0.10, -0.10, -0.12, -0.04),
}

_MOOD_ALIASES: dict[str, str] = {
    "暗い": "dark",
    "ダーク": "dark",
    "深い": "dark",
    "明るい": "bright",
    "ブライト": "bright",
    "攻撃的": "aggressive",
    "激しい": "aggressive",
    "ハード": "aggressive",
    "暖かい": "warm",
    "温かい": "warm",
    "ウォーム": "warm",
    "落ち着き": "chill",
    "静か": "chill",
    "チル": "chill",
    "エネルギッシュ": "energetic",
    "勢い": "energetic",
    "幻想的": "dreamy",
    "浮遊感": "dreamy",
    "ミニマル": "minimal",
}

#: tone 軸（-2..+2）の 1 ステップあたりオフセット
_TONE_STEP_MAP: dict[str, tuple[int, float]] = {
    "brightness": (0, 0.10),
    "length": (1, 0.10),
    "thickness": (2, 0.10),
    "delay": (2, 0.08),
    "grit": (3, 0.10),
}

#: elements キー/値に対する補正
_ELEMENT_FLAG_OFFSETS: dict[str, tuple[float, float, float, float]] = {
    "slap": (0.14, -0.12, -0.04, 0.20),
    "cutting_16th": (0.14, -0.16, -0.06, 0.14),
    "chop_pattern": (0.10, -0.14, -0.04, 0.12),
    "sustain_stabs": (0.04, 0.18, 0.08, -0.04),
    "ghost_notes": (0.02, -0.06, -0.04, 0.06),
    "octave_pops": (0.10, -0.06, 0.00, 0.10),
    "slide_down": (0.00, 0.06, 0.04, 0.04),
    "sparse": (-0.04, -0.08, 0.06, -0.04),
    "big_beats": (-0.04, 0.04, 0.08, 0.08),
    "contrast": (0.06, 0.04, 0.04, 0.04),
}


def available_vital_genres() -> tuple[str, ...]:
    """既知のジャンルキー一覧を返す。"""
    return tuple(_GENRE_OFFSETS.keys())


def available_vital_parts() -> tuple[str, ...]:
    """既知のパートキー一覧を返す。"""
    return tuple(k for k in _PART_BASE if k != "default")


def available_vital_moods() -> tuple[str, ...]:
    """既知のムードキー一覧を返す。"""
    return tuple(_MOOD_OFFSETS.keys())


def estimate_vital_macros(
    *,
    genre: str = "",
    part: str = "",
    mood: str = "",
    tone: dict[str, Any] | None = None,
    elements: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """5入力軸（genre, part, mood, tone, elements）から Macro 1〜4 を推定する。"""
    reasons: list[str] = []

    norm_part = _normalize_part(part)
    base = list(_PART_BASE.get(norm_part, _PART_BASE["default"]))
    if norm_part in _PART_BASE and norm_part != "default":
        reasons.append(f"part={norm_part} 基準")
    else:
        reasons.append("part=default 基準")

    norm_genre = _normalize_genre(genre)
    if norm_genre in _GENRE_OFFSETS:
        offset = _GENRE_OFFSETS[norm_genre]
        _apply_offset(base, offset)
        reasons.append(f"genre={norm_genre} ({_format_offset(offset)})")
    elif str(genre or "").strip():
        reasons.append(f"genre={str(genre).strip()}（未定義のため基準値維持）")

    matched_moods = _extract_moods(mood)
    for mood_key in matched_moods:
        offset = _MOOD_OFFSETS[mood_key]
        _apply_offset(base, offset)
        reasons.append(f"mood={mood_key} ({_format_offset(offset)})")
    if not matched_moods and str(mood or "").strip():
        reasons.append(f"mood={str(mood).strip()}（未定義のため補正なし）")

    normalized_tone: dict[str, float] = {}
    if isinstance(tone, dict):
        for raw_key, raw_step in tone.items():
            key = str(raw_key).strip().lower()
            if key not in _TONE_STEP_MAP:
                continue
            step = _finite_float(raw_step)
            if step is None:
                continue
            clamped_step = max(-2.0, min(2.0, step))
            normalized_tone[key] = clamped_step
            if abs(clamped_step) < 1e-9:
                continue
            idx, per_step = _TONE_STEP_MAP[key]
            delta = round(clamped_step * per_step, 4)
            base[idx] += delta
            reasons.append(
                f"tone.{key}={clamped_step:+g} ({VITAL_MACRO_NAMES[idx]} {delta:+.2f})"
            )

    normalized_elements = _flatten_elements(elements, norm_part)
    for token in _extract_element_tokens(normalized_elements):
        offset = _ELEMENT_FLAG_OFFSETS.get(token)
        if offset is None:
            continue
        _apply_offset(base, offset)
        reasons.append(f"elements.{token} ({_format_offset(offset)})")

    values = {
        name: round(max(0.0, min(1.0, base[idx])), 3)
        for idx, name in enumerate(VITAL_MACRO_NAMES)
    }
    macros = {
        name: {
            "name": name,
            "role": VITAL_MACRO_ROLES[name],
            "value": values[name],
        }
        for name in VITAL_MACRO_NAMES
    }
    return {
        "macros": macros,
        "values": values,
        "inputs": {
            "genre": norm_genre or str(genre or "").strip(),
            "part": norm_part,
            "mood": ", ".join(matched_moods) if matched_moods else str(mood or "").strip(),
            "tone": normalized_tone,
            "elements": normalized_elements,
        },
        "reasons": reasons,
    }


def _normalize_part(part: str) -> str:
    key = str(part or "").strip().lower()
    if not key:
        return "default"
    if key in _PART_BASE:
        return key
    return _PART_ALIASES.get(key, "default")


def _normalize_genre(genre: str) -> str:
    raw = str(genre or "").strip().lower()
    if not raw:
        return ""
    if raw in _GENRE_OFFSETS:
        return raw
    if raw in _GENRE_ALIASES:
        return _GENRE_ALIASES[raw]
    underscored = raw.replace(" ", "_").replace("-", "_")
    if underscored in _GENRE_OFFSETS:
        return underscored
    return raw


def _extract_moods(mood: str) -> list[str]:
    text = str(mood or "").strip().lower()
    if not text:
        return []
    found: list[str] = []
    for alias, canonical in _MOOD_ALIASES.items():
        if alias in text and canonical not in found:
            found.append(canonical)
    for canonical in _MOOD_OFFSETS:
        if canonical in text and canonical not in found:
            found.append(canonical)
    return found


def _flatten_elements(
    elements: dict[str, Any] | None, norm_part: str
) -> dict[str, Any]:
    """パート別ネスト辞書またはフラット辞書から対象パートの要素辞書を抽出する。"""
    if not isinstance(elements, dict) or not elements:
        return {}
    # 例: {"bass": {"slap": True}} のようにパート名でネストされている場合
    part_candidates = [norm_part]
    for alias, target in _PART_ALIASES.items():
        if target == norm_part:
            part_candidates.append(alias)
    lowered = {str(k).strip().lower(): v for k, v in elements.items() if str(k).strip()}
    for candidate in part_candidates:
        if candidate in lowered and isinstance(lowered[candidate], dict):
            return dict(lowered[candidate])
    # ネスト辞書だが対象パート名がない場合はフラット扱いせず空またはスカラー要素のみ採用
    flat: dict[str, Any] = {}
    for key, val in lowered.items():
        if not isinstance(val, dict):
            flat[key] = val
    return flat


def _extract_element_tokens(flat_elements: dict[str, Any]) -> list[str]:
    tokens: list[str] = []
    for key, val in flat_elements.items():
        if val is True and key in _ELEMENT_FLAG_OFFSETS and key not in tokens:
            tokens.append(key)
        elif isinstance(val, str):
            val_key = val.strip().lower()
            if val_key in _ELEMENT_FLAG_OFFSETS and val_key not in tokens:
                tokens.append(val_key)
    return tokens


def _apply_offset(target: list[float], offset: tuple[float, float, float, float]) -> None:
    for idx in range(4):
        target[idx] += offset[idx]


def _format_offset(offset: tuple[float, float, float, float]) -> str:
    parts = [
        f"{VITAL_MACRO_NAMES[idx]} {delta:+.2f}"
        for idx, delta in enumerate(offset)
        if abs(delta) >= 1e-9
    ]
    return ", ".join(parts) if parts else "変更なし"


def _finite_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None
