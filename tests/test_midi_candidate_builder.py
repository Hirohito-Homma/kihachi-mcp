from kihachi_mcp.services.brief_parser import extract_explicit
from kihachi_mcp.services.drum_samples import sample_path_for_note
from kihachi_mcp.services.midi_candidate_builder import (
    CLAP_PITCH,
    HAT_PITCH,
    KICK_PITCH,
    OPEN_HAT_PITCH,
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


def _part_notes(candidate, part):
    """Every note of a part as (absolute beat, pitch, velocity), song order."""
    beats = candidate.brief.beats_per_bar
    return sorted(
        (round((clip.start_bar - 1) * beats + note.start_beats, 6), note.pitch, note.velocity)
        for clip in candidate.clips_for_part(part)
        for note in clip.notes
    )


def _bar_of(beat: float, beats: float = 4.0) -> int:
    return int(beat // beats) + 1


def _bars(candidate, part, pitches=None):
    """Map bar number -> tuple of (offset in bar, pitch) for one part."""
    beats = candidate.brief.beats_per_bar
    bars: dict[int, list[tuple[float, int]]] = {}
    for beat, pitch, _velocity in _part_notes(candidate, part):
        if pitches is not None and pitch not in pitches:
            continue
        bars.setdefault(_bar_of(beat, beats), []).append((round(beat % beats, 6), pitch))
    return {bar: tuple(notes) for bar, notes in bars.items()}


def test_the_kick_drops_out_for_a_breakdown_before_the_drop() -> None:
    kick = _bars(build_candidate(_brief(), seed=5), "Kick")
    assert all(bar not in kick for bar in range(49, 57))
    assert (0.0, KICK_PITCH) in kick[57]
    assert 48 in kick


def test_the_drop_has_a_clap_on_two_and_four() -> None:
    hats = _bars(build_candidate(_brief(), seed=5), "Hats", {CLAP_PITCH})
    for bar in range(57, 81):
        offsets = {offset for offset, _pitch in hats[bar]}
        assert {1.0, 3.0} <= offsets


def test_the_intro_starts_with_the_kick_alone() -> None:
    candidate = build_candidate(_brief(), seed=5)
    hats = _bars(candidate, "Hats")
    bass = _bars(candidate, "Bass")
    stab = _bars(candidate, "Stab")
    assert all(bar not in hats and bar not in bass and bar not in stab for bar in range(1, 9))
    assert any(bar in hats for bar in range(9, 17))


def test_phrase_ends_are_marked_by_a_fill() -> None:
    kick = _bars(build_candidate(_brief(), seed=5), "Kick")
    # Bar 72 closes the Drop's first 16-bar phrase; bar 71 is an ordinary bar.
    assert kick[72] != kick[71]
    hats = _bars(build_candidate(_brief(), seed=5), "Hats", {HAT_PITCH})
    assert hats[64] != hats[63]


def test_the_drop_moves_through_a_chord_progression() -> None:
    candidate = build_candidate(_brief(), seed=5)
    stab = _bars(candidate, "Stab")
    chords = {tuple(sorted({pitch for _offset, pitch in stab[bar]})) for bar in range(57, 61)}
    assert len(chords) >= 2
    bass = _bars(candidate, "Bass")
    lowest = {min(pitch for _offset, pitch in bass[bar]) for bar in range(57, 61)}
    assert len(lowest) >= 2


def test_the_drop_is_not_one_bar_repeated() -> None:
    candidate = build_candidate(_brief(), seed=5)
    for part in ("Hats", "Bass", "Stab"):
        bars = _bars(candidate, part)
        distinct = {bars[bar] for bar in range(57, 81) if bar in bars}
        assert len(distinct) >= 3, part


def test_a_different_seed_changes_the_notes_not_just_the_ids() -> None:
    first = build_candidate(_brief(), seed=1)
    second = build_candidate(_brief(), seed=2)
    differing = [
        part
        for part in ("Kick", "Hats", "Bass", "Stab")
        if _part_notes(first, part) != _part_notes(second, part)
    ]
    assert len(differing) >= 3


def test_every_drum_pitch_has_a_bundled_sample_and_velocities_are_valid() -> None:
    candidate = build_candidate(_brief(), seed=209419474)
    assert set(candidate.used_pitches("Hats")) <= {CLAP_PITCH, HAT_PITCH, OPEN_HAT_PITCH}
    for part in ("Kick", "Hats"):
        for pitch in candidate.used_pitches(part):
            assert sample_path_for_note(pitch).is_file()
    for clip in candidate.clips:
        beats = clip.length_bars * candidate.brief.beats_per_bar
        for note in clip.notes:
            assert 1 <= note.velocity <= 127
            assert 0 <= note.start_beats < beats
            assert note.start_beats + note.duration_beats <= beats + 1e-6


def test_the_bass_stays_in_its_register() -> None:
    candidate = build_candidate(_brief(), seed=5)
    pitches = candidate.used_pitches("Bass")
    assert max(pitches) - min(pitches) <= 24
    assert min(pitches) >= 12
