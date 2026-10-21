"""Which statements of a brief reached the notes, and which reached nothing.

A brief can be ignored silently. The Studio's rule readers know tempo, key,
bars, a drop bar, hat density per half, a few part- or section-scoped requests
and a handful of mood words; the local
model fills a fixed set of fields. Anything else -- "BASS gets busier over
time", "make the drop flashy" -- used to vanish without a word, because the
only list of unsupported requests was the model's own report.

This splits the brief into statements and marks each one by what the rule
readers in :mod:`brief_parser` actually matched there. It re-uses their spans
rather than restating their patterns, so it cannot drift from what they do.

Model-filled fields have no span: the model does not say which words it used.
They are reported next to the statements instead, and a statement no rule read
is called "not reflected, as far as the rules can tell" -- never "rejected".
"""

from typing import Any

from kihachi_mcp.knowledge.sound_recipes import recipe_for
from kihachi_mcp.services.brief_parser import (
    DECLINED,
    out_of_scope_spans,
    read_explicit,
)

BRIEF_COVERAGE_VERSION = "0.1"
CLAUSE_SEPARATORS = "。、\n;；！!？?"
PARTIAL_AT_OR_BELOW = 0.5

_LABELS = {
    "tempo": "テンポ",
    "key": "キー",
    "bars": "小節数",
    "drop_start_bar": "ドロップ位置",
    "bass_activity_curve": "ベースの動きの段階変化（MIDIの音数）",
    "bass_activity": "ベースの動き（MIDIの音数）",
    "upper_brightness": "上物の煌びやかさ（MIDIの音域、音色変化なし）",
    "drop_emphasis": "Dropの強調（上物のMIDIの音域・強弱）",
    "mid_break": "中間のBreak構成",
    "hats_first_half": "前半のハット",
    "hats_second_half": "後半のハット",
    "mood": "ムード",
    "genre": "ジャンル",
    "echo": "ディレイ",
    "swing": "スイング",
    "tone_brightness": "音の明るさ（曲全体）",
    "tone_length": "音の長さ（曲全体）",
    "tone_delay": "ディレイの量",
}

#: Model fields that change the notes, with how the UI names them.
MODEL_FIELDS = {
    "genre": "ジャンル",
    "mood": "ムード",
    "hats_first_half": "前半のハット",
    "hats_second_half": "後半のハット",
    "bass_register": "ベース音域",
    "note_density": "音数",
    "drop_start_bar": "ドロップ位置",
    "tone_brightness": "音の明るさ",
    "tone_length": "音の長さ",
    "tone_delay": "ディレイの量",
}
TONE_FIELD_NAMES = frozenset({"tone_brightness", "tone_length", "tone_delay"})

#: Words for what a genre's generator always writes, whether asked or not.
#: A match is "the pattern already has it", never "the words were read".
GENRE_BUILT_IN: dict[str, tuple[tuple[tuple[str, ...], str], ...]] = {
    "mutation_funk": (
        (("ghost", "ゴースト"), "ゴーストノート"),
        (("octave", "オクターブ"), "ベースのオクターブ移動"),
        (("syncopa", "シンコペ"), "シンコペーション"),
        (("slap", "スラップ"), "短く跳ねるベース"),
        (("dub chord", "ダブコード", "ダブ・コード"), "Stabのコード"),
        (("mutation synth", "ミューテーション"), "Leadパート"),
    ),
}


def _classify_label(
    label: str,
    fields: dict[str, Any],
    brief: Any = None,
) -> str:
    """Classify a request label into 'declined', 'applied', 'unverified', or 'failed'."""
    if _is_declined(label, fields):
        return "declined"

    if label == "mid_break" and fields.get("mid_break") == "required":
        if brief is None:
            # Prior to candidate/section generation, placement has not been verified.
            return "unverified"
        if hasattr(brief, "sections") and hasattr(brief, "bars"):
            from kihachi_mcp.services.studio_interpreter import is_mid_break

            bars_val = int(brief.bars.value)
            if any(is_mid_break(s, bars_val) for s in brief.sections):
                return "applied"
            return "failed"
        return "unverified"

    return "applied"


def read_coverage(
    text_or_brief: Any,
    model_filled: list[dict[str, Any]] | None = None,
    genre: str | None = None,
    *,
    brief: Any = None,
    unhandled: list[str] | None = None,
) -> dict[str, Any]:
    """Mark every statement as read, partly read, unverified, genre default, out of scope, or unread."""
    if hasattr(text_or_brief, "original_text"):
        brief = text_or_brief
        text = str(brief.original_text)
        if genre is None and hasattr(brief, "genre"):
            genre = str(brief.genre.value)
        if model_filled is None:
            model_filled = model_filled_fields(brief)
    else:
        text = str(text_or_brief)

    fields, spans = read_explicit(text)
    scope = out_of_scope_spans(text)

    clauses = []
    for start, clause in _clauses(text):
        end = start + len(clause)
        touching = {label for a, b, label in spans if a < end and b > start}
        declined_labels = {label for label in touching if _classify_label(label, fields, brief) == "declined"}
        applied_labels = {label for label in touching if _classify_label(label, fields, brief) == "applied"}
        unverified_labels = {label for label in touching if _classify_label(label, fields, brief) == "unverified"}
        failed_labels = {label for label in touching if _classify_label(label, fields, brief) == "failed"}

        read_as = sorted({_label(label, fields) for label in (applied_labels | declined_labels)})
        unverified = sorted({_label(label, fields) for label in unverified_labels})
        unapplied = sorted({_label(label, fields) for label in failed_labels})
        out = sorted({message for a, b, message in scope if a < end and b > start})

        touched: set[int] = set()
        for a, b, label in spans:
            if label in applied_labels or label in declined_labels:
                touched.update(range(max(a, start), min(b, end)))
        covered = round(len(touched) / len(clause), 4) if clause else 0.0
        built_in = _built_in(clause, genre)

        if touching and touching == declined_labels:
            # Read, and honoured by changing nothing: not "reflected" as a request.
            state = "declined"
        elif applied_labels:
            if failed_labels or unverified_labels or covered <= PARTIAL_AT_OR_BELOW:
                state = "partly_read"
            else:
                state = "read"
        elif unverified_labels:
            state = "unverified"
        elif failed_labels:
            state = "unread"
        elif built_in:
            state = "genre_default"
            read_as = built_in
        elif out:
            state = "out_of_scope"
        else:
            state = "unread"

        clauses.append(
            {
                "text": clause,
                "state": state,
                "read_as": read_as,
                "unverified": unverified,
                "unapplied": unapplied,
                "out_of_scope": out,
                "covered": covered,
            }
        )
    unread = [item["text"] for item in clauses if item["state"] == "unread"]
    partly_read = [item["text"] for item in clauses if item["state"] == "partly_read"]
    unverified_clauses = [item["text"] for item in clauses if item["state"] == "unverified"]
    declined = [item["text"] for item in clauses if item["state"] == "declined"]
    return {
        "brief_coverage_version": BRIEF_COVERAGE_VERSION,
        "clauses": clauses,
        "unread": unread,
        "partly_read": partly_read,
        "unverified": unverified_clauses,
        "declined": declined,
        "read_fraction": (
            round(
                sum(item["state"] in {"read", "partly_read", "declined"} for item in clauses)
                / len(clauses),
                4,
            )
            if clauses
            else 0.0
        ),
        "model_filled": list(model_filled or []),
        "note": (
            "未反映は規則で読めなかった文です。AIが補った項目のうち"
            " affects_notes が true のものに反映された可能性はあります"
        ),
    }


#: Mood words the MIDI builder reacts to. Any other mood is shown, not used.
MOODS_THAT_CHANGE_NOTES = frozenset({"暗い", "ダーク"})
#: Fields the builder does not read yet.
FIELDS_WITHOUT_EFFECT: frozenset[str] = frozenset()


def model_filled_fields(brief: Any) -> list[dict[str, Any]]:
    """The fields the model, not the brief's words, decided -- and which matter.

    Saying a value was filled is not saying it changed the notes. A genre the
    builder never reads, or a mood it has no rule for, is reported as such.
    """
    filled: list[dict[str, Any]] = []
    for name, label in MODEL_FIELDS.items():
        value = getattr(brief, name, None)
        if value is None or getattr(value, "source", "") != "ai":
            continue
        affects = name not in FIELDS_WITHOUT_EFFECT and (
            name != "mood" or str(value.value) in MOODS_THAT_CHANGE_NOTES
        )
        if name in TONE_FIELD_NAMES:
            # A tone step only turns knobs a sound recipe sets.
            affects = recipe_for(str(brief.genre.value)) is not None
        filled.append({"label": label, "value": str(value.value), "affects_notes": affects})
    return filled


#: How a refused request is named: what KIHACHI leaves alone, not what it adds.
_DECLINED_LABELS = {
    "upper_brightness": "上物を煌びやかにしない（否定として認識・変更なし）",
    "drop_emphasis": "Dropを派手にしない（否定として認識・変更なし）",
    "tone_brightness": "曲全体の明るさを変えない（否定として認識・変更なし）",
    "mid_break": "中間にブレイクを入れない（否定として認識・既定構成のBreakは維持）",
}


def _is_declined(label: str, fields: dict[str, Any]) -> bool:
    value = fields.get(label)
    if label == "tone_brightness":
        return value == 0
    return label in _DECLINED_LABELS and value == DECLINED


def _label(label: str, fields: dict[str, Any]) -> str:
    if _is_declined(label, fields):
        return _DECLINED_LABELS[label]
    if label == "upper_brightness" and fields.get(label) == "rising":
        return "上物の段階的な煌びやかさ（MIDIの音域・強弱、音色変化なし）"
    return _LABELS.get(label, label)


def _built_in(clause: str, genre: str | None) -> list[str]:
    lowered = clause.lower()
    return [
        label
        for words, label in GENRE_BUILT_IN.get(genre or "", ())
        if any(word in lowered for word in words)
    ]


def _clauses(text: str) -> list[tuple[int, str]]:
    clauses: list[tuple[int, str]] = []
    start = 0
    for index, character in enumerate(text):
        if character in CLAUSE_SEPARATORS:
            _append(clauses, text, start, index)
            start = index + 1
    _append(clauses, text, start, len(text))
    return clauses


def _append(clauses: list[tuple[int, str]], text: str, start: int, end: int) -> None:
    raw = text[start:end]
    stripped = raw.strip()
    if stripped:
        clauses.append((start + raw.index(stripped), stripped))
