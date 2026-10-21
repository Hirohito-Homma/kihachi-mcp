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


def test_genre_defaults_are_named_but_not_called_read() -> None:
    text = "Ghost notes。Octave movement。4つ打ちKick"
    states = _states(read_coverage(text, genre="mutation_funk"))
    assert states["Ghost notes"] == "genre_default"
    assert states["Octave movement"] == "genre_default"
    # Mutation Funk writes an irregular kick; a four-on-the-floor request stays unread.
    assert states["4つ打ちKick"] == "unread"
    assert _states(read_coverage(text))["Ghost notes"] == "unread"


def test_statements_nothing_reads_are_listed_not_dropped() -> None:
    """The first real dub-techno brief lost five statements without a word."""
    coverage = read_coverage(USER_BRIEF)
    states = _states(coverage)
    assert states["105 BPM"] == "read"
    assert states["D＃マイナー"] == "read"
    assert states["後半で増やす"] == "read"
    assert states["BASSは最初動き少なく徐々に動きのあるものへ変化する"] == "read"
    assert states["上物は徐々に煌びやかに"] == "read"
    # The Drop is applied, but the Break is unverified prior to arrangement generation.
    assert states["中間でBrakeを必ず入れドロップは派手に"] == "partly_read"


def test_progressive_bass_request_is_explicitly_read() -> None:
    text = "BASSは最初動き少なく徐々に動きのあるものへ変化する"
    fields, spans = read_explicit(text)
    assert fields["bass_activity_curve"] == "rising"
    assert any(label == "bass_activity_curve" for _start, _end, label in spans)
    clause = read_coverage(text)["clauses"][0]
    assert clause["state"] == "read"
    assert clause["read_as"] == ["ベースの動きの段階変化（MIDIの音数）"]


def test_scoped_sound_words_are_not_a_whole_song_tone_step() -> None:
    fields, _spans = read_explicit(USER_BRIEF)
    assert fields["upper_brightness"] == "rising"
    assert fields["drop_emphasis"] == "flashy"
    assert "tone_brightness" not in fields
    # A sound word of its own still brightens the whole song.
    fields, _spans = read_explicit("煌びやかな音で、ドロップは派手に")
    assert fields["tone_brightness"] == 1
    assert fields["drop_emphasis"] == "flashy"
    fields, _spans = read_explicit("ハウス、派手なドロップ")
    assert fields["drop_emphasis"] == "flashy"
    assert "tone_brightness" not in fields


def test_scoped_requests_are_labelled_as_midi_not_sound() -> None:
    labels = {
        item["text"]: item["read_as"]
        for item in read_coverage("上物は徐々に煌びやかに。ドロップは派手に")["clauses"]
    }
    assert labels["上物は徐々に煌びやかに"] == [
        "上物の段階的な煌びやかさ（MIDIの音域・強弱、音色変化なし）"
    ]
    assert labels["ドロップは派手に"] == ["Dropの強調（上物のMIDIの音域・強弱）"]


def _brightness(text: str) -> dict:
    fields, _spans = read_explicit(text)
    names = (
        "tone_brightness",
        "upper_brightness",
        "drop_emphasis",
        "bass_activity_curve",
        "bass_activity",
    )
    return {name: fields[name] for name in names if name in fields}


def test_a_refused_request_is_declined_never_applied_or_turned_darker() -> None:
    for text in (
        "派手じゃないドロップ",
        "派手ではないドロップ",
        "ドロップは派手にしない",
        "派手なドロップにしない",
        "ドロップは派手にしすぎない",
        "ドロップは派手ではなく渋く",
    ):
        assert _brightness(text) == {"drop_emphasis": "declined"}, text
    for text in (
        "上物は煌びやかにしない",
        "煌びやかな上物にしない",
        "上物は徐々に煌びやかにしない",
    ):
        assert _brightness(text) == {"upper_brightness": "declined"}, text
    # Refusing the whole song's brightness pins it at no change, not at darker.
    assert _brightness("派手にしない") == {"tone_brightness": 0}
    assert _brightness("BASSは最初動き少なく徐々に動きを増やさない") == {}
    assert _brightness("ベースは動きを増やさない") == {}


def test_upper_part_words_stay_on_the_upper_parts_without_gradually() -> None:
    assert _brightness("上物は煌びやか") == {"upper_brightness": "steady"}
    assert _brightness("キラキラした上物") == {"upper_brightness": "steady"}
    assert _brightness("上物は徐々に煌びやかに") == {"upper_brightness": "rising"}
    assert _brightness("上物は、徐々に煌びやかに") == {"upper_brightness": "rising"}


def test_compound_briefs_keep_each_statement_on_its_own_target() -> None:
    assert _brightness("派手じゃないドロップ。上物は徐々に煌びやかに") == {
        "drop_emphasis": "declined",
        "upper_brightness": "rising",
    }
    assert _brightness("派手じゃないドロップで上物は徐々に煌びやかに") == {
        "drop_emphasis": "declined",
        "upper_brightness": "rising",
    }
    assert _brightness("上物は煌びやか、ベースはこもった音") == {"upper_brightness": "steady"}
    assert _brightness("曲全体は煌びやかに。ドロップは派手にしない") == {
        "tone_brightness": 1,
        "drop_emphasis": "declined",
    }
    assert _brightness("上物は煌びやかにしない。ドロップは派手に") == {
        "upper_brightness": "declined",
        "drop_emphasis": "flashy",
    }
    assert _brightness("上物は煌びやかにしない。ベースは動きを増やす") == {
        "upper_brightness": "declined",
        "bass_activity": "dense",
    }
    # The refusal belongs to the bass, named after the upper parts' request.
    assert _brightness("上物は煌びやかでベースは派手にしない") == {"upper_brightness": "steady"}


def test_a_word_for_one_other_part_is_out_of_scope_not_the_whole_song() -> None:
    text = "上物は煌びやか、ベースはこもった音"
    clauses = {item["text"]: item for item in read_coverage(text)["clauses"]}
    assert clauses["ベースはこもった音"]["state"] == "out_of_scope"
    assert "「ベース」だけをこもらせる" in clauses["ベースはこもった音"]["out_of_scope"][0]
    assert any("「ベース」だけ" in item for item in extract_explicit(text)["unhandled"])


def test_refusals_show_as_declined_and_mixed_clauses_keep_the_request() -> None:
    coverage = read_coverage(
        "派手じゃないドロップ。派手なドロップにしない。"
        "上物は煌びやかにしない。煌びやかな上物にしない。曲全体は派手にしない"
    )
    clauses = {item["text"]: item for item in coverage["clauses"]}
    assert clauses["派手じゃないドロップ"]["state"] == "declined"
    assert clauses["派手じゃないドロップ"]["read_as"] == [
        "Dropを派手にしない（否定として認識・変更なし）"
    ]
    assert clauses["派手なドロップにしない"]["state"] == "declined"
    assert clauses["上物は煌びやかにしない"]["state"] == "declined"
    assert clauses["煌びやかな上物にしない"]["state"] == "declined"
    assert clauses["曲全体は派手にしない"]["state"] == "declined"
    assert coverage["declined"] == [
        "派手じゃないドロップ",
        "派手なドロップにしない",
        "上物は煌びやかにしない",
        "煌びやかな上物にしない",
        "曲全体は派手にしない",
    ]
    mixed = read_coverage("派手じゃないドロップで上物は徐々に煌びやかに")["clauses"][0]
    assert mixed["state"] == "read"
    assert "上物の段階的な煌びやかさ（MIDIの音域・強弱、音色変化なし）" in mixed["read_as"]
    assert "Dropを派手にしない（否定として認識・変更なし）" in mixed["read_as"]
    compound = {
        item["text"]: item
        for item in read_coverage("上物は煌びやかにしない。ベースは動きを増やす")["clauses"]
    }
    assert compound["上物は煌びやかにしない"]["state"] == "declined"
    assert compound["ベースは動きを増やす"]["state"] == "read"
    assert compound["ベースは動きを増やす"]["read_as"] == ["ベースの動き（MIDIの音数）"]

    pos_cov = {
        item["text"]: item
        for item in read_coverage(
            "中間にブレイクを入れて。中間でBreakを必ず入れ。中間でBrakeを必ず入れ。車のブレーキを踏む"
        )["clauses"]
    }
    # Prior to candidate generation, positive mid break statements are recognized but unverified.
    assert pos_cov["中間にブレイクを入れて"]["state"] == "unverified"
    assert pos_cov["中間にブレイクを入れて"]["unverified"] == ["中間のBreak構成"]
    assert pos_cov["中間でBreakを必ず入れ"]["state"] == "unverified"
    assert pos_cov["中間でBrakeを必ず入れ"]["state"] == "unverified"
    assert pos_cov["車のブレーキを踏む"]["state"] == "unread"

    neg_cov = {
        item["text"]: item
        for item in read_coverage(
            "中間にブレイクを入れない。中間でBreakを入れない。中間でBrakeは入れない"
        )["clauses"]
    }
    assert neg_cov["中間にブレイクを入れない"]["state"] == "declined"
    assert neg_cov["中間にブレイクを入れない"]["read_as"] == [
        "中間にブレイクを入れない（否定として認識・既定構成のBreakは維持）"
    ]
    assert neg_cov["中間でBreakを入れない"]["state"] == "declined"
    assert neg_cov["中間でBrakeは入れない"]["state"] == "declined"


def test_studio_html_renders_declined_coverage_state() -> None:
    import subprocess
    from pathlib import Path

    html_path = (
        Path(__file__).resolve().parents[1]
        / "src/kihachi_mcp/studio/static/index.html"
    )
    script = f"""
    const assert = require("node:assert/strict");
    const fs = require("node:fs");
    const vm = require("node:vm");
    const html = fs.readFileSync({str(html_path)!r}, "utf8");
    const start = html.indexOf("    const COVERAGE_STATES =");
    const end = html.indexOf("    function renderCandidate(");
    const nodes = {{ "coverage-line": {{ textContent: "" }}, "coverage": {{ innerHTML: "" }} }};
    const ctx = vm.createContext({{ $: (id) => nodes[id] }});
    vm.runInContext(html.slice(start, end), ctx);
    ctx.renderCoverageList({{
      clauses: [
        {{
          text: "煌びやかな上物にしない",
          state: "declined",
          read_as: ["上物を煌びやかにしない（否定として認識・変更なし）"],
          out_of_scope: []
        }},
        {{
          text: "ベースは動きを増やす",
          state: "read",
          read_as: ["ベースの動き（MIDIの音数）"],
          out_of_scope: []
        }}
      ]
    }});
    assert.match(nodes["coverage"].innerHTML, /<span class="ok">否定として認識（変更なし）<\\/span> 煌びやかな上物にしない/);
    assert.match(nodes["coverage"].innerHTML, /<span class="ok">反映<\\/span> ベースは動きを増やす/);
    assert.equal(nodes["coverage-line"].textContent, "2 文すべてに、規則で読めた部分があります");
    """
    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr

def test_plain_whole_song_words_still_brighten_or_dull_the_song() -> None:
    assert _brightness("テックハウス、32小節、煌びやかに") == {"tone_brightness": 1}
    assert _brightness("かなり煌びやかに") == {"tone_brightness": 2}
    assert _brightness("こもったダブ") == {"tone_brightness": -1}
    assert _brightness("派手なテクノ") == {"tone_brightness": 1}


def test_a_scoped_request_does_not_borrow_words_from_the_next_sentence() -> None:
    assert "bass_activity_curve" not in read_explicit(
        "ベースは最初シンプル。徐々に動きを増やす"
    )[0]
    assert "upper_brightness" not in read_explicit("上物はそのまま。徐々に煌びやかに")[0]
    # A pause inside one request is still that request.
    assert read_explicit("BASSは最初動き少なく、徐々に動きを出す")[0][
        "bass_activity_curve"
    ] == "rising"


def test_drum_and_bass_is_a_genre_not_the_bass_part() -> None:
    fields, _spans = read_explicit("Drum & Bassで最初は動き少なく徐々に動きを出す")
    assert "bass_activity_curve" not in fields


def test_model_brightness_is_dropped_when_the_brief_scoped_it() -> None:
    from kihachi_mcp.services.studio_interpreter import assemble_brief

    intent = {
        "genre": "dub_techno",
        "tempo": 120,
        "bars": 64,
        "key": "Dm",
        "mood": "",
        "hats_first_half": "normal",
        "hats_second_half": "normal",
        "bass_register": "mid",
        "note_density": "normal",
        "drop_start_bar": 0,
        "tone_brightness": 1,
        "unhandled": [],
        "ambiguous": [],
    }
    for text in (
        "ドロップは派手に",
        "派手じゃないドロップ",
        "上物は煌びやか",
        "キラキラした上物",
        "上物は煌びやかにしない。ドロップは派手に",
        "上物は煌びやか、ベースはこもった音",
    ):
        for guess in (1, 2, -1):
            brief = assemble_brief(
                extract_explicit(text), {**intent, "tone_brightness": guess}, model="gemma4"
            )
            assert brief.tone_brightness.value == 0, (text, guess)
            assert any(
                f"曲全体の明るさ（{guess:+d}）は採用しません" in item
                for item in brief.interpretations
            ), (text, guess)
    # A refused whole-song request is the user's 0, which the model cannot move.
    refused = assemble_brief(extract_explicit("曲全体は派手にしない"), intent, model="gemma4")
    assert refused.tone_brightness.value == 0
    assert refused.tone_brightness.source == "user"
    assert any("曲全体の明るさを変えない" in item for item in refused.interpretations)
    # Words that name nothing, or no brightness word at all, leave the model's guess.
    for text in ("ダブテクノ", "華やかなダブテクノ"):
        plain = assemble_brief(extract_explicit(text), intent, model="gemma4")
        assert plain.tone_brightness.value == 1, text


def test_a_statement_read_only_in_part_says_so() -> None:
    states = _states(read_coverage("144小節で5分程度の重く揺れる感じ"))
    assert states["144小節で5分程度の重く揺れる感じ"] == "partly_read"


def test_a_named_genre_reads_its_statement() -> None:
    clause = next(
        item
        for item in read_coverage(USER_BRIEF)["clauses"]
        if item["text"] == "144小節で5分程度の重いDUB TECHNO"
    )
    assert clause["read_as"] == ["ジャンル", "小節数"]


def test_a_dub_delay_is_read_as_a_delay_not_a_genre() -> None:
    clause = next(
        item
        for item in read_coverage(USER_BRIEF)["clauses"]
        if item["text"] == "DUB ディレイをところどころにいれる"
    )
    assert clause["read_as"] == ["ディレイ"]


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
        {"label": "ジャンル", "value": "dub_techno", "affects_notes": True},
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


def test_tone_words_are_read_with_their_strength() -> None:
    fields, _spans = read_explicit("かなり短めの音で、こもったダブ。ディレイ多めに")
    assert fields["tone_length"] == -2
    assert fields["tone_brightness"] == -1
    assert fields["tone_delay"] == 1


def test_the_mood_word_dark_is_not_also_a_tone_step() -> None:
    fields, _spans = read_explicit("暗いテクノ")
    assert "tone_brightness" not in fields


def test_an_opposite_tone_word_is_left_unread() -> None:
    coverage = read_coverage("煌びやかなダブ。こもったダブ")
    states = {clause["text"]: clause["state"] for clause in coverage["clauses"]}
    assert states["こもったダブ"] == "unread"


def test_duller_upper_parts_are_out_of_scope_not_a_whole_song_step() -> None:
    coverage = read_coverage("煌びやかなダブ。こもった上物")
    states = {clause["text"]: clause["state"] for clause in coverage["clauses"]}
    assert states["こもった上物"] == "out_of_scope"
    assert read_explicit("こもった上物")[0].get("tone_brightness") is None


def test_coverage_distinguishes_recognized_request_from_arrangement_result() -> None:
    from kihachi_mcp.services.brief_parser import extract_explicit
    from kihachi_mcp.services.studio_interpreter import assemble_brief

    text = "16小節。中間にブレイクを入れて"

    # 1. Parsing/analysis phase (before candidate generation):
    # Rule recognition recognizes mid_break as a request span, but status is unverified.
    analyzed_coverage = read_coverage(text)
    analyzed_states = {c["text"]: c["state"] for c in analyzed_coverage["clauses"]}
    assert analyzed_states["16小節"] == "read"
    assert analyzed_states["中間にブレイクを入れて"] == "unverified"
    assert analyzed_coverage["unread"] == []
    assert analyzed_coverage["unverified"] == ["中間にブレイクを入れて"]
    assert analyzed_coverage["read_fraction"] == 0.5

    # 2. Candidate generation: song is 16 bars, cannot place mid break.
    intent = {
        "genre": "dub_techno",
        "tempo": 120,
        "bars": 16,
        "key": "Dm",
        "mood": "",
        "hats_first_half": "normal",
        "hats_second_half": "normal",
        "bass_register": "mid",
        "note_density": "normal",
        "drop_start_bar": 0,
        "unhandled": [],
        "ambiguous": [],
    }
    brief = assemble_brief(extract_explicit(text), intent)
    assert any("曲が短い構成（16小節）のため" in item for item in brief.unhandled)

    # 3. Post-generation coverage: configuration failure is reflected in coverage.
    result_coverage = read_coverage(brief)
    result_clauses = {c["text"]: c for c in result_coverage["clauses"]}
    assert result_clauses["16小節"]["state"] == "read"
    assert result_clauses["中間にブレイクを入れて"]["state"] == "unread"
    assert result_clauses["中間にブレイクを入れて"]["read_as"] == []
    assert result_clauses["中間にブレイクを入れて"]["unapplied"] == ["中間のBreak構成"]
    assert result_coverage["unread"] == ["中間にブレイクを入れて"]
    assert result_coverage["read_fraction"] == 0.5

    # Passing via brief= keyword argument produces identical result (used on reloading candidate).
    kw_coverage = read_coverage(text, brief=brief)
    assert kw_coverage["unread"] == ["中間にブレイクを入れて"]
    assert kw_coverage["read_fraction"] == 0.5


def test_coverage_for_successful_placement_and_declined() -> None:
    from kihachi_mcp.services.brief_parser import extract_explicit
    from kihachi_mcp.services.studio_interpreter import assemble_brief

    # Successful placement: 48 bars, drop=33 -> inserts mid break at 25-28
    text48 = "48小節、33小節目からドロップ。中間にブレイクを入れて"
    intent48 = {
        "genre": "dub_techno",
        "tempo": 120,
        "bars": 48,
        "key": "Dm",
        "mood": "",
        "hats_first_half": "normal",
        "hats_second_half": "normal",
        "bass_register": "mid",
        "note_density": "normal",
        "drop_start_bar": 33,
        "unhandled": [],
        "ambiguous": [],
    }
    brief48 = assemble_brief(extract_explicit(text48), intent48)
    cov48 = read_coverage(brief48)
    clauses48 = {c["text"]: c for c in cov48["clauses"]}
    assert clauses48["中間にブレイクを入れて"]["state"] == "read"
    assert clauses48["中間にブレイクを入れて"]["read_as"] == ["中間のBreak構成"]
    assert cov48["unread"] == []

    # Declined: 144 bars, mid break declined -> declined state
    text_dec = "144小節のダブテクノ。中間にブレイクを入れない"
    intent144 = {
        "genre": "dub_techno",
        "tempo": 120,
        "bars": 144,
        "key": "Dm",
        "mood": "",
        "hats_first_half": "normal",
        "hats_second_half": "normal",
        "bass_register": "mid",
        "note_density": "normal",
        "drop_start_bar": 0,
        "unhandled": [],
        "ambiguous": [],
    }
    brief_dec = assemble_brief(extract_explicit(text_dec), intent144)
    cov_dec = read_coverage(brief_dec)
    clauses_dec = {c["text"]: c for c in cov_dec["clauses"]}
    assert clauses_dec["中間にブレイクを入れない"]["state"] == "declined"
    assert "中間にブレイクを入れない（否定として認識・既定構成のBreakは維持）" in clauses_dec["中間にブレイクを入れない"]["read_as"]


def test_unrelated_unhandled_messages_do_not_cancel_successful_break_placement() -> None:
    from dataclasses import replace

    from kihachi_mcp.services.brief_parser import extract_explicit
    from kihachi_mcp.services.studio_interpreter import assemble_brief

    # 144 bars dub techno with mid break placed at 73-90 bars.
    text = "144小節のダブテクノ。中間にブレイクを入れて"
    intent = {
        "genre": "dub_techno",
        "tempo": 120,
        "bars": 144,
        "key": "Dm",
        "mood": "",
        "hats_first_half": "normal",
        "hats_second_half": "normal",
        "bass_register": "mid",
        "note_density": "normal",
        "drop_start_bar": 0,
        "unhandled": [],
        "ambiguous": [],
    }
    brief = assemble_brief(extract_explicit(text), intent)
    assert any(s.name == "Break" and s.start_bar == 73 for s in brief.sections)

    # Inject an unrelated unhandled message containing "ブレイク"
    brief_with_unrelated = replace(
        brief,
        unhandled=brief.unhandled + ("ブレイクのフィルター操作は未対応です",),
    )
    cov = read_coverage(brief_with_unrelated)
    clauses = {c["text"]: c for c in cov["clauses"]}
    # The break was placed in the arrangement: it must remain "read", NOT turned "unread".
    assert clauses["中間にブレイクを入れて"]["state"] == "read"
    assert clauses["中間にブレイクを入れて"]["read_as"] == ["中間のBreak構成"]
    assert cov["unread"] == []


def test_mixed_clauses_with_success_and_failure_or_unverified_are_partly_read() -> None:
    from kihachi_mcp.services.brief_parser import extract_explicit
    from kihachi_mcp.services.studio_interpreter import assemble_brief

    # 1. Prior to generation: bars is recognized, mid_break is unverified.
    # The clause contains both applied and unverified -> must be partly_read, not read.
    text48_mixed = "48小節で中間にブレイクを入れて"
    cov_pre = read_coverage(text48_mixed)
    clause_pre = cov_pre["clauses"][0]
    assert clause_pre["state"] == "partly_read"
    assert clause_pre["read_as"] == ["小節数"]
    assert clause_pre["unverified"] == ["中間のBreak構成"]

    # After generation (48 bars, drop=33 -> break placed at 25-28):
    # Both bars and mid_break are applied -> fully read.
    intent48 = {
        "genre": "dub_techno",
        "tempo": 120,
        "bars": 48,
        "key": "Dm",
        "mood": "",
        "hats_first_half": "normal",
        "hats_second_half": "normal",
        "bass_register": "mid",
        "note_density": "normal",
        "drop_start_bar": 33,
        "unhandled": [],
        "ambiguous": [],
    }
    brief48 = assemble_brief(extract_explicit(text48_mixed), intent48)
    cov_post = read_coverage(brief48)
    clause_post = cov_post["clauses"][0]
    assert clause_post["state"] == "read"
    assert clause_post["read_as"] == ["中間のBreak構成", "小節数"]

    # 2. Failed placement in a single mixed sentence:
    # 16 bars with mid break. bars succeeds, mid_break fails.
    # The sentence must NOT be displayed as fully reflected ("read") -> partly_read.
    text16_mixed = "16小節で中間にブレイクを入れて"
    intent16 = {
        "genre": "dub_techno",
        "tempo": 120,
        "bars": 16,
        "key": "Dm",
        "mood": "",
        "hats_first_half": "normal",
        "hats_second_half": "normal",
        "bass_register": "mid",
        "note_density": "normal",
        "drop_start_bar": 0,
        "unhandled": [],
        "ambiguous": [],
    }
    brief16 = assemble_brief(extract_explicit(text16_mixed), intent16)
    cov16 = read_coverage(brief16)
    clause16 = cov16["clauses"][0]
    assert clause16["state"] == "partly_read"
    assert clause16["read_as"] == ["小節数"]
    assert clause16["unapplied"] == ["中間のBreak構成"]


def test_studio_html_renders_unverified_coverage_state() -> None:
    import subprocess
    from pathlib import Path

    html_path = (
        Path(__file__).resolve().parents[1]
        / "src/kihachi_mcp/studio/static/index.html"
    )
    script = f"""
    const assert = require("node:assert/strict");
    const fs = require("node:fs");
    const vm = require("node:vm");
    const html = fs.readFileSync({str(html_path)!r}, "utf8");
    const start = html.indexOf("    const COVERAGE_STATES =");
    const end = html.indexOf("    function renderCandidate(");
    const nodes = {{ "coverage-line": {{ textContent: "" }}, "coverage": {{ innerHTML: "" }} }};
    const ctx = vm.createContext({{ $: (id) => nodes[id] }});
    vm.runInContext(html.slice(start, end), ctx);

    // 1. Unverified clause test
    ctx.renderCoverageList({{
      clauses: [
        {{
          text: "中間にブレイクを入れて",
          state: "unverified",
          read_as: [],
          unverified: ["中間のBreak構成"],
          unapplied: [],
          out_of_scope: []
        }}
      ]
    }});
    assert.match(nodes["coverage"].innerHTML, /<span class="warn">認識済み・適用未確認<\\/span> 中間にブレイクを入れて/);
    assert.match(nodes["coverage"].innerHTML, /中間のBreak構成（適用未確認）/);
    assert.equal(nodes["coverage-line"].textContent, "1 文のうち 1 文は認識済みですが、構成への適用は未確認です");

    // 2. Mixed clause test with unapplied
    ctx.renderCoverageList({{
      clauses: [
        {{
          text: "16小節で中間にブレイクを入れて",
          state: "partly_read",
          read_as: ["小節数"],
          unverified: [],
          unapplied: ["中間のBreak構成"],
          out_of_scope: []
        }}
      ]
    }});
    assert.match(nodes["coverage"].innerHTML, /<span class="warn">一部のみ<\\/span> 16小節で中間にブレイクを入れて/);
    assert.match(nodes["coverage"].innerHTML, /小節数、中間のBreak構成（未反映）/);
    """
    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
