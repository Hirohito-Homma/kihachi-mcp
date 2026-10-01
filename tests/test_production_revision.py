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
