import pytest

from kihachi_mcp.models.midi_candidate import MidiCandidate
from kihachi_mcp.services.local_ai import create_preview, validate_intent
from kihachi_mcp.services.midi_candidate_builder import hat_counts_by_half
from kihachi_mcp.services.studio_interpreter import InterpretationError


def intent():
    return {
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
    }


def test_brief_preserved_and_density_changes_without_live_mutation():
    result = create_preview(
        "125 BPM、Dマイナー、96小節の暗いテクノ。前半はハットを少なく、後半で増やす",
        intent(),
    )
    assert result["intent"]["mood"] == "暗い"
    assert result["intent"]["tempo"] == 125
    assert result["intent"]["key"] == "Dm"
    assert result["intent"]["bars"] == 96
    assert result["songspec"]["length_minutes"] == 96 * 4 / 125
    candidate = MidiCandidate.from_dict(result["candidate"])
    first, second = hat_counts_by_half(candidate)
    assert first < second
    assert result["applied_to_live"] is False
    assert result["musical_quality_claimed"] is False
    drop = next(
        section
        for section in result["candidate"]["brief"]["sections"]
        if section["name"] == "Drop"
    )
    assert drop["start_bar"] == 57


@pytest.mark.parametrize(
    "change",
    [
        {"tempo": True},
        {"bars": 97},
        {"tempo": 999},
        {"key": "garbage"},
        {"command": "delete_tracks"},
    ],
)
def test_model_output_cannot_bypass_validation(change):
    with pytest.raises(InterpretationError):
        validate_intent(intent() | change)
