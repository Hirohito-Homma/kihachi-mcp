from kihachi_mcp.knowledge.genre_database import (
    find,
    load_database,
    match_genres,
    typical_bpm,
)
from kihachi_mcp.models.production_brief import SUPPORTED_GENRES
from kihachi_mcp.services.brief_parser import extract_explicit
from kihachi_mcp.services.studio_interpreter import assemble_brief


def _slugs(text: str) -> list[str]:
    return [match.genre.slug for match in match_genres(text)]


def _intent(**overrides):
    intent = {
        "genre": "tech_house",
        "tempo": 125,
        "bars": 96,
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
    intent.update(overrides)
    return intent


def test_the_whole_database_is_shipped() -> None:
    assert len(load_database()) == 1020


def test_the_models_three_genres_are_database_rows() -> None:
    for slug in SUPPORTED_GENRES:
        assert find(slug) is not None, slug


def test_english_and_japanese_names_are_recognised() -> None:
    assert _slugs("重いDUB TECHNO") == ["dub_techno"]
    assert _slugs("ダブテクノ") == ["dub_techno"]
    assert _slugs("テックハウスとダブ") == ["tech_house", "dub"]


def test_user_confirmed_mutashon_spelling_means_mutation_funk() -> None:
    assert _slugs("Mutashon Funkで作って") == ["mutation_funk"]
    assert _slugs("Mutation Funkで作って") == ["mutation_funk"]
    brief = assemble_brief(extract_explicit("Mutashon Funkで64小節"), _intent())
    assert brief.genre.value == "mutation_funk"
    assert brief.genre.source == "user"


def test_a_longer_name_wins_over_the_one_inside_it() -> None:
    assert _slugs("ハードテクノ") == ["hard_techno"]


def test_a_word_inside_another_word_is_not_a_genre() -> None:
    assert _slugs("ファンキーなスラップベース") == []


def test_a_bare_number_is_not_a_genre() -> None:
    """Deep Dubstep carries the alias "140"; a tempo is not a genre request."""
    assert _slugs("ハードテクノ 140") == ["hard_techno"]


def test_a_genre_word_describing_an_effect_is_not_a_genre() -> None:
    assert _slugs("DUB ディレイをところどころに") == []
    assert _slugs("ダブ風のディレイ") == []


def test_a_named_genre_is_the_users_choice() -> None:
    extracted = extract_explicit("105 BPM、D＃マイナー、144小節の重いDUB TECHNO")
    brief = assemble_brief(extracted, _intent())
    assert brief.genre.value == "dub_techno"
    assert brief.genre.source == "user"
    assert not any("genre" in item for item in brief.contradictions)


def test_a_genre_the_model_cannot_answer_is_still_valid() -> None:
    brief = assemble_brief(extract_explicit("ボサノバ、96小節"), _intent())
    assert brief.genre.value == "bossa_nova"


def test_the_genre_sets_the_tempo_only_when_nobody_else_did() -> None:
    brief = assemble_brief(extract_explicit("ダブテクノ、96小節"), _intent())
    assert brief.tempo.value == typical_bpm(["dub_techno"]) == 120
    assert brief.tempo.source == "genre"
    stated = assemble_brief(extract_explicit("ダブテクノ、118 BPM"), _intent())
    assert stated.tempo.value == 118


def test_a_stated_tempo_outside_the_genre_is_kept_and_mentioned() -> None:
    brief = assemble_brief(extract_explicit("105 BPM のダブテクノ"), _intent())
    assert brief.tempo.value == 105
    assert any("より遅め" in note for note in brief.interpretations)


def test_a_wide_database_range_is_not_used_as_a_tempo() -> None:
    brief = assemble_brief(extract_explicit("ボサノバ、96小節"), _intent())
    assert brief.tempo.source == "default"
