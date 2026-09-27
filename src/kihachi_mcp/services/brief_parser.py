"""Deterministic extraction of explicit values from a Japanese brief."""

import re
from typing import Any

from kihachi_mcp.models.production_brief import KEY_ENUM, SOURCE_USER

_KEY_ALIASES = {
    "シーシャープ": "C#",
    "ディーシャープ": "D#",
    "エフシャープ": "F#",
    "ジーシャープ": "G#",
    "エーシャープ": "A#",
}
_ROOTS = (
    ("C#", "C#"),
    ("D#", "D#"),
    ("F#", "F#"),
    ("G#", "G#"),
    ("A#", "A#"),
    ("Db", "C#"),
    ("Eb", "D#"),
    ("Gb", "F#"),
    ("Ab", "G#"),
    ("Bb", "A#"),
    ("C", "C"),
    ("D", "D"),
    ("E", "E"),
    ("F", "F"),
    ("G", "G"),
    ("A", "A"),
    ("B", "B"),
)

_UNHANDLED_PATTERNS = (
    (r"ボーカル|歌声|歌って|歌詞", "歌声・ボーカル生成は今回の対象外です"),
    (r"マスタリング|マスター", "自動マスタリングは今回の対象外です"),
    (r"完成音声|フル曲の書き出し|mp3|wav", "完成音声の丸ごと生成は今回の対象外です"),
    (r"学習|ファインチューン|追加学習", "モデルの追加学習は行いません"),
    (r"serum|massive|vital|serum", "指定シンセの自動読込は未対応です"),
    (r"サイドチェイン|sidechain", "サイドチェイン設定は未対応です"),
    (r"具体的なプリセット|808キット", "特定プリセットの自動読込は未対応です"),
)


def extract_explicit(brief: str) -> dict[str, Any]:
    """Return user-specified fields that the model is not allowed to override."""
    text = brief.strip()
    if not text or len(text) > 4000:
        raise ValueError("制作指示は1〜4000文字で入力してください")
    fields: dict[str, Any] = {}
    tempo = _extract_tempo(text)
    if tempo is not None:
        fields["tempo"] = tempo
    key = _extract_key(text)
    if key is not None:
        fields["key"] = key
    bars = _extract_bars(text)
    if bars is not None:
        fields["bars"] = bars
    drop = _extract_drop(text)
    if drop is not None:
        fields["drop_start_bar"] = drop
    hats = _extract_hat_density(text)
    fields.update(hats)
    mood = _extract_mood(text)
    if mood is not None:
        fields["mood"] = mood
    return {
        "original_text": text,
        "fields": fields,
        "unhandled": _extract_unhandled(text),
        "sources": {name: SOURCE_USER for name in fields},
    }


def _extract_tempo(text: str) -> int | None:
    match = re.search(r"(?<!\d)(\d{2,3})\s*(?:BPM|bpm|ＢＰＭ)", text)
    if not match:
        match = re.search(r"テンポ\s*(\d{2,3})", text)
    if not match:
        return None
    tempo = int(match.group(1))
    if 60 <= tempo <= 180:
        return tempo
    return None


def _extract_bars(text: str) -> int | None:
    match = re.search(r"(?<!\d)(\d{2,3})\s*小節", text)
    if not match:
        return None
    bars = int(match.group(1))
    if 16 <= bars <= 256 and bars % 4 == 0:
        return bars
    return None


def _extract_drop(text: str) -> int | None:
    match = re.search(r"(?<!\d)(\d{1,3})\s*小節目?から\s*(?:ドロップ|Drop|DROP|サビ)", text)
    if not match:
        match = re.search(r"(?:ドロップ|Drop|DROP)\s*(?:は|を|に)?\s*(\d{1,3})\s*小節", text)
    if not match:
        return None
    bar = int(match.group(1))
    return bar if bar >= 1 else None


def _extract_key(text: str) -> str | None:
    lowered = text
    for alias, root in _KEY_ALIASES.items():
        lowered = lowered.replace(alias, root)
    for raw, root in _ROOTS:
        pattern = rf"{re.escape(raw)}\s*(マイナー|minor|m\b|メジャー|major)?"
        match = re.search(pattern, lowered, flags=re.IGNORECASE)
        if not match:
            continue
        quality = (match.group(1) or "").lower()
        key = f"{root}m" if quality in {"マイナー", "minor", "m"} else root
        if (
            raw[0] == root[0]
            and ("マイナー" in text or re.search(r"\bminor\b", text, flags=re.IGNORECASE))
        ):
            key = f"{root}m"
        if key in KEY_ENUM:
            return key
    if re.search(r"\bDm\b", text):
        return "Dm"
    return None


def _extract_hat_density(text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    if re.search(r"前半.{0,12}(ハット|hats?).{0,8}(少なく|疎|減ら)", text, flags=re.IGNORECASE):
        result["hats_first_half"] = "sparse"
    elif re.search(r"前半.{0,12}(ハット|hats?).{0,8}(多く|密|増や)", text, flags=re.IGNORECASE):
        result["hats_first_half"] = "dense"
    if re.search(r"後半.{0,12}(ハット|hats?).{0,8}(多く|密|増や)", text, flags=re.IGNORECASE):
        result["hats_second_half"] = "dense"
    elif re.search(r"後半.{0,12}(ハット|hats?).{0,8}(少なく|疎|減ら)", text, flags=re.IGNORECASE):
        result["hats_second_half"] = "sparse"
    if "hats_first_half" not in result and re.search(
        r"(ハット|hats?).{0,8}前半.{0,8}(少なく|疎|減ら)", text, flags=re.IGNORECASE
    ):
        result["hats_first_half"] = "sparse"
    if "hats_second_half" not in result and re.search(
        r"(ハット|hats?).{0,8}後半.{0,8}(多く|密|増や)", text, flags=re.IGNORECASE
    ):
        result["hats_second_half"] = "dense"
    return result


def _extract_mood(text: str) -> str | None:
    for word in ("暗い", "ダーク", "明るい", "優しい", "激しい", "冷たい"):
        if word in text:
            return word
    return None


def _extract_unhandled(text: str) -> list[str]:
    found: list[str] = []
    for pattern, message in _UNHANDLED_PATTERNS:
        if re.search(pattern, text, flags=re.IGNORECASE) and message not in found:
            found.append(message)
    return found
