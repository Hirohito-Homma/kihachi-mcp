"""Deterministic extraction of explicit values from a Japanese brief."""

import re
import unicodedata
from typing import Any

from kihachi_mcp.knowledge.genre_database import match_genres
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


#: A span of the brief a reader acted on: (start, end, label).
Span = tuple[int, int, str]

_TEMPO_RES = (
    re.compile(r"(?<!\d)(\d{2,3})\s*(?:BPM|bpm|ＢＰＭ|ビーピーエム)"),
    re.compile(r"(?:BPM|bpm|ＢＰＭ|ビーピーエム)\s*(\d{2,3})"),
    re.compile(r"テンポ\s*(\d{2,3})"),
)
# 「57小節目」 is a position, not a length.
_BARS_RE = re.compile(r"(?<!\d)(\d{2,3})\s*小節(?!目)")
_DROP_RES = (
    re.compile(r"(?<!\d)(\d{1,3})\s*小節目?から\s*(?:ドロップ|Drop|DROP|サビ)"),
    re.compile(r"(?:ドロップ|Drop|DROP)\s*(?:は|を|に)?\s*(\d{1,3})\s*小節"),
)
_FEWER = "(少なく|疎|減ら)"
_MORE = "(多く|密|増や)"
_HAT = "(ハット|hats?)"
# (field, value, pattern). The first match per field wins, in this order.
_HAT_RULES = (
    ("hats_first_half", "sparse", rf"前半.{{0,12}}{_HAT}.{{0,8}}{_FEWER}"),
    ("hats_first_half", "dense", rf"前半.{{0,12}}{_HAT}.{{0,8}}{_MORE}"),
    ("hats_second_half", "dense", rf"後半.{{0,12}}{_HAT}.{{0,8}}{_MORE}"),
    ("hats_second_half", "sparse", rf"後半.{{0,12}}{_HAT}.{{0,8}}{_FEWER}"),
    ("hats_first_half", "sparse", rf"{_HAT}.{{0,8}}前半.{{0,8}}{_FEWER}"),
    ("hats_second_half", "dense", rf"{_HAT}.{{0,8}}後半.{{0,8}}{_MORE}"),
)
_MOOD_WORDS = ("暗い", "ダーク", "明るい", "優しい", "激しい", "冷たい")
#: A delay or echo request. KIHACHI writes MIDI, so it answers with quieter
#: repeats of the chord stabs rather than an effect device.
_ECHO_RE = re.compile(r"ディレイ|delay|エコー|echo", re.IGNORECASE)
_BARS_EN_RE = re.compile(r"(?<!\d)(\d{2,3})\s*-?\s*bars?\b", re.IGNORECASE)
_DURATION_RE = re.compile(r"約?\s*(\d+(?:\.\d+)?)\s*分")
_SWING_RES = (
    re.compile(r"(?:スイング|swing)\s*(\d{2,3})\s*%?", re.IGNORECASE),
    re.compile(r"(\d{2,3})\s*%\s*(?:スイング|swing)", re.IGNORECASE),
)
_STRONGER = r"(とても|かなり|すごく|もっと|めちゃくちゃ|すごい)"
_DELAY = r"(ディレイ|エコー|delay|echo)"
#: (field, direction, pattern). A sound word, read as a step of a recipe's tone
#: control. 「暗い」 is not here: it is the mood, which picks minor and a low
#: register, and reading it twice would darken the song twice.
_TONE_RULES = (
    ("tone_brightness", 1, r"煌びやか|きらびやか|キラキラ|きらきら|ブライト|bright|派手|抜けの?いい"),
    ("tone_brightness", -1, r"こもった|こもらせ|くぐもった|曇った|丸い音|ローファイ|lo-?fi|dull"),
    ("tone_length", 1, r"長め|伸びる|伸ばし|余韻|サステイン|sustain"),
    ("tone_length", -1, r"短め|短く|タイト|歯切れ|スタッカート|staccato"),
    ("tone_delay", 1, rf"{_DELAY}.{{0,6}}(多め|多く|強め|深め|深く|たっぷり)"),
    ("tone_delay", -1, rf"{_DELAY}.{{0,6}}(少なめ|少なく|控えめ|弱め|薄め)"),
)


def extract_explicit(brief: str) -> dict[str, Any]:
    """Return user-specified fields that the model is not allowed to override."""
    text = brief.strip()
    if not text or len(text) > 4000:
        raise ValueError("制作指示は1〜4000文字で入力してください")
    fields, _spans = read_explicit(text)
    return {
        "original_text": text,
        "fields": fields,
        "unhandled": _extract_unhandled(text),
        "sources": {name: SOURCE_USER for name in fields},
    }


def read_explicit(text: str) -> tuple[dict[str, Any], list[Span]]:
    """Return the explicit fields and exactly which text produced each one.

    Brief coverage reads the spans, so what it reports as read is what these
    readers used -- not a second copy of the patterns that could drift.
    """
    fields: dict[str, Any] = {}
    spans: list[Span] = []

    def first(name: str, found: tuple[Any, int, int] | None) -> None:
        if found is not None:
            value, start, end = found
            fields[name] = value
            spans.append((start, end, name))

    first("tempo", _find_tempo(text))
    first("key", _find_key(text))
    first("bars", _find_bars(text))
    if "bars" not in fields and "tempo" in fields:
        first("bars", _bars_from_duration(text, int(fields["tempo"])))
    first("drop_start_bar", _find_drop(text))
    first("swing", _find_swing(text))
    for name, value, pattern in _HAT_RULES:
        if name in fields:
            continue
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            fields[name] = value
            spans.append((match.start(), match.end(), name))
    for word in _MOOD_WORDS:
        position = text.find(word)
        if position >= 0:
            fields["mood"] = word
            spans.append((position, position + len(word), "mood"))
            break
    for name, direction, pattern in _TONE_RULES:
        if name in fields:
            # Another statement asking the same way was read too; one asking
            # the opposite way was not, and stays unread in the coverage.
            if (fields[name] > 0) == (direction > 0):
                spans.extend(
                    (match.start(), match.end(), name)
                    for match in re.finditer(pattern, text, flags=re.IGNORECASE)
                )
            continue
        matches = list(re.finditer(pattern, text, flags=re.IGNORECASE))
        if matches:
            first = matches[0]
            before = text[max(0, first.start() - 6):first.start()]
            steps = 2 if re.search(_STRONGER, before) else 1
            fields[name] = direction * steps
            spans.extend((match.start(), match.end(), name) for match in matches)
    echo = _ECHO_RE.search(text)
    if echo:
        fields["echo"] = True
        spans.append((echo.start(), echo.end(), "echo"))
    genres = match_genres(text)
    if genres:
        # The first named genre leads; the others are reported, not blended.
        fields["genre"] = genres[0].genre.slug
        spans.extend((match.start, match.end, "genre") for match in genres)
    return fields, spans


def out_of_scope_spans(text: str) -> list[tuple[int, int, str]]:
    """Where the brief asks for something KIHACHI states it does not do."""
    found: list[tuple[int, int, str]] = []
    for pattern, message in _UNHANDLED_PATTERNS:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            found.append((match.start(), match.end(), message))
    return found


def _find_tempo(text: str) -> tuple[int, int, int] | None:
    for pattern in _TEMPO_RES:
        match = pattern.search(text)
        if match:
            tempo = int(match.group(1))
            return (tempo, match.start(), match.end()) if 60 <= tempo <= 180 else None
    return None


def explicit_swing(text: str) -> float | None:
    """Return a user-written swing ratio, such as 54% -> 0.54."""
    found = _find_swing(text)
    return None if found is None else float(found[0])


def _find_bars(text: str) -> tuple[int, int, int] | None:
    for match in _BARS_RE.finditer(text):
        bars = int(match.group(1))
        if 16 <= bars <= 256 and bars % 4 == 0:
            return bars, match.start(), match.end()
    match = _BARS_EN_RE.search(text)
    if match:
        bars = int(match.group(1))
        if 16 <= bars <= 256 and bars % 4 == 0:
            return bars, match.start(), match.end()
    return None


def _bars_from_duration(text: str, tempo: int) -> tuple[int, int, int] | None:
    """Turn 「約5分」 into bars when the brief did not name a bar count."""
    match = _DURATION_RE.search(text)
    if match is None or tempo <= 0:
        return None
    minutes = float(match.group(1))
    if not 0.5 <= minutes <= 20:
        return None
    bars = int(round((minutes * tempo / 4.0) / 4.0) * 4)
    bars = min(256, max(16, bars))
    return bars, match.start(), match.end()


def _find_swing(text: str) -> tuple[float, int, int] | None:
    for pattern in _SWING_RES:
        match = pattern.search(text)
        if match is None:
            continue
        percent = int(match.group(1))
        if 50 <= percent <= 75:
            return percent / 100.0, match.start(), match.end()
    return None


def _find_drop(text: str) -> tuple[int, int, int] | None:
    for pattern in _DROP_RES:
        match = pattern.search(text)
        if match:
            bar = int(match.group(1))
            return (bar, match.start(), match.end()) if bar >= 1 else None
    return None


def _extract_key(text: str) -> str | None:
    found = _find_key(text)
    return found[0] if found else None


def _find_key(text: str) -> tuple[str, int, int] | None:
    # Full-width 「Ｄ＃」 is what a Japanese IME types; ♯/♭ are the music signs.
    # Normalizing one character at a time keeps an index back into the brief.
    normalized: list[str] = []
    origin: list[int] = []
    for index, character in enumerate(text):
        piece = unicodedata.normalize("NFKC", character).replace("♯", "#").replace("♭", "b")
        normalized.append(piece)
        origin.extend([index] * len(piece))
    lowered = "".join(normalized)
    origin.append(len(text))
    for alias, root in _KEY_ALIASES.items():
        position = lowered.find(alias)
        if position >= 0:
            quality = lowered[position + len(alias):position + len(alias) + 4]
            key = f"{root}m" if quality.startswith("マイナー") else root
            end = position + len(alias) + (4 if quality.startswith(("マイナー", "メジャー")) else 0)
            if key in KEY_ENUM:
                return key, origin[position], origin[end]
    for raw, root in _ROOTS:
        # A key letter stands alone: the C in "TECHNO" or the D in "DUB" is
        # part of a word, not a key.
        pattern = (
            rf"(?<![A-Za-z]){re.escape(raw)}"
            r"\s*(マイナー|minor|m(?![A-Za-z])|メジャー|major)?(?![A-Za-z#])"
        )
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
            return key, origin[match.start()], origin[match.end()]
    return None


def _extract_unhandled(text: str) -> list[str]:
    found: list[str] = []
    for pattern, message in _UNHANDLED_PATTERNS:
        if re.search(pattern, text, flags=re.IGNORECASE) and message not in found:
            found.append(message)
    return found
