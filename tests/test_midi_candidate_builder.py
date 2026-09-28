import hashlib

from kihachi_mcp.models.production_brief import (
    ARRANGEMENT_PARTS,
    DRUM_PARTS,
    PART_ORDER,
)
from kihachi_mcp.services.brief_parser import extract_explicit
from kihachi_mcp.services.drum_samples import (
    IMPACT_NOTE,
    RISER_NOTE,
    RISER_SECONDS,
    sample_path_for_note,
)
from kihachi_mcp.services.midi_candidate_builder import (
    CLAP_PITCH,
    HAT_PITCH,
    KICK_PITCH,
    OPEN_HAT_PITCH,
    SAFE_UDP_REQUEST_BYTES,
    SNARE_PITCHES,
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
    hats = _bars(build_candidate(_brief(), seed=5), "Snare", {CLAP_PITCH})
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
    chords = {tuple(sorted({pitch for _offset, pitch in stab[bar]})) for bar in range(57, 65)}
    assert len(chords) >= 2
    bass = _bars(candidate, "Bass")
    lowest = {min(pitch for _offset, pitch in bass[bar]) for bar in range(57, 65)}
    assert len(lowest) >= 2


def test_bass_and_stab_follow_the_same_drop_harmony() -> None:
    candidate = build_candidate(_brief(), seed=5)
    bass = _bars(candidate, "Bass")
    stab = _bars(candidate, "Stab")
    # At least one bass note in every Drop bar must be a tone of its chord.
    for bar in range(57, 73):
        chord_tones = {pitch % 12 for _offset, pitch in stab[bar]}
        assert any(pitch % 12 in chord_tones for _offset, pitch in bass[bar]), bar


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
    assert set(candidate.used_pitches("Hats")) == {HAT_PITCH}
    assert set(candidate.used_pitches("OpenHat")) == {OPEN_HAT_PITCH}
    assert CLAP_PITCH in candidate.used_pitches("Snare")
    for part in DRUM_PARTS:
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


def test_every_arrangement_part_is_written_in_track_order() -> None:
    candidate = build_candidate(_brief(), seed=5)
    assert candidate.parts == tuple(part for part in PART_ORDER if part != "Lead")
    assert "Lead" not in candidate.parts  # still mutation_funk only


def test_guitar_cuts_sixteenths_and_the_horn_stabs_at_the_peaks() -> None:
    candidate = build_candidate(_brief(), seed=5)
    beats = candidate.brief.beats_per_bar
    guitar = _part_notes(candidate, "Guitar")
    horn = _part_notes(candidate, "Horn")
    assert guitar and horn
    assert all(round(beat * 4, 6) == round(beat * 4) for beat, *_ in guitar)
    assert {velocity < 60 for *_, velocity in guitar} == {True, False}, "muted and open"
    drop = range(57, 97)
    assert all(_bar_of(beat, beats) in drop for beat, *_ in horn)
    d_minor = {2, 4, 5, 7, 9, 10, 0}
    for notes in (guitar, horn):
        assert {pitch % 12 for _beat, pitch, _velocity in notes} <= d_minor


def _digest(candidate, parts) -> str:
    beats = candidate.brief.beats_per_bar
    notes = sorted(
        (
            round((clip.start_bar - 1) * beats + note.start_beats, 6),
            note.pitch,
            note.duration_beats,
            note.velocity,
        )
        for part in parts
        for clip in candidate.clips_for_part(part)
        for note in clip.notes
    )
    return hashlib.sha256(repr(notes).encode()).hexdigest()[:16]


def test_new_parts_leave_the_original_four_note_for_note() -> None:
    """Digests taken before the new parts existed, when Hats held the clap."""
    candidate = build_candidate(_brief(), seed=5)
    assert _digest(candidate, ["Kick"]) == "c90869c03358bf75"
    assert _digest(candidate, ["Hats", "Snare", "OpenHat"]) == "f55e567fcd364dd1"
    assert _digest(candidate, ["Bass"]) == "fff5ca50a2e51fb5"
    assert _digest(candidate, ["Stab"]) == "592b603ad2396ede"
    snare = {pitch for _beat, pitch, _velocity in _part_notes(candidate, "Snare")}
    assert snare <= SNARE_PITCHES


def test_sparse_parts_send_no_empty_clips() -> None:
    candidate = build_candidate(_brief(), seed=5)
    for clip in candidate.clips:
        if clip.part in ARRANGEMENT_PARTS:
            assert clip.notes, (clip.part, clip.section_name, clip.start_bar)


def test_the_sub_follows_the_bass_an_octave_down_and_leaves_the_breakdown() -> None:
    candidate = build_candidate(_brief(), seed=5)
    beats = candidate.brief.beats_per_bar
    sub = _part_notes(candidate, "Sub")
    bass_beats = {beat for beat, _pitch, _velocity in _part_notes(candidate, "Bass")}
    assert sub
    assert all(24 <= pitch <= 35 for _beat, pitch, _velocity in sub)
    assert {beat for beat, _pitch, _velocity in sub} <= bass_beats
    assert all(_bar_of(beat, beats) not in range(49, 57) for beat, *_ in sub)


def test_the_pad_holds_four_note_chords_inside_each_bar() -> None:
    candidate = build_candidate(_brief(), seed=5)
    beats = candidate.brief.beats_per_bar
    by_bar: dict[int, set[int]] = {}
    for beat, pitch, _velocity in _part_notes(candidate, "Pad"):
        assert beat % beats == 0
        by_bar.setdefault(_bar_of(beat, beats), set()).add(pitch)
    assert by_bar and all(len(pitches) == 4 for pitches in by_bar.values())
    assert all(max(p) - min(p) <= 18 for p in by_bar.values())


def test_the_riser_ends_on_the_drop_and_the_impact_lands_on_it() -> None:
    candidate = build_candidate(_brief(), seed=5)
    beats = candidate.brief.beats_per_bar
    drop_beat = 56 * beats
    fx = _part_notes(candidate, "FX")
    impacts = [beat for beat, pitch, _velocity in fx if pitch == IMPACT_NOTE]
    risers = [beat for beat, pitch, _velocity in fx if pitch == RISER_NOTE]
    assert drop_beat in impacts
    riser_beats = RISER_SECONDS * 125 / 60
    assert any(abs(drop_beat - beat - riser_beats) <= 0.25 for beat in risers)


def test_arp_and_vocal_play_chord_tones_at_the_peaks_only() -> None:
    candidate = build_candidate(_brief(), seed=5)
    beats = candidate.brief.beats_per_bar
    intro = range(1, 17)
    for part in ("Arp", "Vocal"):
        notes = _part_notes(candidate, part)
        assert notes, part
        assert all(_bar_of(beat, beats) not in intro for beat, *_ in notes)
        pitch_classes = {pitch % 12 for _beat, pitch, _velocity in notes}
        d_minor = {2, 4, 5, 7, 9, 10, 0}
        assert pitch_classes <= d_minor

