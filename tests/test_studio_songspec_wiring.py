"""Phase V prep: Studio runtime wiring from SongSpec v2 into composition.

The runtime's compose path may receive a SongSpec dict plus a 1-based
start bar; the spec then supplies the per-bar chords, the section name
and energy, and groove defaults (swing / syncopation) for whatever the
caller left unspecified. Adoption can be scoped to one section so a
per-section pattern no longer tiles across the whole song.
"""

from __future__ import annotations

from test_studio_workflow import SMOKE_TEST, _studio

SPEC = {
    "genre": "Dub Techno",
    "tempo": 110,
    "key": "D#m",
    "length_minutes": 2.0,
    "bars": 16,
    "tracks": [],
    "sections": [
        {"name": "Groove", "bars": 8, "energy": 0.7, "chords": ["D#m", "B"], "tracks": [], "note": ""},
        {"name": "Drop", "bars": 8, "energy": 0.9, "chords": ["F#", "C#"], "tracks": [], "note": ""},
    ],
    "groove": {
        "swing": 0.54,
        "kick_style": "four_on_the_floor",
        "bass_density": 6.3,
        "syncopation": 0.35,
        "rest_ratio": 0.52,
        "preserve_space": True,
        "dynamic_profile": "stable",
        "profile_id": "kihachi_style_v1",
    },
}

BASS_8 = {"key": "D#m", "tempo": 110, "bars": 8, "role": "bass"}


def test_preview_enriches_constraints_from_songspec(tmp_path) -> None:
    runtime, _live = _studio(tmp_path)
    preview = runtime.midi_composer_preview(BASS_8, songspec=SPEC, start_bar=9)
    assert preview["ok"] is True
    constraints = preview["request"]["constraints"]
    assert tuple(constraints["chords_by_bar"]) == ("F#",) * 4 + ("C#",) * 4
    assert constraints["section_name"] == "Drop"
    assert constraints["section_energy"] == 0.9
    assert constraints["swing"] == 0.54
    assert constraints["syncopation"] == 0.35


def test_preview_first_section_from_bar_one(tmp_path) -> None:
    runtime, _live = _studio(tmp_path)
    preview = runtime.midi_composer_preview(BASS_8, songspec=SPEC, start_bar=1)
    assert preview["ok"] is True
    constraints = preview["request"]["constraints"]
    assert tuple(constraints["chords_by_bar"]) == ("D#m",) * 4 + ("B",) * 4
    assert constraints["section_name"] == "Groove"


def test_compose_bass_anchors_on_section_chords(tmp_path) -> None:
    runtime, _live = _studio(tmp_path)
    composed = runtime.compose_midi_part(BASS_8, songspec=SPEC, start_bar=9, seed=8)
    assert composed["ok"] is True
    downbeats = {
        note["start"]: note["pitch"] % 12
        for note in composed["notes"]
        if note["start"] % 4 == 0
    }
    assert downbeats[0.0] == 6  # F# bars anchor on F#
    assert downbeats[16.0] == 1  # C# bars anchor on C#


def test_songspec_span_outside_is_an_error(tmp_path) -> None:
    runtime, _live = _studio(tmp_path)
    preview = runtime.midi_composer_preview(BASS_8, songspec=SPEC, start_bar=100)
    assert preview["ok"] is False
    composed = runtime.compose_midi_part(BASS_8, songspec=SPEC, start_bar=100)
    assert composed["ok"] is False


def test_explicit_constraints_win_over_songspec(tmp_path) -> None:
    runtime, _live = _studio(tmp_path)
    explicit = dict(BASS_8, chords_by_bar=["D#m"] * 8, swing=0.5)
    preview = runtime.midi_composer_preview(explicit, songspec=SPEC, start_bar=9)
    assert preview["ok"] is True
    constraints = preview["request"]["constraints"]
    assert tuple(constraints["chords_by_bar"]) == ("D#m",) * 8
    assert constraints["swing"] == 0.5


def test_section_scoped_adoption_only_touches_that_section(tmp_path) -> None:
    runtime, _live = _studio(tmp_path)
    parent_id = runtime.generate(SMOKE_TEST, seed=3)["candidate"]["candidate_id"]
    parent = runtime.get_candidate(parent_id)
    bass_sections = [clip.section_name for clip in parent.clips if clip.part == "Bass"]
    assert bass_sections
    target_section = bass_sections[0]

    composed = runtime.compose_midi_part(
        {"key": "Cm", "tempo": 120, "bars": 2, "role": "bass", "syncopation": 0.8},
        seed=8,
    )
    proposal = runtime.propose_composer_adoption(
        parent_id, "bass", 2, composed["notes"], section_name=target_section
    )
    assert proposal["ok"] is True
    assert proposal["other_parts_unchanged"] is True

    decision = runtime.decide_revision(proposal["revision"]["revision_id"], True)
    child = runtime.get_candidate(decision["selected_candidate_id"])
    changed_in_scope = 0
    for before, after in zip(parent.clips, child.clips, strict=True):
        in_scope = before.part == "Bass" and before.section_name == target_section
        if in_scope:
            changed_in_scope += int(before != after)
        else:
            assert before == after
    assert changed_in_scope >= 1


def test_scoped_adoption_unknown_section_is_an_error(tmp_path) -> None:
    runtime, _live = _studio(tmp_path)
    parent_id = runtime.generate(SMOKE_TEST, seed=3)["candidate"]["candidate_id"]
    composed = runtime.compose_midi_part(
        {"key": "Cm", "tempo": 120, "bars": 2, "role": "bass"},
        seed=8,
    )
    refused = runtime.propose_composer_adoption(
        parent_id, "bass", 2, composed["notes"], section_name="存在しないセクション"
    )
    assert refused["ok"] is False
