from dataclasses import replace

from kihachi_mcp.services.brief_parser import extract_explicit
from kihachi_mcp.services.midi_candidate_builder import build_candidate
from kihachi_mcp.services.production_revision import revise_candidate
from kihachi_mcp.services.session_pattern_builder import MidiNote
from kihachi_mcp.services.studio_interpreter import assemble_brief


def _candidate():
    text = "124 BPM、D# minor、16小節のTech House。"
    intent = {
        "genre": "tech_house",
        "tempo": 124,
        "bars": 16,
        "key": "D#m",
        "mood": "",
        "hats_first_half": "normal",
        "hats_second_half": "normal",
        "bass_register": "mid",
        "note_density": "normal",
        "drop_start_bar": 0,
        "tone_brightness": 0,
        "tone_length": 0,
        "tone_delay": 0,
        "unhandled": [],
        "ambiguous": [],
    }
    brief = assemble_brief(extract_explicit(text), intent)
    candidate = build_candidate(brief, seed=17)
    bass = candidate.clips_for_part("Bass")[0]
    notes = tuple(MidiNote(40 + i, float(i), 0.25, 96) for i in range(4))
    clips = tuple(replace(clip, notes=notes) if clip is bass else clip for clip in candidate.clips)
    return replace(candidate, clips=clips), bass


def test_syncopation_moves_only_selected_bass_notes_and_preserves_count():
    candidate, bass = _candidate()
    result = revise_candidate(candidate, scopes=["syncopation"])

    assert result["ok"] is True
    revised = result["candidate"]
    revised_bass = next(
        clip for clip in revised.clips
        if clip.part == bass.part and clip.start_bar == bass.start_bar
        and clip.section_name == bass.section_name
    )
    assert [note.start_beats for note in revised_bass.notes] == [0, 1.5, 2, 3.5]
    assert revised.note_count() == candidate.note_count()
    assert all(
        before == after
        for before, after in zip(candidate.clips, revised.clips, strict=True)
        if before.part != "Bass"
    )


def test_syncopation_skips_kick_collision_and_respects_bar_range():
    candidate, _bass = _candidate()
    kick = candidate.clips_for_part("Kick")[0]
    kick_notes = (*kick.notes, MidiNote(36, 1.5, 0.25, 110))
    clips = tuple(replace(clip, notes=kick_notes) if clip is kick else clip for clip in candidate.clips)
    candidate = replace(candidate, clips=clips)

    result = revise_candidate(candidate, scopes=["syncopation"], bars=(1, 1))

    assert result["ok"] is True
    revised_bass = next(clip for clip in result["candidate"].clips if clip.part == "Bass")
    assert [note.start_beats for note in revised_bass.notes] == [0, 1, 2, 3.5]


def _hats_in(candidate, first: int, last: int) -> int:
    beats = candidate.brief.beats_per_bar
    return sum(
        1
        for clip in candidate.clips_for_part("Hats")
        for note in clip.notes
        if first <= clip.start_bar + int(note.start_beats // beats) <= last
    )


def test_thin_removes_every_other_hat_only_inside_the_bars():
    candidate, _bass = _candidate()
    before_inside = _hats_in(candidate, 9, 12)
    before_outside = _hats_in(candidate, 1, 8)
    assert before_inside > 1

    result = revise_candidate(candidate, scopes=["thin"], bars=(9, 12), parts=["Hats"])

    assert result["ok"] is True
    revised = result["candidate"]
    assert 0 < _hats_in(revised, 9, 12) < before_inside
    assert _hats_in(revised, 1, 8) == before_outside
    assert revised.clips_for_part("Kick") == candidate.clips_for_part("Kick")
    assert "Hats" in result["after"][0]


def test_clear_removes_the_named_part_inside_the_bars_and_needs_a_part():
    candidate, _bass = _candidate()
    result = revise_candidate(candidate, scopes=["clear"], bars=(9, 12), parts=["Hats"])
    assert result["ok"] is True
    assert _hats_in(result["candidate"], 9, 12) == 0
    assert _hats_in(result["candidate"], 1, 8) == _hats_in(candidate, 1, 8)

    refused = revise_candidate(candidate, scopes=["clear"], bars=(9, 12))
    assert refused["ok"] is False


def test_thicken_adds_softer_hats_between_hits_without_overlaps():
    candidate, _bass = _candidate()
    hats = candidate.clips_for_part("Hats")[0]
    sparse = tuple(MidiNote(42, float(beat), 0.25, 100) for beat in range(hats.length_bars * 4))
    clips = tuple(replace(clip, notes=sparse) if clip is hats else clip for clip in candidate.clips)
    candidate = replace(candidate, clips=clips)
    first = hats.start_bar
    before_outside = _hats_in(candidate, first + 1, first + hats.length_bars - 1)

    result = revise_candidate(candidate, scopes=["thicken"], bars=(first, first), parts=["Hats"])

    assert result["ok"] is True
    revised = next(clip for clip in result["candidate"].clips_for_part("Hats") if clip.start_bar == first)
    starts = [note.start_beats for note in revised.notes]
    assert starts[:8] == [0, 0.5, 1, 1.5, 2, 2.5, 3, 3.5]
    assert all(note.velocity == 75 for note in revised.notes if note.start_beats in (0.5, 1.5))
    assert all(
        later.start_beats >= earlier.start_beats + earlier.duration_beats - 1e-9
        for earlier, later in zip(revised.notes, revised.notes[1:], strict=False)
    )
    assert _hats_in(result["candidate"], first + 1, first + hats.length_bars - 1) == before_outside
