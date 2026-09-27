from kihachi_mcp.models.production_brief import SourcedValue
from kihachi_mcp.services.brief_coverage import model_filled_fields, read_coverage
from kihachi_mcp.services.brief_parser import extract_explicit, read_explicit

USER_BRIEF = (
    "105 BPM、D＃マイナー、144小節で5分程度の重いDUB TECHNO。\n"
    "前半はハットを少なく、後半で増やす。\n"
    "中間でBrakeを必ず入れドロップは派手に。\n"
    "BASSは最初動き少なく徐々に動きのあるものへ変化する。\n"
    "上物は徐々に煌びやかに。\n"
    "DUB ディレイをところどころにいれる。ボーカルも入れて"
)


def _states(coverage):
    return {clause["text"]: clause["state"] for clause in coverage["clauses"]}


def test_statements_nothing_reads_are_listed_not_dropped() -> None:
    """The first real dub-techno brief lost five statements without a word."""
    coverage = read_coverage(USER_BRIEF)
    states = _states(coverage)
    assert states["105 BPM"] == "read"
    assert states["D＃マイナー"] == "read"
    assert states["後半で増やす"] == "read"
    assert states["中間でBrakeを必ず入れドロップは派手に"] == "unread"
    assert states["BASSは最初動き少なく徐々に動きのあるものへ変化する"] == "unread"
    assert states["上物は徐々に煌びやかに"] == "unread"
    assert "DUB ディレイをところどころにいれる" in coverage["unread"]


def test_a_statement_read_only_in_part_says_so() -> None:
    states = _states(read_coverage(USER_BRIEF))
    assert states["144小節で5分程度の重いDUB TECHNO"] == "partly_read"


def test_known_unsupported_requests_are_out_of_scope_not_unread() -> None:
    coverage = read_coverage(USER_BRIEF)
    clause = next(item for item in coverage["clauses"] if item["text"] == "ボーカルも入れて")
    assert clause["state"] == "out_of_scope"
    assert clause["out_of_scope"]


def test_coverage_uses_the_same_spans_the_extractor_used() -> None:
    fields, spans = read_explicit(USER_BRIEF)
    assert fields == {
        name: value
        for name, value in extract_explicit(USER_BRIEF)["fields"].items()
    }
    assert {label for _start, _end, label in spans} == set(fields)


def test_a_bar_position_before_the_length_does_not_hide_the_length() -> None:
    assert extract_explicit("57小節目からドロップ、96小節の暗いテクノ")["fields"]["bars"] == 96


def test_model_filled_lists_only_ai_sourced_fields() -> None:
    class Brief:
        genre = SourcedValue("dub_techno", "ai")
        mood = SourcedValue("暗い", "user")
        hats_first_half = SourcedValue("sparse", "user")
        hats_second_half = SourcedValue("dense", "ai")
        bass_register = SourcedValue("low", "ai")
        note_density = SourcedValue("normal", "default")
        drop_start_bar = SourcedValue(0, "default")

    assert model_filled_fields(Brief()) == [
        {"label": "ジャンル", "value": "dub_techno", "affects_notes": False},
        {"label": "後半のハット", "value": "dense", "affects_notes": True},
        {"label": "ベース音域", "value": "low", "affects_notes": True},
    ]


def test_a_mood_the_builder_has_no_rule_for_is_not_claimed() -> None:
    class Brief:
        genre = SourcedValue("tech_house", "default")
        mood = SourcedValue("heavy", "ai")

    assert model_filled_fields(Brief()) == [
        {"label": "ムード", "value": "heavy", "affects_notes": False}
    ]
