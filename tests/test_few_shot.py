"""Few-shot examples for provider composition (2026-11-19).

An accepted clip from another song is shown to the provider as a style
example, transposed into the request key. Without a few-shot candidate
id, requests and prompts are exactly as before the feature existed.
"""

from types import SimpleNamespace

from test_studio_runtime import _runtime

from kihachi_mcp.models.midi_candidate import CandidateClip
from kihachi_mcp.models.midi_composer import (
    ComposedMidiNote,
    FewShotExample,
    MidiCompositionRequest,
    MusicalConstraints,
)
from kihachi_mcp.services.midi_composer import ProviderMIDIComposer
from kihachi_mcp.services.session_pattern_builder import MidiNote, root_pitch
from kihachi_mcp.services.studio_runtime import _candidate_few_shot


class _Recorder:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    def capabilities(self):
        return {"structured_output": True}

    def generate_structured(self, prompt, schema):
        self.prompts.append(prompt)
        return {
            "notes": [
                {"pitch": 43, "start": 0, "duration": 0.25, "velocity": 90, "channel": 0}
            ]
        }


def _bass_request(examples: tuple[FewShotExample, ...]) -> MidiCompositionRequest:
    return MidiCompositionRequest(
        MusicalConstraints(key="Gm", tempo=124, bars=4, role="bass"),
        few_shot_examples=examples,
    )


def test_prompt_carries_example_notes_and_no_copy_guidance() -> None:
    example = FewShotExample(
        part="Bass",
        section_name="Drop",
        source_key="D#m",
        notes=(ComposedMidiNote(43, 0.0, 0.25, 96), ComposedMidiNote(50, 1.5, 0.5, 88)),
    )
    provider = _Recorder()
    notes = ProviderMIDIComposer(provider).compose(_bass_request((example,)))
    assert notes
    prompt = provider.prompts[0]
    assert "Example of an accepted 'Bass' part" in prompt
    assert '"pitch": 43' in prompt
    assert "Do not copy the example note for note" in prompt
    payload = _bass_request((example,)).to_dict()
    assert payload["few_shot_examples"][0]["source_key"] == "D#m"
    assert len(payload["few_shot_examples"][0]["notes"]) == 2


def test_prompt_without_examples_is_unchanged() -> None:
    provider = _Recorder()
    ProviderMIDIComposer(provider).compose(_bass_request(()))
    assert "Example of an accepted" not in provider.prompts[0]
    assert "few_shot_examples" not in _bass_request(()).to_dict()


def _fake_candidate(part: str, pitches: tuple[int, ...], key: str = "D#m"):
    clip = CandidateClip(
        part=part,
        section_name="Drop",
        start_bar=9,
        length_bars=4,
        notes=tuple(
            MidiNote(pitch=pitch, start_beats=float(i), duration_beats=0.25, velocity=96)
            for i, pitch in enumerate(pitches)
        ),
    )
    return SimpleNamespace(
        brief=SimpleNamespace(key=SimpleNamespace(value=key)), clips=(clip,)
    )


def test_candidate_few_shot_transposes_pitched_parts_into_target_key() -> None:
    candidate = _fake_candidate("Bass", (39, 46))
    examples = _candidate_few_shot(candidate, "Drop", "Bass", "Gm")
    assert len(examples) == 1
    # D#m -> Gm is +4 semitones: D#3 39 -> G3 43, A#3 46 -> D4 50.
    assert [note.pitch for note in examples[0].notes] == [43, 50]
    assert examples[0].source_key == "D#m"
    gm_pcs = {7, 9, 10, 0, 2, 3, 5}
    assert all(note.pitch % 12 in gm_pcs for note in examples[0].notes)


def test_candidate_few_shot_never_transposes_percussion() -> None:
    candidate = _fake_candidate("Perc", (60, 67, 72))
    examples = _candidate_few_shot(candidate, "Drop", "Perc", "Gm")
    assert [note.pitch for note in examples[0].notes] == [60, 67, 72]


def test_candidate_few_shot_missing_clip_returns_empty() -> None:
    candidate = _fake_candidate("Bass", (39,))
    assert _candidate_few_shot(candidate, "Intro", "Bass", "Gm") == ()
    assert _candidate_few_shot(candidate, "Drop", "Lead", "Gm") == ()


def test_runtime_preview_attaches_transposed_example(tmp_path) -> None:
    runtime, candidate, _transport = _runtime(tmp_path)
    bass_clip = next(clip for clip in candidate.clips if clip.part == "Bass")
    source_key = str(candidate.brief.key.value)
    preview = runtime.midi_composer_preview(
        {
            "key": "Gm",
            "tempo": 124,
            "bars": bass_clip.length_bars,
            "role": "bass",
            "section_name": bass_clip.section_name,
        },
        few_shot_candidate_id=candidate.candidate_id,
    )
    assert preview["ok"] is True
    examples = preview["request"]["few_shot_examples"]
    assert len(examples) == 1
    assert examples[0]["part"] == "Bass"
    assert examples[0]["source_key"] == source_key
    diff = (root_pitch("Gm") - root_pitch(source_key)) % 12
    shift = (diff + 6) % 12 - 6
    expected = [
        note.pitch + shift
        for note in bass_clip.notes
        if 0 <= note.pitch + shift <= 127
    ]
    assert [note["pitch"] for note in examples[0]["notes"]] == expected


def test_runtime_preview_unknown_few_shot_candidate_fails(tmp_path) -> None:
    runtime, _candidate_obj, _transport = _runtime(tmp_path)
    preview = runtime.midi_composer_preview(
        {"key": "Gm", "tempo": 124, "bars": 4, "role": "bass", "section_name": "Drop"},
        few_shot_candidate_id="doesnotexist",
    )
    assert preview["ok"] is False
    assert "実例" in preview["error"]


def test_runtime_preview_without_few_shot_has_no_examples(tmp_path) -> None:
    runtime, _candidate_obj, _transport = _runtime(tmp_path)
    preview = runtime.midi_composer_preview(
        {"key": "Gm", "tempo": 124, "bars": 4, "role": "bass", "section_name": "Drop"}
    )
    assert preview["ok"] is True
    assert "few_shot_examples" not in preview["request"]
