from live_fixtures import snapshot_of

from kihachi_mcp.models.live_contract import OP_LOAD_DRUM_PAD_SAMPLE
from kihachi_mcp.services.brief_parser import extract_explicit
from kihachi_mcp.services.candidate_live_planner import CandidateLivePlanner
from kihachi_mcp.services.drum_samples import (
    CLAP_NOTE,
    CLAVES_NOTE,
    CONGA_HIGH_NOTE,
    CONGA_LOW_NOTE,
    CONGA_MUTE_HIGH_NOTE,
    COWBELL_NOTE,
    FAT_TIGHT_KICK,
    HAT_NOTE,
    IMPACT_NOTE,
    KICK_NOTE,
    OPEN_HAT_NOTE,
    RISER_NOTE,
    SHAKER_NOTE,
    TAMBOURINE_NOTE,
    TIMBALE_LOW_NOTE,
    ensure_drum_samples,
    is_bundled_sample,
    sample_dir,
    sample_path_for_note,
)
from kihachi_mcp.services.live_transport_fake import FakeLiveSet, FakeLiveTransport
from kihachi_mcp.services.midi_candidate_builder import build_candidate
from kihachi_mcp.services.studio_interpreter import assemble_brief


def test_bundled_wavs_are_written_locally() -> None:
    paths = ensure_drum_samples()
    assert paths[KICK_NOTE].is_file()
    assert paths[HAT_NOTE].is_file()
    assert paths[CLAP_NOTE].is_file()
    assert paths[OPEN_HAT_NOTE].is_file()
    assert is_bundled_sample(paths[KICK_NOTE])
    assert is_bundled_sample(sample_dir() / FAT_TIGHT_KICK)
    assert not is_bundled_sample("/tmp/other.wav")


def test_apply_plan_loads_kick_and_hat_samples() -> None:
    brief = assemble_brief(
        extract_explicit("32小節 125 BPM Dマイナー。17小節目からドロップにして"),
        {
            "genre": "tech_house",
            "tempo": 125,
            "bars": 32,
            "key": "Dm",
            "mood": "暗い",
            "hats_first_half": "sparse",
            "hats_second_half": "dense",
            "bass_register": "low",
            "note_density": "normal",
            "drop_start_bar": 17,
            "unhandled": [],
            "ambiguous": [],
        },
    )
    candidate = build_candidate(brief, seed=3, candidate_id="abcd1234ffff")
    transport = FakeLiveTransport(FakeLiveSet(live_version="12.4.3", tempo=128))
    plan = CandidateLivePlanner().create_plan(candidate, snapshot_of(transport))
    sample_ops = [
        operation
        for operation in plan.operations
        if operation.op == OP_LOAD_DRUM_PAD_SAMPLE
    ]
    notes = {operation.arguments["note"] for operation in sample_ops}
    drums = {KICK_NOTE, CLAP_NOTE, HAT_NOTE, OPEN_HAT_NOTE}
    extras = {SHAKER_NOTE, CONGA_HIGH_NOTE, CONGA_LOW_NOTE, RISER_NOTE, IMPACT_NOTE}
    assert drums <= notes <= drums | extras
    assert all(is_bundled_sample(operation.arguments["sample_path"]) for operation in sample_ops)


def test_gm_percussion_notes_have_audible_bundled_samples() -> None:
    import wave

    for note in (
        TAMBOURINE_NOTE,
        COWBELL_NOTE,
        CONGA_MUTE_HIGH_NOTE,
        TIMBALE_LOW_NOTE,
        CLAVES_NOTE,
    ):
        path = sample_path_for_note(note)
        assert path.is_file()
        assert is_bundled_sample(path)
        with wave.open(str(path), "r") as handle:
            frames = handle.readframes(handle.getnframes())
        samples = [
            int.from_bytes(frames[i : i + 2], "little", signed=True)
            for i in range(0, len(frames), 2)
        ]
        assert samples and max(abs(sample) for sample in samples) > 5000
