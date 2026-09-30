"""Interpret a Japanese brief with the installed local model only."""

import json
import re
from dataclasses import replace
from http.client import HTTPConnection
from typing import Any

from kihachi_mcp.knowledge.genre_database import find as find_genre
from kihachi_mcp.knowledge.genre_database import match_genres, typical_bpm
from kihachi_mcp.knowledge.sound_recipes import recipe_for
from kihachi_mcp.models.production_brief import (
    DENSITY_VALUES,
    KEY_ENUM,
    REGISTER_VALUES,
    SOURCE_AI,
    SOURCE_DEFAULT,
    SOURCE_GENRE,
    SOURCE_USER,
    SUPPORTED_GENRES,
    ProductionBrief,
    SectionIntent,
    SourcedValue,
)
from kihachi_mcp.services.brief_parser import explicit_swing, extract_explicit

DEFAULT_MODEL = "gemma4:latest"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 11434
DEFAULT_TEMPO = 125
DEFAULT_KEY = "Dm"
DEFAULT_BARS = 96
DEFAULT_GENRE = "tech_house"

STUDIO_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "genre": {"type": "string", "enum": list(SUPPORTED_GENRES)},
        "tempo": {"type": "integer", "minimum": 60, "maximum": 180},
        "bars": {"type": "integer", "minimum": 4, "maximum": 256},
        "key": {"type": "string", "enum": list(KEY_ENUM)},
        "mood": {"type": "string"},
        "hats_first_half": {"type": "string", "enum": list(DENSITY_VALUES)},
        "hats_second_half": {"type": "string", "enum": list(DENSITY_VALUES)},
        "bass_register": {"type": "string", "enum": list(REGISTER_VALUES)},
        "note_density": {"type": "string", "enum": list(DENSITY_VALUES)},
        "drop_start_bar": {"type": "integer", "minimum": 0, "maximum": 256},
        "tone_brightness": {"type": "integer", "minimum": -2, "maximum": 2},
        "tone_length": {"type": "integer", "minimum": -2, "maximum": 2},
        "tone_delay": {"type": "integer", "minimum": -2, "maximum": 2},
        "unhandled": {"type": "array", "items": {"type": "string"}},
        "ambiguous": {"type": "array", "items": {"type": "string"}},
    },
}
STUDIO_SCHEMA["required"] = list(STUDIO_SCHEMA["properties"])
#: Fields an older model reply may leave out; they default to "no change".
OPTIONAL_INTENT_FIELDS = frozenset({"tone_brightness", "tone_length", "tone_delay"})

_DEFAULTS = {
    "genre": DEFAULT_GENRE,
    "tempo": DEFAULT_TEMPO,
    "bars": DEFAULT_BARS,
    "key": DEFAULT_KEY,
    "mood": "",
    "hats_first_half": "normal",
    "hats_second_half": "normal",
    "bass_register": "mid",
    "note_density": "normal",
    "drop_start_bar": 0,
    "tone_brightness": 0,
    "tone_length": 0,
    "tone_delay": 0,
}

_MOOD_INTERPRETATIONS = {
    "暗い": "低めの音域と短調中心の和声として解釈",
    "ダーク": "低めの音域と短調中心の和声として解釈",
    "明るい": "高めの音域と長調寄りの和声として解釈",
    "優しい": "音数を抑え、ベロシティを控えめに解釈",
    "激しい": "ドロップ以降の音数と強弱を上げて解釈",
    "冷たい": "高域のハットを残し、和声は短調中心として解釈",
}


class InterpretationError(ValueError):
    """Raised when model output cannot become a production brief."""


class InferenceCancelled(RuntimeError):
    """Raised when the caller closes an in-flight Ollama request."""


class OllamaClient:
    """One cancellable request to the installed local model."""

    def __init__(
        self,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        timeout: float = 180.0,
    ) -> None:
        self._host = host
        self._port = port
        self._timeout = timeout
        self._connection: HTTPConnection | None = None
        self._cancelled = False

    def cancel(self) -> None:
        """Close the in-flight socket. The model may still finish locally."""
        self._cancelled = True
        connection = self._connection
        if connection is not None:
            connection.close()

    def chat(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Send one /api/chat request. Never downloads a model."""
        if self._cancelled:
            raise InferenceCancelled("推論をキャンセルしました")
        connection = HTTPConnection(self._host, self._port, timeout=self._timeout)
        self._connection = connection
        try:
            connection.request(
                "POST",
                "/api/chat",
                json.dumps(payload),
                {"Content-Type": "application/json"},
            )
            response = connection.getresponse()
            raw = response.read(262145)
            if self._cancelled:
                raise InferenceCancelled("推論をキャンセルしました")
            if response.status != 200 or len(raw) > 262144:
                raise InterpretationError(
                    f"ローカルOllamaの応答に失敗しました (HTTP {response.status})"
                )
            return json.loads(raw)
        except OSError as exc:
            if self._cancelled:
                raise InferenceCancelled("推論をキャンセルしました") from exc
            raise InterpretationError(
                f"Ollamaに接続できません ({self._host}:{self._port})"
            ) from exc
        finally:
            connection.close()
            self._connection = None


def validate_model_intent(value: Any) -> dict[str, Any]:
    """Reject malformed model output before any music data is built."""
    allowed = set(STUDIO_SCHEMA["properties"])
    if (
        not isinstance(value, dict)
        or not set(value) <= allowed
        or allowed - set(value) - OPTIONAL_INTENT_FIELDS
    ):
        raise InterpretationError("AI応答のフィールドが許可スキーマと一致しません")
    value = {**{name: 0 for name in OPTIONAL_INTENT_FIELDS}, **value}
    for name, spec in STUDIO_SCHEMA["properties"].items():
        item = value[name]
        if spec["type"] == "integer":
            if type(item) is not int or not spec["minimum"] <= item <= spec["maximum"]:
                raise InterpretationError(f"AI応答の {name} が不正です")
        elif spec["type"] == "string":
            if not isinstance(item, str) or len(item) > 2000:
                raise InterpretationError(f"AI応答の {name} が不正です")
            if "enum" in spec and item not in spec["enum"]:
                raise InterpretationError(f"AI応答の {name} は未対応です")
        elif (
            not isinstance(item, list)
            or len(item) > 50
            or any(not isinstance(part, str) or len(part) > 2000 for part in item)
        ):
            raise InterpretationError(f"AI応答の {name} が不正です")
    if value["bars"] % 4:
        raise InterpretationError("小節数は4の倍数である必要があります")
    return value


def interpret_brief_offline(brief: str) -> ProductionBrief:
    """Build a brief from explicit text and defaults when Ollama is offline."""
    extracted = extract_explicit(brief)
    intent = {
        **_DEFAULTS,
        "unhandled": ["Ollamaがオフラインのため、明示指定と既定値だけで組み立てました"],
        "ambiguous": [],
    }
    production = assemble_brief(extracted, intent, model="deterministic")
    note = "Ollamaはオフラインです。明示された値と既定値で組み立てました。"
    return replace(
        production,
        provider="deterministic",
        interpretations=production.interpretations + (note,),
    )


def interpret_brief(
    brief: str,
    client: OllamaClient | None = None,
    model: str = DEFAULT_MODEL,
) -> ProductionBrief:
    """Preserve explicit values, then fill the rest from the local model."""
    extracted = extract_explicit(brief)
    payload = {
        "model": model,
        "stream": False,
        "think": False,
        "keep_alive": 0,
        "format": STUDIO_SCHEMA,
        "options": {
            "num_ctx": 2048,
            "num_predict": 512,
            "num_thread": 4,
            "temperature": 0,
        },
        "messages": [
            {
                "role": "system",
                "content": (
                    "Translate a Japanese music brief into the supplied JSON schema. "
                    "Preserve explicit tempo, key, bars and drop bar. "
                    "Defaults: tech_house, 125, Dm, 96. "
                    "Use sparse/normal/dense for hats and note_density. "
                    "Use low bass_register for dark/暗い briefs. "
                    "drop_start_bar is 0 when unspecified. "
                    "tone_brightness, tone_length and tone_delay are -2..2 steps "
                    "(brighter/duller, longer/shorter notes, more/less delay); "
                    "use 0 unless the brief describes the sound itself. "
                    "Put unsupported requests in unhandled and unclear ones in ambiguous. "
                    "Do not invent Ableton operations or executable code."
                ),
            },
            {"role": "user", "content": extracted["original_text"]},
        ],
    }
    session = client or OllamaClient()
    result = session.chat(payload)
    try:
        raw_intent = validate_model_intent(parse_model_json(result["message"]["content"]))
    except (KeyError, TypeError) as exc:
        raise InterpretationError("AI応答をJSONとして読めません") from exc
    return assemble_brief(extracted, raw_intent, model=model)


def parse_model_json(content: Any) -> Any:
    """Parse model JSON, repairing only code fences and text around one object."""
    if not isinstance(content, str):
        raise InterpretationError("AI応答をJSONとして読めません")
    text = content.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise InterpretationError("AI応答をJSONとして読めません")
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise InterpretationError("AI応答をJSONとして読めません") from exc


def interpret_with_fallback(
    brief: str,
    client_factory: Any = None,
    model: str = DEFAULT_MODEL,
    attempts: int = 2,
) -> ProductionBrief:
    """Try the local model a bounded number of times, then use explicit values.

    Cancellation is not a failure to recover from, so it propagates.
    """
    factory = client_factory or OllamaClient
    reasons: list[str] = []
    for _ in range(max(1, min(attempts, 3))):
        try:
            return interpret_brief(brief, client=factory(), model=model)
        except InferenceCancelled:
            raise
        except InterpretationError as exc:
            reasons.append(str(exc))
    fallback = interpret_brief_offline(brief)
    note = (
        f"AIの応答が{len(reasons)}回とも使えなかったため、明示指定と既定値で組み立てました"
        f"（{reasons[-1]}）"
    )
    return replace(
        fallback,
        unhandled=tuple(item for item in fallback.unhandled if "オフライン" not in item) + (note,),
        interpretations=tuple(
            item for item in fallback.interpretations if "オフライン" not in item
        )
        + (note,),
    )


def assemble_brief(
    extracted: dict[str, Any],
    model_intent: dict[str, Any],
    model: str = DEFAULT_MODEL,
) -> ProductionBrief:
    """User-specified values win. Invalid model output never reaches a plan."""
    intent = validate_model_intent(model_intent)
    user_fields = dict(extracted.get("fields") or {})
    merged: dict[str, SourcedValue] = {}
    contradictions: list[str] = []
    for name, default in _DEFAULTS.items():
        if name in user_fields:
            user_value = user_fields[name]
            model_value = intent[name]
            # The model may only answer three genres; a brief naming any of the
            # 1020 is read by rule, and disagreeing with it is not news.
            if model_value != user_value and name != "genre" and model != "deterministic":
                contradictions.append(
                    f"{name} は指示の {user_value} を保持し、AIの {model_value} は採用しません"
                )
            merged[name] = SourcedValue(user_value, SOURCE_USER)
            continue
        model_value = intent[name]
        if model_value == default:
            merged[name] = SourcedValue(default, SOURCE_DEFAULT)
        else:
            merged[name] = SourcedValue(model_value, SOURCE_AI)

    genre_notes = _apply_genre(merged, str(extracted["original_text"]))
    genre_notes += _tone_notes(merged)

    mood_text = str(merged["mood"].value or "")
    if merged["mood"].source != SOURCE_USER and "暗い" in extracted["original_text"]:
        merged["mood"] = SourcedValue("暗い", SOURCE_USER)
        mood_text = "暗い"
    if merged["bass_register"].source != SOURCE_USER and mood_text in {"暗い", "ダーク"}:
        merged["bass_register"] = SourcedValue("low", SOURCE_AI)

    bars = int(merged["bars"].value)
    drop = int(merged["drop_start_bar"].value)
    if drop and drop > bars:
        raise InterpretationError("ドロップ開始小節が曲の長さを超えています")
    sections = build_sections(bars, drop, str(merged["genre"].value))
    model_unhandled = _readable(intent.get("unhandled"))
    if explicit_swing(str(extracted["original_text"])) is not None:
        # The rule reads swing and the builder applies it to note timing.
        model_unhandled = [
            item for item in model_unhandled
            if "swing" not in item.lower() and "スイング" not in item
        ]
    unhandled = _unique(list(extracted.get("unhandled") or []) + model_unhandled)
    interpretations = genre_notes + build_interpretations(merged, sections)
    return ProductionBrief(
        original_text=str(extracted["original_text"]),
        tempo=merged["tempo"],
        key=merged["key"],
        bars=merged["bars"],
        genre=merged["genre"],
        mood=merged["mood"],
        hats_first_half=merged["hats_first_half"],
        hats_second_half=merged["hats_second_half"],
        bass_register=merged["bass_register"],
        note_density=merged["note_density"],
        drop_start_bar=merged["drop_start_bar"],
        tone_brightness=merged["tone_brightness"],
        tone_length=merged["tone_length"],
        tone_delay=merged["tone_delay"],
        sections=sections,
        interpretations=tuple(interpretations),
        unhandled=tuple(unhandled),
        ambiguous=tuple(_unique(_readable(intent.get("ambiguous")))),
        contradictions=tuple(contradictions),
        model=model,
    )


def _readable(items: Any) -> list[str]:
    """Drop bare field names such as "duration": they tell the user nothing."""
    return [
        str(item)
        for item in items or []
        if str(item).strip() and not re.fullmatch(r"[a-z_]+", str(item).strip())
    ]


def _apply_genre(merged: dict[str, SourcedValue], text: str) -> list[str]:
    """Let the database speak for tempo when nobody else did, and say so."""
    genre = find_genre(str(merged["genre"].value))
    if genre is None:
        return []
    notes: list[str] = []
    bpm = genre.informative_bpm
    range_text = f"{bpm[0]:g}–{bpm[1]:g} BPM" if bpm else "範囲が広く目安なし"
    notes.append(f"ジャンル: {genre.name}（{genre.family} 系、一般的なテンポ {range_text}）")
    if genre.slug == "mutation_funk":
        notes.append(
            "リズム: 変則キック、2小節で応答するベース、弱いゴーストノートを使います。"
            "参考音源のMIDIを抽出したものではなく、現段階では試作パターンです"
        )
        notes.append(
            "音色: Analogの短いベースとWavetableの短いコードを試作設定します。"
            "参考曲の音色を複製したものではありません"
        )
    tempo = merged["tempo"]
    if tempo.source in {SOURCE_DEFAULT} and bpm is not None:
        suggested = typical_bpm([genre.slug])
        if suggested is not None and 60 <= suggested <= 180:
            merged["tempo"] = SourcedValue(suggested, SOURCE_GENRE)
            notes.append(f"テンポの指定がないため、{genre.name} の目安から {suggested} BPM にしました")
    elif tempo.source == SOURCE_USER and bpm is not None:
        value = float(tempo.value)
        if not bpm[0] <= value <= bpm[1]:
            side = "遅め" if value < bpm[0] else "速め"
            notes.append(
                f"{value:g} BPM は {genre.name} の一般的な範囲 {range_text} より{side}です"
                f"（指示どおり {value:g} BPM にします）"
            )
    others = [match.genre.name for match in match_genres(text)[1:]]
    if others:
        notes.append(
            f"ほかに名前が出たジャンル: {'、'.join(others)}（今は先頭の {genre.name} だけを使います）"
        )
    return notes


_TONE_WORDS = {
    "tone_brightness": ("明るく", "こもらせて"),
    "tone_length": ("長く", "短く"),
    "tone_delay": ("ディレイを多く", "ディレイを少なく"),
}


def _tone_notes(merged: dict[str, SourcedValue]) -> list[str]:
    """Say which tone steps were taken and whether a recipe will use them."""
    moved = [
        f"{_TONE_WORDS[name][0 if merged[name].value > 0 else 1]}（{abs(merged[name].value)}段階）"
        for name in _TONE_WORDS
        if merged[name].value
    ]
    if not moved:
        return []
    genre = str(merged["genre"].value)
    if recipe_for(genre) is None:
        return [f"音色: {'、'.join(moved)}。{genre} には音色レシピがないため、今は音に反映しません"]
    return [f"音色: {'、'.join(moved)}（{genre} の音色レシピに適用、曲全体で一定）"]


def build_sections(bars: int, drop_start_bar: int, genre: str = "") -> tuple[SectionIntent, ...]:
    """Build contiguous sections, honoring an explicit drop start bar.

    Shaped for listening to one song rather than for a DJ's mix: a short Intro
    and Outro, and a Break between two Drops so the song goes somewhere.
    """
    if drop_start_bar:
        lengths = [
            *_before_drop(drop_start_bar - 1),
            *_from_drop(bars - drop_start_bar + 1),
        ]
        return _contiguous(lengths)

    if genre == "mutation_funk" and bars >= 32:
        if bars >= 96:
            lengths = [8, 16, 16, 16, 8, 16, 16]
        elif bars >= 64:
            lengths = [8, 8, 8, 16, 8, 8, 8]
        else:
            lengths = [4, 4, 4, 4, 4, 8, 4]
        for index in range((bars - sum(lengths)) // 4):
            lengths[(1, 3, 2, 5)[index % 4]] += 4
        cursor = 1
        sections = []
        for name, length in zip(
            ("Intro", "VerseA", "VerseB", "ChorusA", "Break", "ChorusB", "Outro"),
            lengths,
        ):
            sections.append(SectionIntent(name, cursor, length))
            cursor += length
        return tuple(sections)

    if genre == "j_pop" and bars >= 32:
        # Verse/chorus form, with the second chorus providing the late peak.
        unit, remainder = divmod(bars, 16)
        return _contiguous([
            ("Intro", unit), ("VerseA", unit * 3), ("ChorusA", unit * 4),
            ("VerseB", unit * 2), ("Break", unit),
            ("ChorusB", unit * 4 + remainder), ("Outro", unit),
        ])

    if genre == "liquid_drum_bass" and bars >= 32:
        # A restrained verse delays the first drop; the reprise is shorter.
        eighth, remainder = divmod(bars, 8)
        return _contiguous([
            ("Intro", eighth), ("VerseA", eighth), ("Build", eighth),
            ("Drop", eighth * 2), ("Break", eighth),
            ("Drop", eighth + remainder), ("Outro", eighth),
        ])

    if bars >= 32:
        # Eighths: Intro, Build, Drop x2, Break, Drop x2, Outro.
        eighth = bars // 8
        return _contiguous(
            [
                ("Intro", eighth),
                ("Build", eighth),
                ("Drop", eighth * 2),
                ("Break", eighth),
                ("Drop", eighth * 2),
                ("Outro", bars - eighth * 7),
            ]
        )

    quarter = bars // 4
    return (
        SectionIntent("Intro", 1, quarter),
        SectionIntent("Build", 1 + quarter, quarter),
        SectionIntent("Drop", 1 + quarter * 2, quarter),
        SectionIntent("Outro", 1 + quarter * 3, bars - quarter * 3),
    )


def _before_drop(bars: int) -> list[tuple[str, int]]:
    """Intro and Build before the drop; a long run-up gets a groove between."""
    if bars <= 0:
        return []
    if bars < 12:
        return [("Intro", bars)]
    if bars < 32:
        return [("Intro", bars - 8), ("Build", 8)]
    if bars == 32:
        return [("Intro", 16), ("Build", 16)]
    return [("Intro", 16), ("VerseA", bars - 32), ("Build", 16)]


def _from_drop(bars: int) -> list[tuple[str, int]]:
    """Drop, Break, a second Drop and a short Outro, as the bars allow."""
    if bars >= 48:
        return [("Drop", 16), ("Break", 8), ("Drop", bars - 32), ("Outro", 8)]
    if bars >= 32:
        return [("Drop", 16), ("Break", 4), ("Drop", bars - 24), ("Outro", 4)]
    if bars >= 24:
        return [("Drop", bars - 8), ("Outro", 8)]
    if bars >= 12:
        return [("Drop", bars - 4), ("Outro", 4)]
    return [("Drop", bars)]


def _contiguous(lengths: list[tuple[str, int]]) -> tuple[SectionIntent, ...]:
    sections = []
    cursor = 1
    for name, length in lengths:
        if length > 0:
            sections.append(SectionIntent(name, cursor, length))
            cursor += length
    return tuple(sections)


def build_interpretations(
    merged: dict[str, SourcedValue], sections: tuple[SectionIntent, ...]
) -> list[str]:
    """Turn abstract words into the concrete choices shown on screen."""
    notes: list[str] = []
    mood = str(merged["mood"].value or "")
    if mood in _MOOD_INTERPRETATIONS:
        notes.append(_MOOD_INTERPRETATIONS[mood])
    notes.append(
        f"ハット密度は前半を{_density_ja(merged['hats_first_half'].value)}、"
        f"後半を{_density_ja(merged['hats_second_half'].value)}として生成します"
    )
    notes.append(
        f"ベース音域は{_register_ja(merged['bass_register'].value)}、"
        f"全体の音数は{_density_ja(merged['note_density'].value)}です"
    )
    drop = int(merged["drop_start_bar"].value)
    if drop:
        notes.append(f"{drop}小節目から Drop セクションを開始します")
    names = " → ".join(
        f"{section.name} {section.start_bar}–{section.end_bar}" for section in sections
    )
    notes.append(f"構成: {names}")
    for name in ("tempo", "key", "bars"):
        sourced = merged[name]
        if sourced.source == SOURCE_DEFAULT:
            notes.append(f"{name} は指定がなかったため {sourced.value} を補いました")
    return notes


def _density_ja(value: Any) -> str:
    return {"sparse": "少なく", "normal": "標準", "dense": "多く"}.get(str(value), str(value))


def _register_ja(value: Any) -> str:
    return {"low": "低め", "mid": "中音域", "high": "高め"}.get(str(value), str(value))


def _unique(items: list[Any]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        text = str(item).strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result
