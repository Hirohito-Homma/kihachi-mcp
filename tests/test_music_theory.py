import pytest

from kihachi_mcp.knowledge.music_theory import (
    HARMONY_STYLES,
    MODES,
    diatonic,
    fit_to_chord,
    mode_from_text,
    parse_numeral,
)

MAJOR = MODES["ionian"]
MINOR = MODES["aeolian"]


def test_modes_are_seven_notes_from_the_tonic() -> None:
    for name, scale in MODES.items():
        assert len(scale) == 7 and scale[0] == 0, name
        assert list(scale) == sorted(set(scale)), name


def test_a_brief_names_its_mode_in_japanese_or_english() -> None:
    assert mode_from_text("Dドリアンで") == "dorian"
    assert mode_from_text("mixolydian feel") == "mixolydian"
    assert mode_from_text("フリジアンドミナントの妖しさ") == "phrygian_dominant"
    assert mode_from_text("暗いテクノ") is None


def test_diatonic_sevenths_in_major() -> None:
    assert diatonic(MAJOR, 0).tones == (0, 4, 7, 11)  # Imaj7
    assert diatonic(MAJOR, 1).tones == (0, 3, 7, 10)  # ii7
    assert diatonic(MAJOR, 4).tones == (0, 4, 7, 10)  # V7
    assert diatonic(MAJOR, 6).tones == (0, 3, 6, 10)  # viiø


@pytest.mark.parametrize(
    ("symbol", "root", "tones"),
    [
        ("I", 0, (0, 4, 7)),
        ("vi7", 9, (0, 3, 7, 10)),
        ("IVmaj7", 5, (0, 4, 7, 11)),
        ("bVI", 8, (0, 4, 7)),
        ("bVII7", 10, (0, 4, 7, 10)),
        ("iiø", 2, (0, 3, 6, 10)),
        ("vii°7", 11, (0, 3, 6, 9)),
        ("V7sus4", 7, (0, 5, 7, 10)),
        ("i9", 0, (0, 3, 7, 10, 14)),
        ("III7", 4, (0, 4, 7, 10)),
        ("V7/vi", 4, (0, 4, 7, 10)),  # the dominant of A minor is E7
        ("V7/V", 2, (0, 4, 7, 10)),
    ],
)
def test_roman_numerals(symbol: str, root: int, tones: tuple[int, ...]) -> None:
    chord = parse_numeral(symbol, MAJOR)
    assert chord.root == root
    assert chord.tones == tones


def test_a_borrowed_chord_steps_from_the_degree_below_it() -> None:
    assert parse_numeral("bVI", MAJOR).degree == 4  # G, below A-flat
    assert parse_numeral("bVI", MINOR).degree == 5


def test_a_ninth_chord_voices_its_ninth_instead_of_the_fifth() -> None:
    assert parse_numeral("i9", MINOR).four_voices() == (0, 3, 10, 14)


def test_every_style_parses_in_both_modes() -> None:
    for style, rows in HARMONY_STYLES.items():
        for quality in ("minor", "major"):
            assert rows[quality], (style, quality)
            for row in rows[quality]:
                assert len(row) in {2, 4}, (style, row)
                for symbol in row:
                    parse_numeral(symbol, MINOR if quality == "minor" else MAJOR)


def test_marusa_has_its_two_secondary_dominants() -> None:
    _iv, iii7, _vi7, i7 = (
        parse_numeral(s, MAJOR) for s in HARMONY_STYLES["j_pop"]["major"][1]
    )
    assert iii7.pitch_classes() == {4, 8, 11, 2}  # E7 in C: the G-sharp leads to A
    assert i7.pitch_classes() == {0, 4, 7, 10}  # C7: the B-flat leads to F


def test_fit_to_chord_moves_a_clash_onto_the_chord() -> None:
    e7 = parse_numeral("III7", MAJOR)
    tonic = 60
    assert fit_to_chord(tonic + 7, tonic, e7, MAJOR) == tonic + 8  # G clashes with G-sharp
    assert fit_to_chord(tonic + 4, tonic, e7, MAJOR) == tonic + 4
    a_minor = diatonic(MAJOR, 5)
    assert fit_to_chord(tonic + 11, tonic, a_minor, MAJOR) == tonic + 11  # B stays
