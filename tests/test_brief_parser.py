import pytest

from kihachi_mcp.services.brief_parser import extract_explicit
from kihachi_mcp.services.studio_interpreter import (
    InterpretationError,
    assemble_brief,
    validate_model_intent,
)

BRIEF = (
    "125 BPM、Dマイナー、96小節の暗いテクノ。"
    "前半はハットを少なく、後半で増やす。"
    "57小節目からドロップにして"
)


def test_extracts_explicit_tempo_key_bars_drop_and_hats() -> None:
    extracted = extract_explicit(BRIEF)
    assert extracted["fields"]["tempo"] == 125
    assert extracted["fields"]["key"] == "Dm"
    assert extracted["fields"]["bars"] == 96
    assert extracted["fields"]["drop_start_bar"] == 57
    assert extracted["fields"]["hats_first_half"] == "sparse"
    assert extracted["fields"]["hats_second_half"] == "dense"
    assert extracted["fields"]["mood"] == "暗い"


@pytest.mark.parametrize("spoken", ["ビーピーエム125", "125ビーピーエム", "BPM 125"])
def test_spoken_bpm_is_preserved_as_an_explicit_value(spoken: str) -> None:
    extracted = extract_explicit(f"Mutashon Funkで、{spoken}")
    assert extracted["fields"]["genre"] == "mutation_funk"
    assert extracted["fields"]["tempo"] == 125


def test_user_values_win_over_conflicting_model_output() -> None:
    extracted = extract_explicit(BRIEF)
    intent = {
        "genre": "melodic_techno",
        "tempo": 128,
        "bars": 64,
        "key": "Am",
        "mood": "明るい",
        "hats_first_half": "dense",
        "hats_second_half": "sparse",
        "bass_register": "high",
        "note_density": "dense",
        "drop_start_bar": 17,
        "unhandled": [],
        "ambiguous": [],
    }
    brief = assemble_brief(extracted, intent)
    assert brief.tempo.value == 125
    assert brief.tempo.source == "user"
    assert brief.key.value == "Dm"
    assert brief.bars.value == 96
    assert brief.drop_start_bar.value == 57
    assert any(section.name == "Drop" and section.start_bar == 57 for section in brief.sections)
    assert any("125" in item or "tempo" in item for item in brief.contradictions)


def test_unhandled_vocal_request_is_kept() -> None:
    extracted = extract_explicit(BRIEF + "。ボーカルも入れて")
    intent = _valid_intent()
    brief = assemble_brief(extracted, intent)
    assert any("ボーカル" in item for item in brief.unhandled)


def test_unknown_model_field_is_rejected() -> None:
    intent = _valid_intent()
    intent["command"] = "delete_tracks"
    with pytest.raises(InterpretationError, match="フィールド"):
        validate_model_intent(intent)


def _valid_intent() -> dict:
    return {
        "genre": "tech_house",
        "tempo": 125,
        "bars": 96,
        "key": "Dm",
        "mood": "暗い",
        "hats_first_half": "sparse",
        "hats_second_half": "dense",
        "bass_register": "low",
        "note_density": "normal",
        "drop_start_bar": 57,
        "unhandled": [],
        "ambiguous": [],
    }


def test_letters_inside_english_words_are_not_keys() -> None:
    """The C of TECHNO once turned a D minor brief into C minor."""
    assert extract_explicit("Dマイナーの重いDUB TECHNO")["fields"]["key"] == "Dm"
    assert extract_explicit("CのACID DISCO")["fields"]["key"] == "C"
    assert "key" not in extract_explicit("BPM 125 のハードテクノ")["fields"]


def test_full_width_and_music_sign_sharps_are_read() -> None:
    brief = "105 BPM、D＃マイナー、144小節で5分程度の重いDUB TECHNO。"
    assert extract_explicit(brief)["fields"]["key"] == "D#m"
    assert extract_explicit("Ｄ＃マイナー")["fields"]["key"] == "D#m"
    assert extract_explicit("D♯マイナー")["fields"]["key"] == "D#m"
    assert extract_explicit("E♭マイナー")["fields"]["key"] == "D#m"
