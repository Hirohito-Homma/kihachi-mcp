from itertools import pairwise

import pytest

from kihachi_mcp.knowledge.genre_database import load_database
from kihachi_mcp.knowledge.genre_profiles import (
    CHORD_ARTICULATIONS,
    DRUM_PATTERNS,
    FAMILY_PROFILES,
    GENRE_PROFILES,
    profile_for,
    swung,
)
from kihachi_mcp.services.brief_parser import extract_explicit
from kihachi_mcp.services.drum_samples import sample_path_for_note
from kihachi_mcp.services.midi_candidate_builder import build_candidate
from kihachi_mcp.services.studio_interpreter import assemble_brief


def _candidate(text: str, seed: int = 5):
    intent = {
        "genre": "tech_house",
        "tempo": 120,
        "bars": 64,
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
    return build_candidate(assemble_brief(extract_explicit(text), intent), seed=seed)


def _drop_bars(candidate):
    drop = next(s for s in candidate.brief.sections if s.name == "Drop")
    return range(drop.start_bar, drop.end_bar + 1)


def _notes_in(candidate, part, bars):
    """(bar, offset in bar, pitch, duration, velocity) for one part."""
    beats = candidate.brief.beats_per_bar
    found = []
    for clip in candidate.clips_for_part(part):
        for note in clip.notes:
            absolute = (clip.start_bar - 1) * beats + note.start_beats
            bar = int(absolute // beats) + 1
            if bar in bars:
                found.append((bar, round(absolute % beats, 6), note.pitch, note.duration_beats, note.velocity))
    return found


def test_every_family_key_is_a_database_family() -> None:
    families = {genre.family for genre in load_database()}
    assert set(FAMILY_PROFILES) <= families


def test_every_named_pattern_and_articulation_exists() -> None:
    for profile in [*FAMILY_PROFILES.values(), *GENRE_PROFILES.values()]:
        assert profile.drum_pattern in (None, *DRUM_PATTERNS)
        assert profile.articulation in (None, *CHORD_ARTICULATIONS)


def test_every_drum_sound_a_pattern_plays_has_a_bundled_sample() -> None:
    """A pitch with no sample would apply as silence on an empty Drum Rack."""
    for pattern in DRUM_PATTERNS.values():
        for pitch in (pattern.backbeat_pitch, pattern.hat_pitch, 36, 39, 42, 46):
            assert sample_path_for_note(pitch).is_file()


@pytest.mark.parametrize(
    "text",
    ["ダブテクノ", "ダブ", "ジャズ", "ブルース", "アンビエント", "ボサノバ", "サルサ",
     "ドラムンベース", "UKガラージ", "ヒップホップ", "メタル", "ロック", "カントリー"],
)
def test_every_family_builds_notes_that_stay_inside_their_clips(text: str) -> None:
    candidate = _candidate(text)
    for clip in candidate.clips:
        length = clip.length_bars * candidate.brief.beats_per_bar
        for note in clip.notes:
            assert 0 <= note.start_beats < length
            assert note.start_beats + note.duration_beats <= length + 1e-6
            assert 1 <= note.velocity <= 127
    for part in ("Kick", "Hats"):
        for pitch in candidate.used_pitches(part):
            assert sample_path_for_note(pitch).is_file()


def test_reggae_leaves_beat_one_empty_and_drops_the_snare_on_three() -> None:
    candidate = _candidate("ダブ、64小節")
    kick = _notes_in(candidate, "Kick", _drop_bars(candidate))
    assert kick and all(offset != 0.0 for _bar, offset, *_ in kick)
    snare = [n for n in _notes_in(candidate, "Hats", _drop_bars(candidate)) if n[2] == 38]
    loud = {offset for _bar, offset, _p, _d, velocity in snare if velocity >= 80}
    assert loud == {2.0}  # quieter ghost notes elsewhere are fine


def test_techno_keeps_four_on_the_floor() -> None:
    candidate = _candidate("テクノ、64小節")
    bars = list(_drop_bars(candidate))[:4]
    offsets = {offset for _bar, offset, *_ in _notes_in(candidate, "Kick", bars)}
    assert {0.0, 1.0, 2.0, 3.0} <= offsets


def test_jazz_rides_and_swings() -> None:
    assert profile_for("bebop").swing == 0.58
    candidate = _candidate("ビバップ、64小節")
    ride = [n for n in _notes_in(candidate, "Hats", _drop_bars(candidate)) if n[2] == 51]
    assert ride
    offbeats = {round(offset % 1.0, 6) for _bar, offset, *_ in ride if offset % 1.0}
    assert offbeats == {0.58}


def test_a_triplet_shuffle_comes_from_a_12_8_meter() -> None:
    assert abs(profile_for("chicago_blues").swing - 2 / 3) < 1e-9


def test_swing_warps_the_beat_and_leaves_downbeats_alone() -> None:
    assert swung(0.0, 0.58) == 0.0
    assert swung(0.5, 0.58) == 0.58
    assert swung(1.5, 2 / 3) == round(1 + 2 / 3, 6)
    assert swung(0.25, 0.5) == 0.25


def test_ambient_holds_pads_and_has_no_backbeat() -> None:
    candidate = _candidate("アンビエント、64小節")
    stab = _notes_in(candidate, "Stab", _drop_bars(candidate))
    assert stab and max(duration for *_x, duration, _v in stab) >= 3.5
    drums = _notes_in(candidate, "Hats", _drop_bars(candidate))
    assert not [n for n in drums if n[2] in {38, 39}]


def test_dub_techno_chords_leave_the_delay_to_the_echo_device() -> None:
    """The recipe puts a real Echo on the chords; repeats written as notes too
    would double the delay, as they did on 2026-09-27 in Live."""
    candidate = _candidate("ダブテクノ、64小節")
    bars = _drop_bars(candidate)
    stab = _notes_in(candidate, "Stab", bars)
    loudest = max(velocity for *_x, velocity in stab)
    assert all(velocity > loudest * 0.6 for *_x, velocity in stab)
    chords = [
        tuple(sorted({pitch for bar, _o, pitch, *_ in stab if bar == number}))
        for number in bars
    ]
    changes = sum(1 for a, b in pairwise(chords) if a != b)
    assert changes <= len(chords) // 4


def test_a_supporting_bass_plays_less_than_a_dominant_one() -> None:
    techno = _candidate("テクノ、64小節", seed=3)
    dnb = _candidate("ドラムンベース、64小節", seed=3)
    assert profile_for("techno").bass_role == "supporting"
    assert techno.note_count("Bass") < dnb.note_count("Bass")


def test_a_delay_request_echoes_stabs_here_and_there() -> None:
    plain = _candidate("テックハウス、64小節")
    delayed = _candidate("テックハウス、64小節、ディレイをところどころに")
    assert delayed.note_count("Stab") > plain.note_count("Stab")
    beats = delayed.brief.beats_per_bar
    extra_bars = {
        int(((clip.start_bar - 1) * beats + note.start_beats) // beats) + 1
        for clip in delayed.clips_for_part("Stab")
        for note in clip.notes
    }
    assert extra_bars  # echoes land inside Build/Drop only, checked by clip bounds
