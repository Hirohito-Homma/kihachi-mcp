import hashlib
from itertools import pairwise

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
    _rising_activity_steps,
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


#: Where the Drops of BRIEF fall, around the Break at 73-76.
DROP_BARS = (*range(57, 73), *range(77, 93))


def _brief(text: str = BRIEF):
    return assemble_brief(
        extract_explicit(text),
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


def _notes_by_bar(candidate, part):
    """Absolute (bar, pitch, start, duration, velocity) per bar, for one part."""
    beats = candidate.brief.beats_per_bar
    bars: dict[int, list[tuple]] = {}
    for clip in candidate.clips_for_part(part):
        for note in clip.notes:
            start = round((clip.start_bar - 1) * beats + note.start_beats, 6)
            bar = int(start // beats) + 1
            bars.setdefault(bar, []).append(
                (note.pitch, start, note.duration_beats, note.velocity)
            )
    return bars


def _section_bars(candidate, name):
    return {
        bar
        for section in candidate.brief.sections
        if section.name == name
        for bar in range(section.start_bar, section.end_bar + 1)
    }


#: The same brief and seed with and without one request: only the request differs.
def _pair(extra: str, seed: int = 11):
    plain = build_candidate(_brief(), seed=seed, candidate_id="same")
    asked = build_candidate(_brief(BRIEF + "。" + extra), seed=seed, candidate_id="same")
    return plain, asked


def test_progressive_bass_steps_restore_the_pattern_over_song_time() -> None:
    source = list(range(16))
    early = _rising_activity_steps(source, 0.0)
    middle = _rising_activity_steps(source, 0.5)
    late = _rising_activity_steps(source, 1.0)
    assert len(early) < len(middle) < len(late) == len(source)
    assert early[0] == 0 and early[-1] == 15


def test_progressive_bass_thins_early_bars_and_keeps_break_and_outro() -> None:
    plain, asked = _pair("BASSは最初動き少なく徐々に動きのあるものへ変化する")
    before, after = _notes_by_bar(plain, "Bass"), _notes_by_bar(asked, "Bass")
    early = range(9, 41)  # the second half of the Intro and the VerseA
    assert sum(len(after.get(bar, [])) for bar in early) < sum(
        len(before.get(bar, [])) for bar in early
    )
    # A thinned bar plays some of the same notes, never other ones, so the
    # late Drop is back to the pattern the plain song plays.
    for bar, notes in after.items():
        assert set(notes) <= set(before[bar])
    assert all(after.get(bar) == before.get(bar) for bar in range(77, 93))
    # The Break holds its two long notes and the Outro ends silent, as before.
    for bar in _section_bars(plain, "Break") | _section_bars(plain, "Outro"):
        assert after.get(bar) == before.get(bar)
    for part in ("Kick", "Hats", "Stab"):
        assert asked.clips_for_part(part) == plain.clips_for_part(part)


def test_gradual_upper_request_moves_only_upper_parts_and_only_later() -> None:
    plain, asked = _pair("上物は徐々に煌びやかに")
    assert asked.brief.tone_brightness.value == 0
    for part in ("Kick", "Snare", "Hats", "OpenHat", "Bass", "Sub", "FX"):
        assert asked.clips_for_part(part) == plain.clips_for_part(part)
    before, after = _notes_by_bar(plain, "Stab"), _notes_by_bar(asked, "Stab")
    first_third = [bar for bar in before if (bar - 1) / 95 < 1 / 3]
    assert first_third and all(after[bar] == before[bar] for bar in first_third)
    for bar in _section_bars(plain, "Break") | _section_bars(plain, "Outro"):
        assert after.get(bar) == before.get(bar)
    late_drop = [bar for bar in range(77, 93) if bar in before]
    assert late_drop
    for bar in late_drop:
        top = max(pitch for pitch, *_rest in before[bar])
        assert max(pitch for pitch, *_rest in after[bar]) == top + 12
        assert len(after[bar]) > len(before[bar])
        written = {(pitch, start): velocity for pitch, start, _d, velocity in before[bar]}
        for pitch, start, _duration, velocity in after[bar]:
            if (pitch, start) in written:
                assert velocity == min(127, written[(pitch, start)] + 5)
    assert any("音色（フィルター等）の時間変化は未実装" in item for item in asked.brief.interpretations)


def test_flashy_drop_changes_only_drop_bars_of_upper_parts() -> None:
    plain, asked = _pair("ドロップは派手に")
    assert asked.brief.tone_brightness.value == 0
    for part in ("Kick", "Snare", "Hats", "OpenHat", "Bass", "Sub", "FX"):
        assert asked.clips_for_part(part) == plain.clips_for_part(part)
    drop = set(DROP_BARS)
    for part in ("Stab", "Pad"):
        before, after = _notes_by_bar(plain, part), _notes_by_bar(asked, part)
        assert all(after.get(bar) == before.get(bar) for bar in set(before) - drop)
        changed = [bar for bar in drop if after.get(bar) != before.get(bar)]
        assert changed and all(len(after[bar]) > len(before[bar]) for bar in changed)
    assert any("Drop 区間の上物だけ" in item for item in asked.brief.interpretations)


def test_a_song_without_drops_does_not_take_a_chorus_for_one() -> None:
    from kihachi_mcp.services.midi_candidate_builder import (
        flash_sections,
        scoped_request_notes,
    )
    from kihachi_mcp.services.studio_interpreter import build_sections

    sections = build_sections(64, 0, "j_pop")
    assert flash_sections(sections) == frozenset()
    (note,) = scoped_request_notes(sections, {"drop_emphasis": "flashy"})
    assert "Drop区間がない構成のため、ほかの区間には適用せず" in note

    def song(text):
        intent = {
            "genre": "tech_house", "tempo": 140, "bars": 64, "key": "C", "mood": "",
            "hats_first_half": "normal", "hats_second_half": "normal",
            "bass_register": "mid", "note_density": "normal", "drop_start_bar": 0,
            "unhandled": [], "ambiguous": [],
        }
        brief = assemble_brief(extract_explicit(text), intent)
        return build_candidate(brief, seed=4, candidate_id="same")

    plain = song("J-POP、140 BPM、Cメジャー、64小節")
    asked = song("J-POP、140 BPM、Cメジャー、64小節。ドロップは派手に")
    assert {section.name for section in asked.brief.sections} >= {"ChorusA", "ChorusB"}
    assert asked.clips == plain.clips


def test_steady_upper_request_adds_the_octave_everywhere_but_break_and_outro() -> None:
    plain, asked = _pair("上物は煌びやか")
    assert asked.brief.tone_brightness.value == 0
    for part in ("Kick", "Snare", "Hats", "OpenHat", "Bass", "Sub", "FX"):
        assert asked.clips_for_part(part) == plain.clips_for_part(part)
    before, after = _notes_by_bar(plain, "Stab"), _notes_by_bar(asked, "Stab")
    held_back = _section_bars(plain, "Break") | _section_bars(plain, "Outro")
    for bar, notes in before.items():
        if bar in held_back:
            assert after[bar] == notes
            continue
        # The written notes keep their velocity; only the octave is new.
        assert set(notes) < set(after[bar])
        top = max(pitch for pitch, *_rest in notes)
        assert max(pitch for pitch, *_rest in after[bar]) == top + 12
    # 「キラキラした上物」 says the same thing in the other word order.
    assert _pair("キラキラした上物")[1].clips == asked.clips
    assert any("上物だけに、MIDIの音域で表現" in item for item in asked.brief.interpretations)


def test_refused_upper_and_drop_requests_write_the_plain_song() -> None:
    plain = build_candidate(_brief(), seed=11, candidate_id="same")
    for extra in (
        "上物は煌びやかにしない",
        "煌びやかな上物にしない",
        "派手じゃないドロップ",
        "派手ではないドロップ",
        "ドロップは派手にしない",
        "派手なドロップにしない",
    ):
        asked = build_candidate(_brief(BRIEF + "。" + extra), seed=11, candidate_id="same")
        assert asked.clips == plain.clips, extra
        assert asked.brief.tone_brightness.value == 0, extra


def test_refused_upper_request_keeps_positive_bass_activity_request() -> None:
    plain = build_candidate(_brief(), seed=11, candidate_id="same")
    _, bass_only = _pair("ベースは動きを増やす")
    _, both = _pair("上物は煌びやかにしない。ベースは動きを増やす")
    assert both.clips == bass_only.clips
    assert both.brief.tone_brightness.value == 0
    for part in ("Kick", "Snare", "Hats", "OpenHat", "Stab", "Pad", "Arp", "FX"):
        assert both.clips_for_part(part) == plain.clips_for_part(part)
    assert len(_part_notes(both, "Bass")) > len(_part_notes(plain, "Bass"))
    assert len(_part_notes(both, "Sub")) > len(_part_notes(plain, "Sub"))
    assert any("上物を煌びやかにしない" in item for item in both.brief.interpretations)
    assert any("ベースの動きを増やす" in item for item in both.brief.interpretations)


def test_a_refused_drop_leaves_the_gradual_upper_request_working() -> None:
    _plain, rising = _pair("上物は徐々に煌びやかに")
    _plain, both = _pair("派手じゃないドロップ。上物は徐々に煌びやかに")
    assert both.clips == rising.clips
    assert any("Drop専用の上乗せはしません" in item for item in both.brief.interpretations)


def test_a_flashy_drop_after_refused_upper_parts_only_plays_louder() -> None:
    plain, asked = _pair("上物は煌びやかにしない。ドロップは派手に")
    for part in ("Kick", "Snare", "Hats", "OpenHat", "Bass", "Sub", "FX"):
        assert asked.clips_for_part(part) == plain.clips_for_part(part)
    drop = set(DROP_BARS)
    before, after = _notes_by_bar(plain, "Stab"), _notes_by_bar(asked, "Stab")
    assert all(after.get(bar) == before.get(bar) for bar in set(before) - drop)
    for bar in drop & set(before):
        assert [(p, s, d) for p, s, d, _v in after[bar]] == [
            (p, s, d) for p, s, d, _v in before[bar]
        ]
        assert [v for *_rest, v in after[bar]] == [
            min(127, v + 6) for *_rest, v in before[bar]
        ]


def test_scoped_brightness_leaves_the_sound_recipe_alone() -> None:
    from kihachi_mcp.knowledge.sound_recipes import DUB_TECHNO, tuned
    from kihachi_mcp.services.candidate_live_planner import tone_steps

    def recipe(text):
        intent = {
            "genre": "dub_techno", "tempo": 120, "bars": 64, "key": "Dm", "mood": "",
            "hats_first_half": "normal", "hats_second_half": "normal",
            "bass_register": "mid", "note_density": "normal", "drop_start_bar": 0,
            "tone_brightness": 1, "unhandled": [], "ambiguous": [],
        }
        brief = assemble_brief(extract_explicit(text), intent, model="gemma4")
        return tuned(DUB_TECHNO, tone_steps(brief))

    for text in (
        "ダブテクノ、64小節。上物は煌びやか、ベースはこもった音",
        "ダブテクノ、64小節。キラキラした上物",
        "ダブテクノ、64小節。派手じゃないドロップ",
        "ダブテクノ、64小節。曲全体は派手にしない",
    ):
        assert recipe(text) == DUB_TECHNO, text
    # A whole-song word still turns the Stab and Bass filters.
    brightened = recipe("ダブテクノ、64小節。煌びやかな音")
    assert brightened != DUB_TECHNO


def test_scoped_requests_reproduce_with_the_same_seed() -> None:
    text = BRIEF + "。上物は徐々に煌びやかに。ドロップは派手に"
    first = build_candidate(_brief(text), seed=5)
    second = build_candidate(_brief(text), seed=5)
    # candidate_id is a fresh uuid each time, so compare the notes themselves.
    assert first.candidate_id != second.candidate_id
    assert first.clips == second.clips


def test_a_refused_drop_request_changes_nothing() -> None:
    plain, refused = _pair("ドロップは派手にしすぎない")
    assert refused.clips == plain.clips
    assert refused.brief.tone_brightness.value == 0


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
    for bar in DROP_BARS:
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
    # Bar 32 closes the groove's first 16-bar phrase; bar 31 is an ordinary bar.
    assert kick[32] != kick[31]
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
        distinct = {bars[bar] for bar in DROP_BARS if bar in bars}
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


def test_every_part_the_genre_plays_is_written_in_track_order() -> None:
    candidate = build_candidate(_brief(), seed=5)
    left_out = {"Lead", "Guitar", "Horn", "Vocal"}
    assert candidate.parts == tuple(part for part in PART_ORDER if part not in left_out)


def test_a_brief_that_asks_for_a_part_gets_it() -> None:
    candidate = build_candidate(_brief(BRIEF + "。ギターとホーンも入れて"), seed=5)
    assert {"Guitar", "Horn"} <= set(candidate.parts)
    assert "Vocal" not in candidate.parts


def test_leaving_a_part_out_keeps_the_others_note_for_note() -> None:
    plain = build_candidate(_brief(), seed=5)
    asked = build_candidate(_brief(BRIEF + "。ギターとホーンも入れて"), seed=5)
    for part in plain.parts:
        assert _part_notes(plain, part) == _part_notes(asked, part), part


def test_guitar_cuts_sixteenths_and_the_horn_stabs_at_the_peaks() -> None:
    candidate = build_candidate(_brief(BRIEF + "。ギターとホーンも入れて"), seed=5)
    beats = candidate.brief.beats_per_bar
    guitar = _part_notes(candidate, "Guitar")
    horn = _part_notes(candidate, "Horn")
    assert guitar and horn
    assert all(round(beat * 4, 6) == round(beat * 4) for beat, *_ in guitar)
    assert {velocity < 60 for *_, velocity in guitar} == {True, False}, "muted and open"
    assert all(_bar_of(beat, beats) in DROP_BARS for beat, *_ in horn)
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
    """Stable notes under the current genre harmony and mode rules."""
    candidate = build_candidate(_brief(), seed=5)
    assert _digest(candidate, ["Kick"]) == "85804191d10c43be"
    assert _digest(candidate, ["Hats", "Snare", "OpenHat"]) == "3017d663247297e2"
    assert _digest(candidate, ["Bass"]) == "5f0b1594a7d05274"
    assert _digest(candidate, ["Stab"]) == "4f19da2cd56f39aa"
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
    candidate = build_candidate(_brief(BRIEF + "。ボーカルチョップも"), seed=5)
    beats = candidate.brief.beats_per_bar
    intro = range(1, 17)
    for part in ("Arp", "Vocal"):
        notes = _part_notes(candidate, part)
        assert notes, part
        assert all(_bar_of(beat, beats) not in intro for beat, *_ in notes)
        pitch_classes = {pitch % 12 for _beat, pitch, _velocity in notes}
        d_minor = {2, 4, 5, 7, 9, 10, 0}
        assert pitch_classes <= d_minor



def test_a_song_for_listening_breaks_between_two_drops() -> None:
    sections = [
        (section.name, section.start_bar, section.end_bar)
        for section in build_candidate(_brief(), seed=5).brief.sections
    ]
    assert sections == [
        ("Intro", 1, 16),
        ("VerseA", 17, 40),
        ("Build", 41, 56),
        ("Drop", 57, 72),
        ("Break", 73, 76),
        ("Drop", 77, 92),
        ("Outro", 93, 96),
    ]


def test_the_break_leaves_the_drop_loop_and_leads_back_to_the_tonic() -> None:
    candidate = build_candidate(_brief(), seed=5)
    beats = candidate.brief.beats_per_bar
    pad: dict[int, set[int]] = {}
    for beat, pitch, _velocity in _part_notes(candidate, "Pad"):
        pad.setdefault(_bar_of(beat, beats), set()).add(pitch % 12)
    break_chords = [frozenset(pad[bar]) for bar in range(73, 77)]
    assert len(set(break_chords)) == 4
    assert {10, 2, 5} <= break_chords[0]  # VI: B-flat major
    assert {9, 0, 4} <= break_chords[-1]  # v: A minor, back to D minor next


def test_the_arp_climbs_an_octave_when_the_drop_returns() -> None:
    candidate = build_candidate(_brief(), seed=5)
    beats = candidate.brief.beats_per_bar
    arp = _part_notes(candidate, "Arp")
    first = [pitch for beat, pitch, _v in arp if _bar_of(beat, beats) in range(57, 73)]
    second = [pitch for beat, pitch, _v in arp if _bar_of(beat, beats) in range(77, 93)]
    assert first and second
    assert min(second) >= min(first) + 12


def test_sections_for_a_64_bar_song_with_or_without_a_drop_bar() -> None:
    from kihachi_mcp.services.studio_interpreter import build_sections

    def shape(sections):
        return [(section.name, section.start_bar, section.length_bars) for section in sections]

    assert shape(build_sections(64, 33)) == [
        ("Intro", 1, 16),
        ("Build", 17, 16),
        ("Drop", 33, 16),
        ("Break", 49, 4),
        ("Drop", 53, 8),
        ("Outro", 61, 4),
    ]
    assert shape(build_sections(64, 0)) == [
        ("Intro", 1, 8),
        ("Build", 9, 8),
        ("Drop", 17, 16),
        ("Break", 33, 8),
        ("Drop", 41, 16),
        ("Outro", 57, 8),
    ]
    for bars, drop in ((16, 0), (32, 9), (48, 17), (128, 65), (96, 1)):
        sections = build_sections(bars, drop)
        assert sections[0].start_bar == 1 and sections[-1].end_bar == bars
        for before, after in pairwise(sections):
            assert after.start_bar == before.end_bar + 1


def test_pop_and_liquid_arrangements_have_distinct_sections_and_honor_drop() -> None:
    from kihachi_mcp.services.studio_interpreter import build_sections

    def shape(genre):
        return [(s.name, s.start_bar, s.length_bars) for s in build_sections(64, 0, genre)]

    assert shape("j_pop") == [
        ("Intro", 1, 4), ("VerseA", 5, 12), ("ChorusA", 17, 16),
        ("VerseB", 33, 8), ("Break", 41, 4),
        ("ChorusB", 45, 16), ("Outro", 61, 4),
    ]
    assert shape("liquid_drum_bass") == [
        ("Intro", 1, 8), ("VerseA", 9, 8), ("Build", 17, 8),
        ("Drop", 25, 16), ("Break", 41, 8),
        ("Drop", 49, 8), ("Outro", 57, 8),
    ]
    for genre in ("j_pop", "liquid_drum_bass"):
        assert build_sections(64, 33, genre) == build_sections(64, 33)
        for bars in (32, 48, 96):
            sections = build_sections(bars, 0, genre)
            assert sections[0].start_bar == 1 and sections[-1].end_bar == bars
            assert all(b.start_bar == a.end_bar + 1 for a, b in pairwise(sections))


def test_explicit_mid_break_placement_and_interpretations() -> None:
    from kihachi_mcp.services.studio_interpreter import assemble_brief

    def make_brief(text: str, bars: int = 144, drop: int = 0):
        intent = {
            "genre": "dub_techno",
            "tempo": 120,
            "bars": bars,
            "key": "Dm",
            "mood": "",
            "hats_first_half": "normal",
            "hats_second_half": "normal",
            "bass_register": "mid",
            "note_density": "normal",
            "drop_start_bar": drop,
            "unhandled": [],
            "ambiguous": [],
        }
        return assemble_brief(extract_explicit(text), intent)

    # 1. 144 bars user brief: Break is at bars 73-90 (18 bars) for drop=0, or 73-80 (8 bars) for drop=57
    b144_plain = make_brief("144小節のダブテクノ", 144, 0)
    b144_asked = make_brief("144小節のダブテクノ。中間でBrakeを必ず入れ", 144, 0)
    assert b144_asked.sections == b144_plain.sections
    assert any("73–90小節目（18小節）にBreakを配置" in item for item in b144_asked.interpretations)
    assert not any("Breakを配置しました" in item for item in b144_plain.interpretations)

    b144_drop57_plain = make_brief("144小節、57小節目からドロップ", 144, 57)
    b144_drop57_asked = make_brief("144小節、57小節目からドロップ。中間でBreakを必ず入れ", 144, 57)
    assert b144_drop57_asked.sections == b144_drop57_plain.sections
    assert any("73–80小節目（8小節）にBreakを配置" in item for item in b144_drop57_asked.interpretations)

    # 1b. 144 bars, drop=9: default break is at 25-32 (not middle).
    # It must NOT treat 25-32 as mid break. Instead, it inserts a mid break at 73-76
    # while preserving drop=9, total bars=144, continuity, and keeping 25-32 intact.
    b144_drop9_plain = make_brief("144小節、9小節目からドロップ", 144, 9)
    assert [(s.name, s.start_bar, s.end_bar, s.length_bars) for s in b144_drop9_plain.sections] == [
        ("Intro", 1, 8, 8),
        ("Drop", 9, 24, 16),
        ("Break", 25, 32, 8),
        ("Drop", 33, 136, 104),
        ("Outro", 137, 144, 8),
    ]
    b144_drop9_asked = make_brief("144小節、9小節目からドロップ。中間でBreakを必ず入れ", 144, 9)
    assert not any("25–32小節目" in item for item in b144_drop9_asked.interpretations)
    assert any("73–76小節目（4小節）にBreakを配置" in item for item in b144_drop9_asked.interpretations)
    assert [(s.name, s.start_bar, s.end_bar, s.length_bars) for s in b144_drop9_asked.sections] == [
        ("Intro", 1, 8, 8),
        ("Drop", 9, 24, 16),
        ("Break", 25, 32, 8),
        ("Drop", 33, 72, 40),
        ("Break", 73, 76, 4),
        ("Drop", 77, 136, 60),
        ("Outro", 137, 144, 8),
    ]
    assert b144_drop9_asked.sections[0].start_bar == 1
    assert b144_drop9_asked.sections[-1].end_bar == 144
    assert sum(s.length_bars for s in b144_drop9_asked.sections) == 144
    for before, after in pairwise(b144_drop9_asked.sections):
        assert after.start_bar == before.end_bar + 1

    # 2. Section without break naturally: 48 bars, drop=33 -> inserts Break at 25-28
    b48_plain = make_brief("48小節、33小節目からドロップ", 48, 33)
    b48_asked = make_brief("48小節、33小節目からドロップ。中間にブレイクを入れて", 48, 33)
    assert not any(s.name == "Break" for s in b48_plain.sections)
    assert [(s.name, s.start_bar, s.end_bar, s.length_bars) for s in b48_asked.sections] == [
        ("Intro", 1, 16, 16),
        ("Build", 17, 24, 8),
        ("Break", 25, 28, 4),
        ("Build", 29, 32, 4),
        ("Drop", 33, 44, 12),
        ("Outro", 45, 48, 4),
    ]
    assert any("25–28小節目（4小節）にBreakを配置" in item for item in b48_asked.interpretations)
    # verify continuity and total bars
    assert b48_asked.sections[0].start_bar == 1
    assert b48_asked.sections[-1].end_bar == 48
    assert sum(s.length_bars for s in b48_asked.sections) == 48
    for before, after in pairwise(b48_asked.sections):
        assert after.start_bar == before.end_bar + 1

    # 3. Short song (16 bars): reported in unhandled
    b16 = make_brief("16小節のテクノ。中間にブレイクを入れて", 16, 0)
    assert not any(s.name == "Break" for s in b16.sections)
    assert any("曲が短い構成（16小節）のため" in item for item in b16.unhandled)

    # 4. Drop conflict (32 bars, drop=17): reported in unhandled
    b32 = make_brief("32小節、17小節目からドロップ。中間にブレイクを入れて", 32, 17)
    assert not any(s.name == "Break" for s in b32.sections)
    assert any("干渉するため、中間ブレイクは配置できませんでした" in item for item in b32.unhandled)

    # 5. Declined expression: break is not removed from default, but noted
    b_declined = make_brief("144小節のダブテクノ。中間にブレイクを入れない", 144, 0)
    assert any(s.name == "Break" for s in b_declined.sections)
    assert any("中間にブレイクを入れない" in item and "既定構成のBreakは維持" in item for item in b_declined.interpretations)

    # 6. Notes reproducibility: existing candidate notes unchanged when no break instruction is given
    c_plain = build_candidate(b144_plain, seed=7, candidate_id="same")
    c_plain_2 = build_candidate(b144_plain, seed=7, candidate_id="same")
    assert c_plain.clips == c_plain_2.clips
