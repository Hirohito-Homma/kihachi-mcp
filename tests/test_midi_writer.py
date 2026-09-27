from kihachi_mcp.services.brief_parser import extract_explicit
from kihachi_mcp.services.midi_candidate_builder import build_candidate
from kihachi_mcp.services.midi_writer import candidate_to_midi_bytes
from kihachi_mcp.services.studio_interpreter import assemble_brief


def test_midi_file_contains_header_and_four_parts() -> None:
    brief = assemble_brief(
        extract_explicit("32小節 125 BPM Dm"),
        {
            "genre": "tech_house",
            "tempo": 125,
            "bars": 32,
            "key": "Dm",
            "mood": "",
            "hats_first_half": "sparse",
            "hats_second_half": "dense",
            "bass_register": "low",
            "note_density": "normal",
            "drop_start_bar": 17,
            "unhandled": [],
            "ambiguous": [],
        },
    )
    data = candidate_to_midi_bytes(build_candidate(brief, seed=1))
    assert data.startswith(b"MThd")
    assert data[8:10] == (1).to_bytes(2, "big")
    assert data.count(b"MTrk") == 5
