"""The Studio's HTTP API on loopback, with a fake Live Set behind it."""

import json
import re
import threading
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from test_studio_workflow import SMOKE_TEST, _studio

from kihachi_mcp.services.reference_library import ReferenceLibrary
from kihachi_mcp.services.sample_catalog import SampleCatalog
from kihachi_mcp.studio.app import STATIC_DIR, StudioApp, _handler_for


@pytest.fixture()
def studio(tmp_path: Path):
    runtime, live = _studio(tmp_path)
    app = StudioApp(
        runtime=runtime,
        reference_library=ReferenceLibrary(tmp_path / "references"),
        sample_catalog=SampleCatalog(tmp_path / "samples.sqlite3"),
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler_for(app))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server.server_address[1], runtime, live
    server.shutdown()
    server.server_close()


def _call(port: int, method: str, path: str, body=None, headers=None):
    connection = HTTPConnection("127.0.0.1", port, timeout=30)
    payload = json.dumps(body) if body is not None else None
    connection.request(method, path, payload, {"Content-Type": "application/json", **(headers or {})})
    response = connection.getresponse()
    data = response.read()
    connection.close()
    return response.status, json.loads(data) if data.startswith(b"{") else data


def test_health_no_longer_crashes(studio) -> None:
    port, _runtime, _live = studio
    status, health = _call(port, "GET", "/api/health")
    assert status == 200
    assert {item["name"] for item in health["statuses"]} >= {"Ollama", "Ableton Live"}


def test_candidate_audio_preview_is_read_only(studio) -> None:
    port, runtime, live = studio
    candidate_id = runtime.generate(SMOKE_TEST, seed=3)["candidate"]["candidate_id"]
    status, wav = _call(port, "GET", f"/api/candidate/{candidate_id}/preview.wav")
    assert status == 200 and wav.startswith(b"RIFF") and len(wav) > 1000
    assert live.tracks == []
    missing, body = _call(port, "GET", "/api/candidate/missing/preview.wav")
    assert missing == 404 and body["ok"] is False


def test_sample_replacement_http_requires_preview_and_confirmation(tmp_path: Path) -> None:
    runtime, live = _studio(tmp_path)
    catalog = SampleCatalog(tmp_path / "sample-catalog")
    source = tmp_path / "samples"
    source.mkdir()
    replacement = source / "Heavy Kick.wav"
    replacement.write_bytes(b"RIFFheavy")
    catalog.index(str(source))
    sample_id = catalog.search("kick")["results"][0]["sample_id"]
    candidate_id = runtime.generate(SMOKE_TEST, seed=3)["candidate"]["candidate_id"]
    assert runtime.apply(candidate_id, confirmed=True)["ok"] is True
    app = StudioApp(
        runtime=runtime,
        reference_library=ReferenceLibrary(tmp_path / "references"),
        sample_catalog=catalog,
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler_for(app))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        _status, preview = _call(
            port, "POST", "/api/samples/replace-preview", {"sample_id": sample_id}
        )
        assert preview["ok"] is True and preview["applied_to_live"] is False
        kick = next(track for track in live.tracks if "Kick" in track["name"])
        assert kick["occupied_pads"][0]["sample_path"] != str(replacement)
        _status, refused = _call(
            port,
            "POST",
            "/api/samples/replace",
            {"replacement_id": preview["replacement_id"], "confirmed": False},
        )
        assert refused["ok"] is False
        _status, applied = _call(
            port,
            "POST",
            "/api/samples/replace",
            {"replacement_id": preview["replacement_id"], "confirmed": True},
        )
        assert applied["ok"] is True
        assert kick["occupied_pads"][0]["sample_path"] == str(replacement)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_full_studio_flow_over_http(studio) -> None:
    port, runtime, live = studio
    candidate_id = runtime.generate(SMOKE_TEST, seed=3)["candidate"]["candidate_id"]
    _status, projects = _call(port, "GET", "/api/projects")
    assert projects["projects"][0]["candidate_id"] == candidate_id
    _status, project = _call(port, "GET", f"/api/project?candidate_id={candidate_id}")
    assert project["songspec"]["tempo"] == 120
    assert project["arrangement"]["sections"][0]["start_bar"] == 1
    _status, review = _call(port, "POST", "/api/review", {"candidate_id": candidate_id})
    assert review["dimensions"]
    _status, refused = _call(port, "POST", "/api/apply", {"candidate_id": candidate_id, "confirmed": True})
    assert refused["ok"] is False and live.tracks == []
    _status, proposal = _call(
        port, "POST", "/api/revision", {"candidate_id": candidate_id, "scopes": ["drums"], "bars": [9, 16]}
    )
    assert proposal["ok"] is True
    _status, decided = _call(
        port, "POST", "/api/revision/decide", {"revision_id": proposal["revision"]["revision_id"], "accept": True}
    )
    child = decided["selected_candidate_id"]
    _status, approved = _call(port, "POST", "/api/approve", {"candidate_id": child})
    assert approved["approved"] is True
    _status, dry = _call(port, "POST", "/api/ableton/dry-run", {"candidate_id": child, "change_tempo": True})
    assert dry["can_send"] is True and live.tracks == []
    _status, sent = _call(
        port, "POST", "/api/ableton/send", {"candidate_id": child, "confirmed": True, "change_tempo": True}
    )
    assert sent["verification"]["status"] == "PROJECT READY"
    _status, verified = _call(port, "POST", "/api/ableton/verify", {"candidate_id": child})
    assert verified["ok"] is True


def test_bad_bar_range_is_a_readable_error(studio) -> None:
    port, runtime, _live = studio
    candidate_id = runtime.generate(SMOKE_TEST, seed=3)["candidate"]["candidate_id"]
    status, body = _call(port, "POST", "/api/revision", {"candidate_id": candidate_id, "bars": ["a"]})
    assert status == 400 and "33-49" in body["error"]


def test_foreign_origin_cannot_post(studio) -> None:
    port, runtime, live = studio
    candidate_id = runtime.generate(SMOKE_TEST, seed=3)["candidate"]["candidate_id"]
    runtime.approve(candidate_id)
    status, _body = _call(
        port,
        "POST",
        "/api/ableton/send",
        {"candidate_id": candidate_id, "confirmed": True},
        {"Origin": "http://evil.example"},
    )
    assert status == 403
    assert live.tracks == []


def test_unexpected_errors_hide_the_traceback(studio, monkeypatch) -> None:
    port, runtime, _live = studio

    def boom(*_args, **_kwargs):
        raise RuntimeError("secret internals")

    monkeypatch.setattr(runtime, "list_projects", boom)
    status, body = _call(port, "GET", "/api/projects")
    assert status == 500
    assert "Traceback" not in json.dumps(body, ensure_ascii=False)
    assert "診断" in body["error"]


def test_settings_diagnostics_and_ollama_routes(studio) -> None:
    port, _runtime, _live = studio
    _status, settings = _call(port, "GET", "/api/settings")
    assert settings["settings"]["ai_provider"] == "deterministic"
    _status, diagnostics = _call(port, "GET", "/api/diagnostics")
    names = {check["name"] for check in diagnostics["checks"]}
    assert {"Ollama", "Model", "Ableton Live", "Remote Script", "Storage"} <= names
    live = next(check for check in diagnostics["checks"] if check["name"] == "Ableton Live")
    assert live["status"] == "PASS"
    _status, ollama = _call(port, "GET", "/api/ollama")
    assert "installed_models" in ollama
    _status, capabilities = _call(port, "GET", "/api/ableton/capabilities")
    assert capabilities["bridge_ready"] is True
    assert "warp" not in capabilities["executable_capability_ids"]


def test_midi_composer_previews_then_generates_without_touching_live(studio) -> None:
    port, _runtime, live = studio
    constraints = {"key": "D#m", "tempo": 124, "bars": 2, "role": "bass", "syncopation": 0.7}
    _status, preview = _call(port, "POST", "/api/midi/preview", {"constraints": constraints})
    _status, generated = _call(port, "POST", "/api/midi/compose", {"constraints": constraints})
    assert preview["ok"] is True and preview["paid_api"] is False
    assert generated["ok"] is True and generated["note_count"] > 0
    assert generated["applied_to_live"] is False
    assert live.tracks == []


def test_composer_adoption_uses_existing_revision_approval_flow(studio) -> None:
    port, runtime, live = studio
    candidate_id = runtime.generate(SMOKE_TEST, seed=3)["candidate"]["candidate_id"]
    constraints = {"key": "Dm", "tempo": 120, "bars": 2, "role": "bass", "syncopation": 0.8}
    _status, generated = _call(port, "POST", "/api/midi/compose", {"constraints": constraints})
    _status, proposal = _call(
        port,
        "POST",
        "/api/midi/adopt-preview",
        {"candidate_id": candidate_id, "role": "bass", "pattern_bars": 2, "notes": generated["notes"]},
    )
    assert proposal["ok"] is True and proposal["other_parts_unchanged"] is True
    assert runtime.selected_id() == candidate_id
    _status, accepted = _call(
        port,
        "POST",
        "/api/revision/decide",
        {"revision_id": proposal["revision"]["revision_id"], "accept": True},
    )
    assert accepted["selected_candidate_id"] != candidate_id
    assert live.tracks == []


def test_measure_route_reads_audio_files_only(studio, tmp_path: Path) -> None:
    port, _runtime, _live = studio
    wav = tmp_path / "mix.wav"
    seconds = np.arange(48000 * 5) / 48000
    tone = 0.1 * np.sin(2 * np.pi * 1000 * seconds)
    sf.write(wav, np.stack([tone, tone], axis=1), 48000)

    _status, report = _call(port, "POST", "/api/measure", {"path": f"'{wav}'"})
    assert report["ok"] is True
    assert report["integrated_lufs"] < -15
    assert report["verdict"]

    secret = tmp_path / "config.json"
    secret.write_text("{}")
    _status, refused = _call(port, "POST", "/api/measure", {"path": str(secret)})
    assert refused["ok"] is False
    assert "WAV" in refused["error"] or ".wav" in refused["error"]


def test_retune_route_needs_parts_and_previews_before_sending(studio) -> None:
    port, runtime, live = studio
    candidate_id = runtime.generate(SMOKE_TEST, seed=3)["candidate"]["candidate_id"]
    runtime.approve(candidate_id)
    runtime.send_to_ableton(candidate_id, confirmed=True)
    runtime.apply_effects(candidate_id, confirmed=True)
    _status, empty = _call(port, "POST", "/api/ableton/retune", {"candidate_id": candidate_id})
    assert empty["ok"] is False

    before = [list(track["device_names"]) for track in live.tracks]
    _status, preview = _call(
        port, "POST", "/api/ableton/retune", {"candidate_id": candidate_id, "parts": ["Kick"]}
    )
    assert preview["ok"] is True and preview["preview"] is True
    gain = next(row for row in preview["chains"] if row["parameter"] == "2 Gain A")
    assert gain["setting"] == "-3 dB"
    _status, sent = _call(
        port,
        "POST",
        "/api/ableton/retune",
        {"candidate_id": candidate_id, "parts": ["Kick"], "confirmed": True},
    )
    assert sent["receipt"]["status"] == "verified"
    assert [list(track["device_names"]) for track in live.tracks] == before


def test_page_calls_only_routes_the_server_has() -> None:
    page = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    source = (Path(__file__).resolve().parents[1] / "src/kihachi_mcp/studio/app.py").read_text()
    routes = set(re.findall(r'"(/api/[a-z/_-]+)', page)) | set(re.findall(r"'(/api/[a-z/_-]+)", page))
    for route in routes:
        base = route.rstrip("/")
        assert base in source or base.startswith(("/api/candidate", "/api/references")), route
    for element in (
        "review-view",
        "revision-view",
        "arrangement-bar",
        "diagnostics-rows",
        "verify-result",
        "setting-model",
        "retune-parts",
        "master-retune",
        "measure-path",
        "midi-composer-view",
        "composer-preview",
        "composer-run",
        "composer-adopt",
    ):
        assert f'id="{element}"' in page
