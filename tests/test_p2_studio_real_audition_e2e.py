"""E2E Verification of Studio Real Audition, Loudness, Evaluations, and Non-destructive Groove generation."""

from __future__ import annotations

import io
import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import soundfile as sf

from kihachi_mcp.services.brief_parser import extract_explicit
from kihachi_mcp.services.studio_interpreter import assemble_brief
from kihachi_mcp.services.studio_runtime import StudioRuntime
from kihachi_mcp.studio.app import StudioApp, _handler_for


def test_studio_real_audition_e2e_flow(tmp_path: Path) -> None:
    """Full lifecycle verification of Task 1 through Task 5."""
    runtime = StudioRuntime(start_bridge=False, candidate_dir=tmp_path)
    app = StudioApp(runtime=runtime)

    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler_for(app))
    port = server.server_address[1]
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    base_url = f"http://127.0.0.1:{port}"

    try:
        # Task 1: UI loading
        with urllib.request.urlopen(f"{base_url}/") as res:
            html = res.read().decode("utf-8")
            assert "A/B 試聴 & パート別ソロ" in html
            assert "Groove パラメータ調整" in html

        # Task 5: Candidate generation with fixed musical brief (Mutation Funk x DUB x Tech House, 110 BPM, D#m)
        brief = assemble_brief(
            extract_explicit("16小節 110 BPM D#マイナー。ジャンルはtech_house。ドロップあり。"),
            {
                "genre": "tech_house",
                "tempo": 110,
                "bars": 16,
                "key": "D#m",
                "mood": "Hypnotic",
                "hats_first_half": "sparse",
                "hats_second_half": "dense",
                "bass_register": "low",
                "note_density": "normal",
                "drop_start_bar": 5,
                "unhandled": [],
                "ambiguous": [],
            },
        )
        mat_res = runtime.materialize(brief, seed=42)
        candidate_id = mat_res["candidate"]["candidate_id"]
        runtime.select(candidate_id)
        candidate = runtime.get_candidate(candidate_id)
        assert candidate is not None
        initial_hash = candidate.note_fingerprint

        # Task 3: A/B and part auditions with uniform loudness normalization
        for part, variant in [("all", "B"), ("all", "A"), ("drums", "B"), ("bass", "B"), ("chords", "B")]:
            url = f"{base_url}/api/candidate/{candidate_id}/preview.wav?part={part}&variant={variant}"
            with urllib.request.urlopen(url) as res:
                wav_bytes = res.read()
                data, _ = sf.read(io.BytesIO(wav_bytes))
                peak = float(abs(data).max())
                assert 0.75 <= peak <= 0.88, f"Peak not normalized for {part} {variant}: {peak}"

        # Task 2 & 3: Loudness metrics & MIDI integrity audit
        metrics_url = f"{base_url}/api/candidate/{candidate_id}/preview-metrics?part=all&variant=B"
        with urllib.request.urlopen(metrics_url) as res:
            metrics = json.loads(res.read().decode("utf-8"))
            assert metrics["ok"] is True
            assert metrics["is_clipped"] is False
            assert metrics["is_silent"] is False
            assert metrics["content_hash"] == initial_hash
            assert metrics["target_midi_protected"] is True

        # Task 1: Evaluation recording (Rating & Notes)
        eval_payload = json.dumps({
            "variant": "B",
            "rating": "B_BETTER",
            "notes": "Slap Bass 54% groove and Dub echo repeats sit deep in pocket",
        }).encode("utf-8")
        req = urllib.request.Request(
            f"{base_url}/api/candidate/{candidate_id}/evaluate",
            data=eval_payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req) as res:
            eval_res = json.loads(res.read().decode("utf-8"))
            assert eval_res["ok"] is True

        # Task 1: Reload persistence verification
        with urllib.request.urlopen(f"{base_url}/api/candidate/{candidate_id}") as res:
            reloaded = json.loads(res.read().decode("utf-8"))
            assert reloaded["evaluation"]["rating"] == "B_BETTER"
            assert "Slap Bass" in reloaded["evaluation"]["notes"]

        # Task 4: Groove adjustment & New candidate generation
        groove_payload = json.dumps({
            "candidate_id": candidate_id,
            "swing": 0.56,
            "ghost_notes": True,
            "syncopation": 0.8,
        }).encode("utf-8")
        groove_req = urllib.request.Request(
            f"{base_url}/api/groove/generate",
            data=groove_payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(groove_req) as res:
            groove_res = json.loads(res.read().decode("utf-8"))
            assert groove_res["ok"] is True
            new_id = groove_res["candidate_id"]
            assert new_id != candidate_id
            assert groove_res["parent_candidate_id"] == candidate_id

        # Verify parent candidate remains unchanged
        parent = runtime.get_candidate(candidate_id)
        assert parent is not None
        assert parent.note_fingerprint == initial_hash
    finally:
        server.shutdown()
        server.server_close()
