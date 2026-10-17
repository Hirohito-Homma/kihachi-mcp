import pytest
from test_studio_workflow import SMOKE_TEST, _studio

from kihachi_mcp.models.midi_composer import (
    ComposedMidiNote,
    MidiCompositionRequest,
    MusicalConstraints,
)
from kihachi_mcp.services.ai_cost_control import MonthlyCostGuard
from kihachi_mcp.services.ai_provider import OpenAIProvider
from kihachi_mcp.services.midi_composer import (
    DeterministicMIDIComposer,
    ProviderMIDIComposer,
)
from kihachi_mcp.services.studio_runtime import StudioRuntime
from kihachi_mcp.tools.composer import compose_midi_part


@pytest.mark.parametrize("role", ["drums", "bass", "chords", "melody", "arp", "percussion"])
def test_deterministic_composer_supports_every_required_role(role: str) -> None:
    constraints = MusicalConstraints(key="D#m", tempo=124, bars=2, role=role)
    notes = DeterministicMIDIComposer(seed=7).compose(MidiCompositionRequest(constraints))
    assert notes
    assert all(0 <= note.start < 8 for note in notes)
    assert all(65 <= note.velocity <= 118 for note in notes)


def test_every_studio_part_is_a_composer_role() -> None:
    from kihachi_mcp.models.production_brief import PART_ORDER

    for part in PART_ORDER:
        constraints = MusicalConstraints(key="D#m", tempo=124, bars=1, role=part)
        notes = DeterministicMIDIComposer(seed=3).compose(MidiCompositionRequest(constraints))
        assert notes
    kick = DeterministicMIDIComposer(seed=3).compose(
        MidiCompositionRequest(MusicalConstraints(key="D#m", tempo=124, bars=1, role="Kick"))
    )
    assert {note.pitch for note in kick} == {36}


def test_midi_constraints_carry_genre_mood_and_prompt() -> None:
    empty = MusicalConstraints(key="D#m", tempo=124, bars=1, role="bass")
    assert "genre" not in empty.to_dict()
    filled = MusicalConstraints.from_dict(
        {
            "key": "D#m",
            "tempo": 124,
            "bars": 8,
            "role": "全パート",
            "genre": "mutation_funk",
            "mood": "暗い",
            "prompt": "キックは短め。ミューテーションファンクを作って",
        }
    )
    payload = filled.to_dict()
    assert payload["genre"] == "mutation_funk"
    assert payload["mood"] == "暗い"
    assert "ミューテーションファンク" in payload["prompt"]
    captured: list[str] = []

    class Recorder:
        def capabilities(self):
            return {"structured_output": True}

        def generate_structured(self, prompt, schema):
            captured.append(prompt)
            return {"notes": [{"pitch": 36, "start": 0, "duration": 0.25, "velocity": 90, "channel": 0}]}

    notes = ProviderMIDIComposer(Recorder()).compose(MidiCompositionRequest(filled))
    assert notes
    assert "musical direction only" in captured[0]
    assert "mutation_funk" in captured[0]


def test_all_parts_role_combines_every_studio_part() -> None:
    notes = DeterministicMIDIComposer(seed=3).compose(
        MidiCompositionRequest(MusicalConstraints(key="D#m", tempo=124, bars=1, role="全パート"))
    )
    assert 36 in {note.pitch for note in notes}
    assert len({note.pitch for note in notes}) > 1


def test_bass_constraints_avoid_kick_and_create_syncopation() -> None:
    # 1. High syncopation: pop notes shift to 16th syncopations (0.75, 3.25), avoiding straight 1.0/3.0
    constraints = MusicalConstraints(
        key="D#m",
        tempo=124,
        bars=2,
        role="bass",
        syncopation=0.8,
        octave_jump=0.5,
        avoid_kick_collision=True,
        kick_onsets=(0.5, 4.5),
    )
    notes = DeterministicMIDIComposer(seed=2).compose(MidiCompositionRequest(constraints))

    # Avoid kick onsets (0.5, 4.5)
    starts = {note.start for note in notes}
    assert starts.isdisjoint({0.5, 4.5})

    # Octave slap dynamics present (difference of 12 semitones between thumb and pop)
    assert max(note.pitch for note in notes) - min(note.pitch for note in notes) == 12

    # Syncopation creates 16th-note anticipation/delay pops (0.75, 3.25, 4.75, 7.25)
    sixteenth_syncopations = {s for s in starts if round(s % 1, 2) in (0.25, 0.75)}
    assert sixteenth_syncopations == {0.75, 3.25, 4.75, 7.25}

    # Contrast with straight bass (syncopation=0.0): straight bass keeps on-beat 1.0/3.0
    straight = DeterministicMIDIComposer(seed=2).compose(
        MidiCompositionRequest(
            MusicalConstraints(
                key="D#m",
                tempo=124,
                bars=2,
                role="bass",
                syncopation=0.0,
                avoid_kick_collision=False,
            )
        )
    )
    straight_starts = {note.start for note in straight}
    assert {1.0, 3.0, 5.0, 7.0}.issubset(straight_starts)
    assert not {0.75, 3.25, 4.75, 7.25}.intersection(straight_starts)

    # Monophony: no duplicate start times and no overlapping durations
    assert len(notes) == len(starts)
    for i in range(len(notes) - 1):
        assert notes[i].start + notes[i].duration <= notes[i + 1].start + 1e-4


def test_revision_preserves_note_count_and_moves_only_eligible_bass_notes() -> None:
    source = tuple(ComposedMidiNote(39, float(index), 0.25, 90) for index in range(4))
    constraints = MusicalConstraints(
        key="D#m", tempo=124, bars=1, role="bass", syncopation=0.8,
        avoid_kick_collision=True, kick_onsets=(1.5,),
    )
    result = DeterministicMIDIComposer().compose(
        MidiCompositionRequest(constraints, operation="revision", source_notes=source)
    )
    assert len(result) == len(source)
    assert [note.start for note in result] == [0.0, 1.0, 2.0, 3.5]


def test_live_conversion_drops_only_optional_fields() -> None:
    note = ComposedMidiNote(60, 0.5, 0.25, 99, channel=2, probability=0.8, expression={"pressure": 0.4})
    assert note.to_live_note().to_dict() == {"pitch": 60, "start": 0.5, "duration": 0.25, "velocity": 99}


class _Provider:
    def capabilities(self):
        return {"structured_output": True}

    def generate_structured(self, prompt, schema):
        assert "source_notes" in prompt
        assert schema["properties"]["notes"]["maxItems"] == 4096
        return {"notes": [{"pitch": 39, "start": 0.5, "duration": 0.25, "velocity": 90, "channel": 0}]}


def test_provider_composer_uses_the_same_validated_contract() -> None:
    request = MidiCompositionRequest(MusicalConstraints(key="D#m", tempo=124, bars=1, role="bass"))
    notes = ProviderMIDIComposer(_Provider()).compose(request)
    assert notes == (ComposedMidiNote(39, 0.5, 0.25, 90),)


def test_provider_composer_validates_ghost_notes_and_rejects_out_of_range() -> None:
    class FakeProvider:
        def __init__(self, notes):
            self._notes = notes

        def capabilities(self):
            return {"structured_output": True}

        def generate_structured(self, prompt, schema):
            return {"notes": self._notes}

    # 1. ghost_notes=True: 正当なbass ghost（35〜55）と通常ノート（>= velocity_min）を含む場合は合格
    c_ghost = MusicalConstraints(key="D#m", tempo=124, bars=1, role="bass", ghost_notes=True, velocity_min=65, velocity_max=118)
    good_notes = [
        {"pitch": 39, "start": 0.0, "duration": 0.25, "velocity": 90, "channel": 0},
        {"pitch": 39, "start": 0.5, "duration": 0.1, "velocity": 45, "channel": 0},
    ]
    res = ProviderMIDIComposer(FakeProvider(good_notes)).compose(MidiCompositionRequest(c_ghost))
    assert len(res) == 2
    assert res[1].velocity == 45

    # 2. ghost_notes=True でも、ghost範囲（35〜55）と通常下限（65）の中間にある中途半端なvelocity（例: 60）は不合格
    mid_vel_notes = [
        {"pitch": 39, "start": 0.0, "duration": 0.25, "velocity": 60, "channel": 0},
    ]
    with pytest.raises(ValueError, match="velocityが制約外です"):
        ProviderMIDIComposer(FakeProvider(mid_vel_notes)).compose(MidiCompositionRequest(c_ghost))

    # 3. ghost_notes=True でも、ghost下限（35）を下回る不正な弱音（例: 20）は不合格
    too_quiet_notes = [
        {"pitch": 39, "start": 0.0, "duration": 0.25, "velocity": 20, "channel": 0},
    ]
    with pytest.raises(ValueError, match="velocityが制約外です"):
        ProviderMIDIComposer(FakeProvider(too_quiet_notes)).compose(MidiCompositionRequest(c_ghost))

    # 4. ghost_notes=False の場合、35〜55の弱音ノートは下限（65）違反として不合格
    c_no_ghost = MusicalConstraints(key="D#m", tempo=124, bars=1, role="bass", ghost_notes=False, velocity_min=65, velocity_max=118)
    with pytest.raises(ValueError, match="velocityが制約外です"):
        ProviderMIDIComposer(FakeProvider(good_notes)).compose(MidiCompositionRequest(c_no_ghost))

    # 5. 上限（velocity_max=118）を超えるノート（例: 125）は不合格
    too_loud_notes = [
        {"pitch": 39, "start": 0.0, "duration": 0.25, "velocity": 125, "channel": 0},
    ]
    with pytest.raises(ValueError, match="velocityが制約外です"):
        ProviderMIDIComposer(FakeProvider(too_loud_notes)).compose(MidiCompositionRequest(c_ghost))


def test_mcp_composer_is_offline_and_does_not_apply_to_live() -> None:
    result = compose_midi_part({"key": "D#m", "tempo": 124, "bars": 2, "role": "arp"}, seed=4)
    assert result["ok"] is True
    assert result["paid_api"] is False
    assert result["applied_to_live"] is False
    assert result["note_count"] > 0


def test_variation_requires_source_notes() -> None:
    result = compose_midi_part({"key": "D#m", "tempo": 124, "bars": 2, "role": "bass"}, operation="variation")
    assert result["ok"] is False
    assert "source_notes" in result["error"]


def test_studio_paid_composer_requires_preview_confirmation_and_records_cost(
    tmp_path, monkeypatch
) -> None:
    calls = []

    def structured(self, prompt, schema):
        calls.append(prompt)
        self.last_usage = {"input_tokens": 500, "output_tokens": 100}
        return {"notes": [{"pitch": 39, "start": 0.5, "duration": 0.25, "velocity": 90, "channel": 0}]}

    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-sent")
    monkeypatch.setattr(OpenAIProvider, "generate_structured", structured)
    ledger = tmp_path / "costs.json"
    runtime = StudioRuntime(cost_guard=MonthlyCostGuard(ledger))
    assert runtime.update_settings(
        {"ai_provider": "openai", "openai_model": "gpt-6-astra"}, persist=False
    )["ok"]
    constraints = {"key": "D#m", "tempo": 124, "bars": 1, "role": "bass"}

    preview = runtime.midi_composer_preview(constraints)
    refused = runtime.compose_midi_part(constraints)

    assert preview["paid_api"] is True and preview["model"] == "gpt-6-astra"
    assert refused["ok"] is False and calls == []
    generated = runtime.compose_midi_part(constraints, confirmed_paid=True)
    assert generated["ok"] is True and generated["applied_to_live"] is False
    assert len(calls) == 1
    assert MonthlyCostGuard(ledger).status()["spent_jpy"] > 0


def test_composer_adoption_proposes_only_the_selected_candidate_part(tmp_path) -> None:
    runtime, _live = _studio(tmp_path)
    parent_id = runtime.generate(SMOKE_TEST, seed=3)["candidate"]["candidate_id"]
    parent = runtime.get_candidate(parent_id)
    composed = runtime.compose_midi_part(
        {"key": "Dm", "tempo": 120, "bars": 2, "role": "bass", "syncopation": 0.8},
        seed=8,
    )
    proposal = runtime.propose_composer_adoption(
        parent_id, "bass", 2, composed["notes"]
    )
    assert proposal["ok"] is True
    assert proposal["target_part"] == "Bass"
    assert proposal["other_parts_unchanged"] is True
    assert runtime.selected_id() == parent_id

    decision = runtime.decide_revision(proposal["revision"]["revision_id"], True)
    child = runtime.get_candidate(decision["selected_candidate_id"])
    assert child.parent_candidate_id == parent_id
    for before, after in zip(parent.clips, child.clips, strict=True):
        if before.part != "Bass":
            assert before == after


def test_composer_adoption_rejects_ambiguous_drum_bundle(tmp_path) -> None:
    runtime, _live = _studio(tmp_path)
    parent_id = runtime.generate(SMOKE_TEST, seed=3)["candidate"]["candidate_id"]
    refused = runtime.propose_composer_adoption(
        parent_id,
        "drums",
        1,
        [{"pitch": 36, "start": 0, "duration": 0.25, "velocity": 100}],
    )
    assert refused["ok"] is False
    assert "Kick/Hats/Snare" in refused["error"]


def test_funky_slap_bass_fill_bar_is_strictly_monophonic_with_kick_avoidance() -> None:
    from kihachi_mcp.models.instrument_spec import slap_bass_spec
    from kihachi_mcp.models.music_plan import (
        GenerationScope,
        MusicClip,
        MusicNote,
        MusicPlan,
        MusicTrack,
    )
    from kihachi_mcp.services.music_plan_validator import validate_music_plan

    # 4 bars: bar 3 (index 3) is a fill bar (is_fill_bar == True)
    kick_onsets = tuple(float(beat) for beat in range(16))
    constraints = MusicalConstraints(
        key="D#m",
        tempo=110,
        bars=4,
        role="bass",
        syncopation=0.7,
        octave_jump=0.12,
        ghost_notes=True,
        avoid_kick_collision=True,
        kick_onsets=kick_onsets,
    )
    notes = DeterministicMIDIComposer(seed=110).compose(MidiCompositionRequest(constraints))
    assert notes

    # 1. Monophony: no identical start times
    starts = [note.start for note in notes]
    assert len(starts) == len(set(starts)), "Bass notes must have unique start times"

    # 2. Monophony: no overlapping durations
    for i in range(len(notes) - 1):
        curr = notes[i]
        nxt = notes[i + 1]
        assert curr.start + curr.duration <= nxt.start + 1e-4, f"Note {curr} overlaps with {nxt}"

    # 3. Fill bar (bar 4, 12.0 <= start < 16.0) exists and has slap notes
    fill_notes = [n for n in notes if 12.0 <= n.start < 16.0]
    assert len(fill_notes) >= 6, "Fill bar should have dense slap/pop/ghost pattern"
    assert {n.pitch for n in fill_notes} == {39, 49, 51}

    # 4. MusicPlan validation succeeds with max_polyphony=1
    plan = MusicPlan(
        tempo=110,
        key="D#m",
        bars=4,
        tracks=(
            MusicTrack(
                id="slap_bass",
                name="SLAP BASS",
                clips=(
                    MusicClip(
                        start_bar=1,
                        length_bars=4,
                        notes=tuple(MusicNote(n.pitch, n.start, n.duration, n.velocity) for n in notes),
                    ),
                ),
            ),
        ),
        scope=GenerationScope(tracks=("slap_bass",), start_bar=1, length_bars=4),
    )
    res = validate_music_plan(plan, {"slap_bass": slap_bass_spec()})
    assert res.ok is True, [issue.message for issue in res.issues]


def test_funky_slap_bass_turnaround_bar_is_strictly_monophonic_with_kick_avoidance() -> None:
    from kihachi_mcp.models.instrument_spec import slap_bass_spec
    from kihachi_mcp.models.music_plan import (
        GenerationScope,
        MusicClip,
        MusicNote,
        MusicPlan,
        MusicTrack,
    )
    from kihachi_mcp.services.music_plan_validator import validate_music_plan

    # 8 bars: bar 7 (index 7) is an 8-bar turnaround bar (is_turnaround == True)
    kick_onsets = tuple(float(beat) for beat in range(32))
    constraints = MusicalConstraints(
        key="D#m",
        tempo=110,
        bars=8,
        role="bass",
        syncopation=0.7,
        octave_jump=0.12,
        ghost_notes=True,
        avoid_kick_collision=True,
        kick_onsets=kick_onsets,
    )
    notes = DeterministicMIDIComposer(seed=110).compose(MidiCompositionRequest(constraints))
    assert notes

    # 1. Monophony: no identical start times
    starts = [note.start for note in notes]
    assert len(starts) == len(set(starts)), "Bass notes must have unique start times"

    # 2. Monophony: no overlapping durations
    for i in range(len(notes) - 1):
        curr = notes[i]
        nxt = notes[i + 1]
        assert curr.start + curr.duration <= nxt.start + 1e-4, f"Note {curr} overlaps with {nxt}"

    # 3. Turnaround bar (bar 8, 28.0 <= start < 32.0) exists and has turnaround run notes
    turnaround_notes = [n for n in notes if 28.0 <= n.start < 32.0]
    assert len(turnaround_notes) >= 6, "Turnaround bar should have dense pentatonic run"

    # 4. MusicPlan validation succeeds with max_polyphony=1
    plan = MusicPlan(
        tempo=110,
        key="D#m",
        bars=8,
        tracks=(
            MusicTrack(
                id="slap_bass",
                name="SLAP BASS",
                clips=(
                    MusicClip(
                        start_bar=1,
                        length_bars=8,
                        notes=tuple(MusicNote(n.pitch, n.start, n.duration, n.velocity) for n in notes),
                    ),
                ),
            ),
        ),
        scope=GenerationScope(tracks=("slap_bass",), start_bar=1, length_bars=8),
    )
    res = validate_music_plan(plan, {"slap_bass": slap_bass_spec()})
    assert res.ok is True, [issue.message for issue in res.issues]
