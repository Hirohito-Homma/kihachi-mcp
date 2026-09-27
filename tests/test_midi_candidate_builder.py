from kihachi_mcp.services.brief_parser import extract_explicit
from kihachi_mcp.services.midi_candidate_builder import (
    KICK_PITCH,
    SAFE_UDP_REQUEST_BYTES,
    build_candidate,
    estimate_replace_request_bytes,
    hat_counts_by_half,
)
from kihachi_mcp.services.studio_interpreter import assemble_brief

BRIEF = (
    "125 BPM、Dマイナー、96小節の暗いテクノ。"
    "前半はハットを少なく、後半で増やす。"
    "57小節目からドロップにして"
)


def _brief():
    return assemble_brief(
        extract_explicit(BRIEF),
        {
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
        },
    )


def test_candidate_keeps_explicit_values_and_drop() -> None:
    candidate = build_candidate(_brief(), seed=1)
    assert candidate.brief.tempo.value == 125
    assert candidate.brief.key.value == "Dm"
    assert candidate.brief.bars.value == 96
    assert candidate.brief.duration_minutes == 96 * 4 / 125
    drop = next(section for section in candidate.brief.sections if section.name == "Drop")
    assert drop.start_bar == 57
    assert any("低めの音域" in item for item in candidate.brief.interpretations)


def test_hats_are_sparser_in_the_first_half() -> None:
    first, second = hat_counts_by_half(build_candidate(_brief(), seed=1))
    assert first < second


def test_kick_uses_only_pitch_36() -> None:
    candidate = build_candidate(_brief(), seed=1)
    assert candidate.used_pitches("Kick") == (KICK_PITCH,)


def test_same_seed_reproduces_the_same_notes() -> None:
    first = build_candidate(_brief(), seed=7, candidate_id="a")
    second = build_candidate(_brief(), seed=7, candidate_id="b")
    assert first.note_fingerprint != second.note_fingerprint
    assert [clip.to_dict()["notes"] for clip in first.clips] == [
        clip.to_dict()["notes"] for clip in second.clips
    ]


def test_dense_clips_stay_under_macos_udp_budget() -> None:
    candidate = build_candidate(_brief(), seed=209419474)
    hats = candidate.clips_for_part("Hats")
    assert any(clip.length_bars < 16 for clip in hats)
    for clip in candidate.clips:
        assert estimate_replace_request_bytes(clip.notes) <= SAFE_UDP_REQUEST_BYTES
    assert candidate.note_count("Hats") == 864


def test_regenerate_seed_changes_stab_choice_but_keeps_structure() -> None:
    first = build_candidate(_brief(), seed=1, candidate_id="one")
    second = build_candidate(
        _brief(), seed=2, candidate_id="two", parent_candidate_id="one"
    )
    assert first.candidate_id != second.candidate_id
    assert second.parent_candidate_id == "one"
    assert [clip.section_name for clip in first.clips] == [
        clip.section_name for clip in second.clips
    ]
