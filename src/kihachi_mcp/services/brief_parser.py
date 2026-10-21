"""Deterministic extraction of explicit values from a Japanese brief."""

import re
import unicodedata
from dataclasses import dataclass
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
_BARS_RE = re.compile(r"(?<!\d)(\d{1,3})\s*小節(?!目)")
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
_BARS_EN_RE = re.compile(r"(?<!\d)(\d{1,3})\s*-?\s*bars?\b", re.IGNORECASE)
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
#: Brightness is read word by word in :func:`_brightness_mentions`, because a
#: brightness word names a part, a section or the whole song, and may be refused.
_BRIGHTER = r"ド派手|派手|煌びやか|きらびやか|キラキラ|きらきら|ブライト|bright|抜けの?いい|華やか"
_DULLER = r"こもった|こもらせ|くぐもった|曇った|丸い音|ローファイ|lo-?fi|dull"
#: Only a scoped request reads these; on their own they never were tone steps.
_SCOPED_ONLY_WORDS = frozenset({"華やか"})
_TONE_RULES = (
    ("tone_length", 1, r"長め|伸びる|伸ばし|余韻|サステイン|sustain"),
    ("tone_length", -1, r"短め|短く|タイト|歯切れ|スタッカート|staccato"),
    ("tone_delay", 1, rf"{_DELAY}.{{0,6}}(多め|多く|強め|深め|深く|たっぷり)"),
    ("tone_delay", -1, rf"{_DELAY}.{{0,6}}(少なめ|少なく|控えめ|弱め|薄め)"),
)
#: Any character but a sentence end: a request never borrows words from the
#: next sentence. 「、」 stays allowed, people pause inside one request.
_S = r"[^。\n！!？?]"
_GRADUAL = r"(?:徐々に|次第に|だんだん|段々|少しずつ|gradually)"
# 「Drum & Bass」 and 「ドラムンベース」 name a genre, not the bass part.
_BASS_PART = r"(?<!drum)(?<!drum )(?<![&＆'’])(?<![&＆] )(?<!ドラム)(?<!ドラムン)(?:ベース|bass)"
_PROGRESSIVE_BASS_RE = re.compile(
    rf"{_BASS_PART}{_S}{{0,24}}(?:最初|冒頭|序盤|intro){_S}{{0,16}}"
    rf"(?:動き{_S}{{0,4}}(?:少|控え)|少なめ|シンプル){_S}{{0,32}}"
    rf"{_GRADUAL}{_S}{{0,16}}"
    r"(?:動き|細か|活発|多く|busier|active)",
    re.IGNORECASE,
)
_BASS_MORE_RE = re.compile(
    rf"{_BASS_PART}(?:は|も|を|の)?{_S}{{0,8}}"
    r"(?:動き(?:を|は)?\s*(?:増や[すし]|多く|活発に|細かく)|細かく|活発に|音数(?:を|は)?\s*(?:増や[すし]|多く))",
    re.IGNORECASE,
)
#: Fields a scoped request sets; a brightness word inside one is not a tone step.
_SCOPED_RULES = (
    ("bass_activity_curve", "rising", _PROGRESSIVE_BASS_RE),
    ("bass_activity", "dense", _BASS_MORE_RE),
)
_CLAUSE_END_RE = re.compile(r"[。、\n;；！!？?]")
_SENTENCE_END_RE = re.compile(r"[。\n;；！!？?]")
# 「派手にしない」「増やさない」「派手すぎない」「派手じゃない」「ではなく」. A bare
# 「ない」 is not here: 「動きの少ない」 asks for little movement, it does not refuse.
_REFUSAL_RE = re.compile(
    r"[かさしせなえらやぎく]ない|せず|すぎず|過ぎず|じゃない|ではない|でない"
    r"|ではなく|じゃなく|でなく"
    r"|(?:入れ|いれ|作|つく|設|もうけ)(?:ない|ず)"
)
_MID_BREAK_RE = re.compile(
    r"(?:中間|曲の中間|中盤)(?:[にではをも]|地点で)?(?:必ず)?"
    r"(?:ブレイク|Break|Brake|break|brake)"
    r"(?:[をはにも]|って)?(?:必ず)?"
    r"(?P<verb>入れ|いれ|追加|配置|挟[むん]|設[けけ]|作[るっ])(?P<aux>て|た)?",
    re.IGNORECASE,
)
#: What a brightness word can be about. 「上物」 is the parts above the rhythm
#: section; any other named part is one KIHACHI cannot brighten on its own.
_TARGETS = (
    ("upper", r"上物|上モノ|うわもの|ウワモノ"),
    ("drop", r"ドロップ|\bdrop\b"),
    ("whole", r"曲全体|全体"),
    (
        "part",
        (
            rf"{_BASS_PART}|キック|\bkick\b|ハット|\bhats?\b|スネア|クラップ|サブ|パッド|\bpad\b"
            r"|コード|リード|\blead\b|アルペジオ|\barp\b|シンセ|\bsynth\b"
        ),
    ),
)
_TARGET_RE = re.compile("|".join(f"(?P<{name}>{pattern})" for name, pattern in _TARGETS), re.IGNORECASE)
_BRIGHTNESS_RE = re.compile(f"(?P<up>{_BRIGHTER})|(?P<down>{_DULLER})", re.IGNORECASE)
# 「キラキラした上物」「派手じゃないドロップ」「派手めのドロップ」: the word describes
# the target right after it, and may be refused on the way.
_ATTACHED_RE = re.compile(
    r"(?:な|の|した|とした|めの|め)?(?P<refused>じゃない|ではない|でない|じゃなく|ではなく)?(?:の)?"
)


@dataclass(frozen=True)
class BrightnessMention:
    """One brightness word and what it is about."""

    #: The word and its target together, for the coverage.
    start: int
    end: int
    word_start: int
    word: str
    #: +1 brighter, -1 duller.
    direction: int
    #: "upper", "drop", "whole", "part", or None when the clause names nothing.
    target: str | None
    target_word: str
    refused: bool
    gradual: bool


def brightness_mentions(text: str) -> list[BrightnessMention]:
    """Every brightness word with its target, refusal and 「徐々に」, in text order.

    One reader for the parser, the coverage and the model's fallback, so a
    refused 「派手じゃないドロップ」 cannot come back as a whole-song step.
    """
    bass_spans = [match.span() for match in _PROGRESSIVE_BASS_RE.finditer(text)]
    mentions: list[BrightnessMention] = []
    for match in _BRIGHTNESS_RE.finditer(text):
        if any(a < match.end() and match.start() < b for a, b in bass_spans):
            continue
        mentions.append(_mention(text, match))
    return mentions


def _mention(text: str, match: re.Match[str]) -> BrightnessMention:
    start, end = match.span()
    sentence_start = max(
        (found.end() for found in _SENTENCE_END_RE.finditer(text, 0, start)), default=0
    )
    attached = _ATTACHED_RE.match(text, end)
    target = _TARGET_RE.match(text, attached.end())
    refusal_after: re.Match[str] | None = None
    if target is not None:
        if attached.group("refused"):
            refused = True
        else:
            refusal_after = _refusal_match(text, target.end())
            refused = refusal_after is not None
        lead_from = max(sentence_start, start - 8)
    else:
        target = _target_before(text, sentence_start, start)
        refusal_after = _refusal_match(text, end)
        refused = refusal_after is not None
        lead_from = target.end() if target else max(sentence_start, start - 8)
    span_start = min(start, target.start()) if target else start
    span_end = max(end, target.end()) if target else end
    if refusal_after is not None:
        span_end = max(span_end, refusal_after.end())
    lead_in = text[lead_from:start]
    return BrightnessMention(
        start=span_start,
        end=span_end,
        word_start=start,
        word=match.group(0),
        direction=1 if match.group("up") else -1,
        target=target.lastgroup if target else None,
        target_word=target.group(0) if target else "",
        refused=refused,
        gradual=bool(re.search(_GRADUAL, lead_in, flags=re.IGNORECASE)),
    )


def _target_before(text: str, sentence_start: int, start: int) -> re.Match[str] | None:
    """The last target named in the word's clause, or a bare 「上物は、」 just before it."""
    clause_start = max(
        (found.end() for found in _CLAUSE_END_RE.finditer(text, sentence_start, start)),
        default=sentence_start,
    )
    found = list(_TARGET_RE.finditer(text, clause_start, start))
    if found:
        return found[-1]
    if clause_start > sentence_start:
        previous_start = max(
            (
                item.end()
                for item in _CLAUSE_END_RE.finditer(text, sentence_start, clause_start - 1)
            ),
            default=sentence_start,
        )
        topic = re.compile(rf"\s*(?:{_TARGET_RE.pattern})(?:は|も|を|って)?\s*", re.IGNORECASE)
        bare = topic.fullmatch(text, previous_start, clause_start - 1)
        if bare:
            return _TARGET_RE.search(text, previous_start, clause_start - 1)
    return None


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
    _read_mid_break(text, fields, spans)
    for name, value, pattern in _SCOPED_RULES:
        for match in pattern.finditer(text):
            if name not in fields and not _refused(text, match.end()):
                fields[name] = value
                spans.append((match.start(), match.end(), name))
    _read_brightness(text, fields, spans)
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
        matches = list(re.finditer(pattern, text, flags=re.IGNORECASE))
        if name in fields:
            # Another statement asking the same way was read too; one asking
            # the opposite way was not, and stays unread in the coverage.
            if (fields[name] > 0) == (direction > 0):
                spans.extend((match.start(), match.end(), name) for match in matches)
            continue
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


#: Value of a scoped brightness field the brief turned down: nothing changes.
DECLINED = "declined"


def _read_brightness(text: str, fields: dict[str, Any], spans: list[Span]) -> None:
    """Whole-song steps, upper-part and Drop requests, and refusals of them.

    A refusal is read as "leave it", never as the opposite step: 「派手じゃない」
    does not ask for a duller sound. A word about another part is reported by
    :func:`unsupported_brightness_spans` and changes nothing here.
    """
    mentions = brightness_mentions(text)
    whole = [
        item
        for item in mentions
        if item.target in (None, "whole")
        and not (item.target is None and item.word in _SCOPED_ONLY_WORDS)
    ]
    for item in whole:
        if item.refused:
            continue
        if "tone_brightness" not in fields:
            before = text[max(0, item.word_start - 6):item.word_start]
            steps = 2 if re.search(_STRONGER, before) else 1
            fields["tone_brightness"] = item.direction * steps
        # A statement asking the opposite way stays unread in the coverage.
        if (fields["tone_brightness"] > 0) == (item.direction > 0):
            spans.append((item.start, item.end, "tone_brightness"))
    if "tone_brightness" not in fields:
        refused = [item for item in whole if item.refused]
        if refused:
            fields["tone_brightness"] = 0
            spans.extend((item.start, item.end, "tone_brightness") for item in refused)
    for item in mentions:
        if item.target not in ("upper", "drop") or item.direction < 0:
            continue
        if item.target == "upper":
            name = "upper_brightness"
            value = DECLINED if item.refused else "rising" if item.gradual else "steady"
        else:
            name = "drop_emphasis"
            value = DECLINED if item.refused else "flashy"
        if fields.setdefault(name, value) == value:
            spans.append((item.start, item.end, name))


def brightness_is_scoped_or_refused(text: str) -> bool:
    """Whether every brightness word names a part or the Drop, or is refused.

    Then no whole-song brightness was asked for, and a model's guess at one
    -- brighter from 「派手」, darker from 「派手じゃない」 -- is not used.
    """
    mentions = brightness_mentions(text)
    return bool(mentions) and all(
        item.refused or item.target not in (None, "whole") for item in mentions
    )


def _read_mid_break(text: str, fields: dict[str, Any], spans: list[Span]) -> None:
    """Read requests for a Break section around the middle of the song."""
    for match in _MID_BREAK_RE.finditer(text):
        verb_start = match.start("verb")
        refusal = _refusal_match(text, verb_start)
        refused = refusal is not None
        span_end = refusal.end() if refusal else match.end()
        value = DECLINED if refused else "required"
        if fields.setdefault("mid_break", value) == value:
            spans.append((match.start(), span_end, "mid_break"))


def unsupported_brightness_spans(text: str) -> list[tuple[int, int, str]]:
    """Brightness asked of one part KIHACHI cannot brighten alone, kept off the whole song."""
    found: list[tuple[int, int, str]] = []
    for item in brightness_mentions(text):
        if item.refused or item.target in (None, "whole"):
            continue
        if item.target in ("upper", "drop") and item.direction > 0:
            continue
        what = "こもらせる" if item.direction < 0 else "明るくする"
        found.append(
            (
                item.start,
                item.end,
                f"「{item.target_word}」だけを{what}音色指定は未対応です（曲全体には適用しません）",
            )
        )
    return found


def _refusal_match(text: str, end: int) -> re.Match[str] | None:
    """A refusal in the rest of the clause after ``end``, before the next target.

    The clause ends early at the next part or section it names: in
    「上物は煌びやかでベースは派手にしない」 the refusal is the bass's.
    """
    stop = _CLAUSE_END_RE.search(text, end)
    limit = stop.start() if stop else len(text)
    next_target = _TARGET_RE.search(text, end, limit)
    if next_target:
        limit = next_target.start()
    return _REFUSAL_RE.search(text, end, min(limit, end + 16))


def _refused(text: str, end: int) -> bool:
    """Whether the rest of the clause after a request turns it down."""
    return _refusal_match(text, end) is not None


def out_of_scope_spans(text: str) -> list[tuple[int, int, str]]:
    """Where the brief asks for something KIHACHI states it does not do."""
    found: list[tuple[int, int, str]] = []
    for pattern, message in _UNHANDLED_PATTERNS:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            found.append((match.start(), match.end(), message))
    return found + unsupported_brightness_spans(text)


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
        if 4 <= bars <= 256 and bars % 4 == 0:
            return bars, match.start(), match.end()
    match = _BARS_EN_RE.search(text)
    if match:
        bars = int(match.group(1))
        if 4 <= bars <= 256 and bars % 4 == 0:
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
    for _start, _end, message in unsupported_brightness_spans(text):
        if message not in found:
            found.append(message)
    return found
