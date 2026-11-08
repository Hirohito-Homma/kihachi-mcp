"""Refinement stack for the provider MIDI composer, on the live role system.

Covers scale validation (including Studio part names via COMPOSITION_ENGINE),
the validation-feedback retry loop, wrong-key transposition rescue, and
strict note-field conversion.
"""

import pytest

from kihachi_mcp.models.midi_composer import (
    ComposedMidiNote,
    MidiCompositionRequest,
    MusicalConstraints,
)
from kihachi_mcp.services.midi_composer import ProviderMIDIComposer


class _SequenceProvider:
    """Returns each queued payload in turn and records the prompts it saw."""

    def __init__(self, payloads: list[dict], paid: bool = False) -> None:
        self._payloads = list(payloads)
        self._paid = paid
        self.prompts: list[str] = []

    def capabilities(self):
        return {"structured_output": True, "paid_api": self._paid}

    def generate_structured(self, prompt, schema):
        self.prompts.append(prompt)
        return self._payloads.pop(0)


def _payload(*pitches: int) -> dict:
    return {
        "notes": [
            {"pitch": pitch, "start": index * 0.5, "duration": 0.25, "velocity": 90, "channel": 0}
            for index, pitch in enumerate(pitches)
        ]
    }


def _request(role: str, **kwargs) -> MidiCompositionRequest:
    return MidiCompositionRequest(
        MusicalConstraints(key="D#m", tempo=110, bars=1, role=role, **kwargs)
    )


def test_scale_check_rejects_off_scale_notes_for_role_and_part_name() -> None:
    # 62 (D natural) sits a semitone below the D# tonic in both spellings.
    for role in ("melody", "Lead"):
        provider = _SequenceProvider([_payload(63, 62, 65)] * 3)
        with pytest.raises(ValueError, match="スケール外"):
            ProviderMIDIComposer(provider).compose(_request(role))


def test_scale_check_accepts_in_scale_notes() -> None:
    provider = _SequenceProvider([_payload(63, 65, 66, 70)])
    notes = ProviderMIDIComposer(provider).compose(_request("melody"))
    assert [note.pitch for note in notes] == [63, 65, 66, 70]
    assert len(provider.prompts) == 1


def test_scale_check_exempts_drum_engines_and_part_drums() -> None:
    # 40 (E) is outside D# minor but is a legitimate rhythm pitch.
    for role in ("drums", "percussion", "Kick", "Perc"):
        provider = _SequenceProvider([_payload(36, 40, 42)])
        notes = ProviderMIDIComposer(provider).compose(_request(role))
        assert len(notes) == 3


def test_retry_feeds_the_validation_failure_back() -> None:
    provider = _SequenceProvider([_payload(62), _payload(63, 65)])
    notes = ProviderMIDIComposer(provider).compose(_request("melody"))
    assert [note.pitch for note in notes] == [63, 65]
    assert len(provider.prompts) == 2
    assert "Previous attempt failed validation" in provider.prompts[1]
    assert "スケール外" in provider.prompts[1]


def test_retry_gives_up_after_max_attempts() -> None:
    provider = _SequenceProvider([_payload(62)] * 3)
    with pytest.raises(ValueError, match="3回の生成すべて検証不合格"):
        ProviderMIDIComposer(provider).compose(_request("melody"))
    assert len(provider.prompts) == 3


def test_paid_provider_is_not_retried_by_default() -> None:
    provider = _SequenceProvider([_payload(62), _payload(63)], paid=True)
    with pytest.raises(ValueError, match="スケール外"):
        ProviderMIDIComposer(provider).compose(_request("melody"))
    assert len(provider.prompts) == 1


def test_rescue_transposes_a_line_written_in_the_wrong_key() -> None:
    # A coherent G-minor bass line (root 31) for a D#m request lands on the
    # house bass root 39 after rescue, within a single provider call. The
    # same holds when the role is spelled as the Studio part name.
    for role in ("bass", "Bass"):
        provider = _SequenceProvider([_payload(31, 31, 34, 31, 38, 31, 36, 31)])
        notes = ProviderMIDIComposer(provider).compose(_request(role))
        assert [note.pitch for note in notes] == [39, 39, 42, 39, 46, 39, 44, 39]
        assert len(provider.prompts) == 1


def test_rescue_does_not_apply_to_chromatic_material_or_revisions() -> None:
    provider = _SequenceProvider([_payload(60, 61, 62, 63, 64, 65)] * 3)
    request = MidiCompositionRequest(
        MusicalConstraints(key="Cm", tempo=120, bars=1, role="melody")
    )
    with pytest.raises(ValueError, match="スケール外"):
        ProviderMIDIComposer(provider).compose(request)

    provider = _SequenceProvider([_payload(31, 31, 34, 31, 38, 31, 36, 31)] * 3)
    revision = MidiCompositionRequest(
        MusicalConstraints(key="D#m", tempo=110, bars=1, role="bass"),
        operation="revision",
        source_notes=(ComposedMidiNote(39, 0.0, 0.25, 90),),
    )
    with pytest.raises(ValueError, match="スケール外"):
        ProviderMIDIComposer(provider).compose(revision)


def test_notes_with_missing_fields_are_rejected_loudly() -> None:
    class _MissingStartProvider:
        def capabilities(self):
            return {"structured_output": True}

        def generate_structured(self, prompt, schema):
            return {"notes": [{"pitch": 39, "duration": 0.25, "velocity": 90, "channel": 0}]}

    with pytest.raises(ValueError, match="必須フィールド不足"):
        ProviderMIDIComposer(_MissingStartProvider()).compose(_request("bass"))


def test_prompt_anchors_the_tonic_and_shows_the_envelope() -> None:
    provider = _SequenceProvider([_payload(39, 42)])
    ProviderMIDIComposer(provider).compose(_request("bass"))
    prompt = provider.prompts[0]
    assert "MIDI 39" in prompt
    assert '"notes"' in prompt
