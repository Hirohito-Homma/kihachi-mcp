"""Phase W: cross-part context for composition.

A compose request may carry summaries of the other parts that already
play in the same section (pitch set, onsets, long notes). Providers see
the context in their prompt, lead-like lines are validated against the
bass line's long notes, and the Studio runtime builds the summaries
from a stored candidate. Without context, behavior is unchanged.
"""

from __future__ import annotations

import pytest
from test_studio_workflow import SMOKE_TEST, _studio

from kihachi_mcp.models.midi_composer import (
    ComposedMidiNote,
    ContextLongNote,
    ContextPart,
    MidiCompositionRequest,
    MusicalConstraints,
)
from kihachi_mcp.services.midi_composer import (
    ProviderMIDIComposer,
    context_collision_ratio,
)

BASS_CONTEXT = ContextPart(
    part="Bass",
    note_count=4,
    pitches=(35, 39, 42),
    onsets=(0.0, 1.0, 2.0, 3.0),
    long_notes=(ContextLongNote(start=0.0, pitch=39, duration=4.0),),
)


def _constraints(**overrides) -> MusicalConstraints:
    base = {"key": "D#m", "tempo": 110, "bars": 4, "role": "melody"}
    base.update(overrides)
    return MusicalConstraints(**base)


def _notes(*specs: tuple[int, float]) -> tuple[ComposedMidiNote, ...]:
    return tuple(
        ComposedMidiNote(pitch=pitch, start=start, duration=0.25, velocity=100)
        for pitch, start in specs
    )


def test_context_part_round_trip() -> None:
    data = BASS_CONTEXT.to_dict()
    assert ContextPart.from_dict(data) == BASS_CONTEXT


def test_request_omits_context_when_empty() -> None:
    request = MidiCompositionRequest(constraints=_constraints())
    assert "context_parts" not in request.to_dict()
    with_context = MidiCompositionRequest(
        constraints=_constraints(), context_parts=(BASS_CONTEXT,)
    )
    assert with_context.to_dict()["context_parts"][0]["part"] == "Bass"


def test_collision_ratio_counts_doubled_long_bass_notes() -> None:
    colliding = _notes((39, 0.5), (39, 1.5), (63, 2.0), (66, 3.0))
    assert context_collision_ratio(colliding, _constraints(), (BASS_CONTEXT,)) == 0.5
    clear = _notes((63, 0.5), (66, 1.5))
    assert context_collision_ratio(clear, _constraints(), (BASS_CONTEXT,)) == 0.0


def test_collision_ratio_applies_only_to_lead_like_roles() -> None:
    notes = _notes((39, 0.5))
    assert context_collision_ratio(notes, _constraints(role="chords"), (BASS_CONTEXT,)) is None
    assert context_collision_ratio(notes, _constraints(role="bass"), (BASS_CONTEXT,)) is None
    no_bass = (ContextPart(part="Stab", note_count=3),)
    assert context_collision_ratio(notes, _constraints(), no_bass) is None
    short_bass = (ContextPart(part="Bass", note_count=2, pitches=(39,)),)
    assert context_collision_ratio(notes, _constraints(), short_bass) is None


class _StubProvider:
    """Paid-style provider: one attempt, captures the prompt it was given."""

    def __init__(self, notes: list[dict]) -> None:
        self._notes = notes
        self.prompt = ""

    def capabilities(self) -> dict:
        return {"structured_output": True, "paid_api": True}

    def generate_structured(self, prompt: str, schema: dict) -> dict:
        self.prompt = prompt
        return {"notes": self._notes}


def _note_dicts(*specs: tuple[int, float]) -> list[dict]:
    return [
        {"pitch": pitch, "start": start, "duration": 0.25, "velocity": 100, "channel": 0}
        for pitch, start in specs
    ]


def test_provider_prompt_carries_context() -> None:
    provider = _StubProvider(_note_dicts((63, 0.0), (66, 1.0), (68, 2.0)))
    composer = ProviderMIDIComposer(provider)
    request = MidiCompositionRequest(
        constraints=_constraints(), context_parts=(BASS_CONTEXT,)
    )
    notes = composer.compose(request)
    assert len(notes) == 3
    assert "Other parts in this section already play" in provider.prompt
    assert "- Bass: 4 notes" in provider.prompt
    assert "do not double another part's long note" in provider.prompt


def test_provider_rejects_line_doubling_bass_long_notes() -> None:
    provider = _StubProvider(_note_dicts((39, 0.5), (39, 1.5), (39, 2.5)))
    composer = ProviderMIDIComposer(provider)
    request = MidiCompositionRequest(
        constraints=_constraints(), context_parts=(BASS_CONTEXT,)
    )
    with pytest.raises(ValueError, match="衝突"):
        composer.compose(request)


def test_runtime_preview_builds_context_from_candidate(tmp_path) -> None:
    runtime, _live = _studio(tmp_path)
    parent_id = runtime.generate(SMOKE_TEST, seed=3)["candidate"]["candidate_id"]
    parent = runtime.get_candidate(parent_id)
    section = next(
        clip.section_name for clip in parent.clips if clip.part == "Bass"
    )
    preview = runtime.midi_composer_preview(
        {"key": "D#m", "tempo": 110, "bars": 4, "role": "melody", "section_name": section},
        context_candidate_id=parent_id,
    )
    assert preview["ok"] is True
    parts = {part["part"]: part for part in preview["request"]["context_parts"]}
    assert "Lead" not in parts  # the target part itself is excluded
    assert parts["Bass"]["pitches"]  # pitched parts carry a pitch set
    assert parts["Kick"]["pitches"] == []  # drum parts carry onsets only
    assert parts["Kick"]["onsets"]


def test_runtime_context_unknown_candidate_is_an_error(tmp_path) -> None:
    runtime, _live = _studio(tmp_path)
    preview = runtime.midi_composer_preview(
        {"key": "D#m", "tempo": 110, "bars": 4, "role": "melody", "section_name": "Intro"},
        context_candidate_id="does-not-exist",
    )
    assert preview["ok"] is False
    composed = runtime.compose_midi_part(
        {"key": "D#m", "tempo": 110, "bars": 4, "role": "melody", "section_name": "Intro"},
        context_candidate_id="does-not-exist",
    )
    assert composed["ok"] is False


def test_runtime_compose_reports_collision_measurement(tmp_path) -> None:
    runtime, _live = _studio(tmp_path)
    parent_id = runtime.generate(SMOKE_TEST, seed=3)["candidate"]["candidate_id"]
    parent = runtime.get_candidate(parent_id)
    section = next(
        clip.section_name for clip in parent.clips if clip.part == "Bass"
    )
    composed = runtime.compose_midi_part(
        {"key": "Cm", "tempo": 120, "bars": 2, "role": "melody", "section_name": section},
        context_candidate_id=parent_id,
        seed=8,
    )
    assert composed["ok"] is True
    assert "Bass" in composed["context_parts"]
    ratio = composed["context_collision_ratio"]
    assert ratio is None or 0.0 <= ratio <= 1.0
