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


def test_bass_constraints_avoid_kick_and_create_syncopation() -> None:
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
    assert {note.start for note in notes}.isdisjoint({0.5, 4.5})
    assert all(note.start % 1 == 0.5 for note in notes)
    assert max(note.pitch for note in notes) - min(note.pitch for note in notes) == 12


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
