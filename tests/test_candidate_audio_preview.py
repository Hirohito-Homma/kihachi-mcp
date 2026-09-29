"""Audio preview is derived from the candidate and never calls Live."""

from io import BytesIO

import numpy as np
import soundfile as sf

from kihachi_mcp.services.brief_parser import extract_explicit
from kihachi_mcp.services.candidate_audio_preview import (
    candidate_preview_wav,
    preview_window,
)
from kihachi_mcp.services.midi_candidate_builder import build_candidate
from kihachi_mcp.services.studio_interpreter import assemble_brief


def _candidate(text: str):
    intent = {
        "genre": "tech_house", "tempo": 120, "bars": 64, "key": "Dm", "mood": "",
        "hats_first_half": "normal", "hats_second_half": "normal",
        "bass_register": "mid", "note_density": "normal", "drop_start_bar": 0,
        "unhandled": [], "ambiguous": [],
    }
    return build_candidate(assemble_brief(extract_explicit(text), intent), seed=3)


def test_candidate_preview_uses_first_peak_and_contains_audio() -> None:
    pop = _candidate("J-Pop、120 BPM、Cメジャー、64小節、歌うようなリード")
    liquid = _candidate("Liquid Drum & Bass、174 BPM、Dマイナー、64小節、歌うようなリード")
    assert preview_window(pop) == (17, 8, "ChorusA")
    assert preview_window(liquid) == (25, 8, "Drop")
    original = pop.to_dict()
    wav = candidate_preview_wav(pop)
    audio, rate = sf.read(BytesIO(wav))
    assert wav.startswith(b"RIFF") and rate == 22_050
    assert len(audio) == 16 * rate
    assert 0 < float(np.max(np.abs(audio))) <= 0.9
    assert pop.to_dict() == original
