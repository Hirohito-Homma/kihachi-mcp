"""The Studio's HTTP API on loopback, with a fake Live Set behind it."""

import json
import re
import threading
from datetime import UTC, datetime, timedelta
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


def test_live_overview_reads_set_without_mutation(studio) -> None:
    port, _runtime, live = studio
    live.tempo = 126
    live.signature_numerator = 4
    live.signature_denominator = 4
    live.create_midi_track("Drums")
    live.create_scene("Intro")

    status, result = _call(port, "GET", "/api/live/overview")
    assert status == 200
    assert result["ok"] is True
    assert result["live_changed"] is False
    assert result["live_set"]["tempo"] == 126
    assert result["live_set"]["time_signature"] == "4/4"
    assert len(result["live_set"]["tracks"]) == 1
    assert result["live_set"]["tracks"][0]["name"] == "Drums"
    assert result["live_set"]["scene_count"] == 1


def test_live_candidate_uses_overview_and_keeps_live_unchanged(studio, monkeypatch) -> None:
    port, runtime, live = studio
    live.tempo = 124
    received = []
    monkeypatch.setattr(runtime, "queue_generate", lambda brief: received.append(brief) or {"ok": True, "started": True})
    status, result = _call(port, "POST", "/api/live/candidate", {"brief": "D minor、8小節のテクノ"})
    assert status == 200
    assert result["ok"] is True
    assert result["live_changed"] is False
    assert result["live_context"]["sources"]["tempo"] == "live"
    assert received == ["124 BPM。D minor、8小節のテクノ"]
    assert live.tracks == []


def test_live_candidate_rejects_openai_provider(studio, monkeypatch) -> None:
    port, runtime, live = studio
    assert runtime.update_settings({"ai_provider": "openai"}, persist=False)["ok"]
    queued = []
    monkeypatch.setattr(runtime, "queue_generate", lambda brief: queued.append(brief) or {"ok": True})
    status, result = _call(port, "POST", "/api/live/candidate", {"brief": "D minor、8小節のテクノ"})
    assert status == 400
    assert result["ok"] is False
    assert "ローカルAI設定" in result["error"]
    assert queued == []
    assert live.tracks == []


def test_live_candidate_rejects_empty_or_excessive_brief(studio, monkeypatch) -> None:
    port, runtime, live = studio
    queued = []
    monkeypatch.setattr(runtime, "queue_generate", lambda brief: queued.append(brief) or {"ok": True})

    for bad_brief in ("", "   \n  ", "a" * 4001):
        status, result = _call(port, "POST", "/api/live/candidate", {"brief": bad_brief})
        assert status == 400
        assert result["ok"] is False
        assert "1〜4000文字" in result["error"]

    assert queued == []
    assert live.tracks == []


def test_openai_connection_route_checks_saved_provider(studio, monkeypatch) -> None:
    from kihachi_mcp.services.ai_provider import OpenAIProvider

    port, runtime, _live = studio
    status, result = _call(port, "GET", "/api/ai/connection")
    assert status == 200 and result["ok"] is False
    assert runtime.update_settings({"ai_provider": "openai"}, persist=False)["ok"]
    monkeypatch.setattr(OpenAIProvider, "test_connection", lambda self: {"ok": True, "model": self.model})
    status, result = _call(port, "GET", "/api/ai/connection")
    assert status == 200 and result == {"ok": True, "model": "gpt-6.1-sol"}


def test_http_generate_previews_paid_cost_before_start(studio) -> None:
    port, runtime, _live = studio
    assert runtime.update_settings({"ai_provider": "openai"}, persist=False)["ok"]
    status, result = _call(port, "POST", "/api/generate", {"brief": "124 BPM、D# minor"})
    assert status == 200 and result["ok"] is False
    assert result["cost"]["estimate"]["estimated_jpy"] > 0
    assert runtime.job_status()["state"] == "idle"


def test_http_regenerate_enforces_paid_confirmation_and_cost_guards(
    studio, tmp_path: Path, monkeypatch
) -> None:
    from kihachi_mcp.services.ai_cost_control import MonthlyCostGuard, estimate_cost
    from kihachi_mcp.services.ai_provider import OpenAIProvider

    port, runtime, _live = studio
    runtime._cost_guard = MonthlyCostGuard(tmp_path / "regen_ledger.json")
    candidate_id = runtime.generate(SMOKE_TEST, seed=3)["candidate"]["candidate_id"]
    assert runtime.update_settings({"ai_provider": "openai"}, persist=False)["ok"]

    provider_calls = []
    monkeypatch.setattr(
        OpenAIProvider,
        "interpret",
        lambda self, brief: provider_calls.append(brief) or pytest.fail("provider must not run"),
    )

    # 1 & 2. Unconfirmed /api/regenerate requires paid confirmation and never starts a job or calls provider
    status, unconfirmed = _call(port, "POST", "/api/regenerate", {"candidate_id": candidate_id})
    assert status == 200 and unconfirmed["ok"] is False
    assert "有料再生成" in unconfirmed["error"]
    assert unconfirmed["cost"]["estimate"]["estimated_jpy"] > 0
    assert provider_calls == []

    # 3. Confirmed /api/regenerate forwards confirmed_paid=True to runtime
    regenerated_calls = []
    orig_regenerate = runtime.regenerate
    monkeypatch.setattr(
        runtime,
        "regenerate",
        lambda cid, confirmed_paid=False: regenerated_calls.append((cid, confirmed_paid))
        or {"ok": True},
    )
    status, confirmed_res = _call(
        port, "POST", "/api/regenerate", {"candidate_id": candidate_id, "confirmed_paid": True}
    )
    assert status == 200 and confirmed_res["ok"] is True
    for _ in range(50):
        if regenerated_calls:
            break
        threading.Event().wait(0.01)
    assert regenerated_calls == [(candidate_id, True)]
    assert provider_calls == []
    monkeypatch.setattr(runtime, "regenerate", orig_regenerate)

    # 4. Monthly cost limit exceeded blocks execution before provider even when confirmed_paid=True over HTTP
    assert runtime.update_settings({"monthly_ai_limit_jpy": 100}, persist=False)["ok"]
    runtime._cost_guard.record(
        {"input_tokens": 500_000, "output_tokens": 0},
        estimate_cost("gpt-6.1-sol", 500_000, 0),
    )
    status, _queued_over_limit = _call(
        port, "POST", "/api/regenerate", {"candidate_id": candidate_id, "confirmed_paid": True}
    )
    assert status == 200
    for _ in range(50):
        if runtime.job_status()["state"] == "error":
            break
        threading.Event().wait(0.01)
    assert runtime.job_status()["state"] == "error"
    assert "月間AI利用上限" in runtime.job_status()["error"]
    assert provider_calls == []

    # 5. Connection test over HTTP sanitizes secrets, Authorization headers, and external raw error bodies
    class FakeErrorResponse:
        status = 401

        def read(self):
            return b'{"error":{"code":"invalid_api_key","message":"Incorrect API key provided: sk-secret-999"}}'

    class FakeErrorConn:
        def __init__(self, host, timeout):
            pass

        def request(self, method, path, headers):
            pass

        def getresponse(self):
            return FakeErrorResponse()

        def close(self):
            pass

    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret-999")
    monkeypatch.setattr("kihachi_mcp.services.ai_provider.HTTPSConnection", FakeErrorConn)
    conn_status, conn_body = _call(port, "GET", "/api/ai/connection")
    assert conn_status == 200 and conn_body["ok"] is False
    serialized = json.dumps(conn_body, ensure_ascii=False)
    assert "sk-secret-999" not in serialized
    assert "Bearer" not in serialized
    assert "Incorrect API key provided" not in serialized


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
    runtime.dry_run(candidate_id)
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

def test_music_plan_preview_does_not_touch_live(studio) -> None:
    port, _runtime, live = studio
    before = list(live.tracks)
    status, preview = _call(port, "POST", "/api/music-plan/preview", {})
    assert status == 200
    assert preview["ok"] is True
    assert preview["paid_api"] is False
    assert preview["executed"] is False
    assert preview["live_changed"] is False
    assert preview["save"] is False
    assert preview["music"]["tempo"] == 110
    assert preview["music"]["key"] == "D#m"
    assert preview["dry_run"]["playback"] == "UNKNOWN"
    assert preview["operations"] == [
        "create_scene",
        "create_midi_track",
        "create_session_clip",
        "replace_clip_notes",
    ]
    assert live.tracks == before


def test_all_parts_plan_preview_does_not_touch_live(studio) -> None:
    port, _runtime, live = studio
    before = list(live.tracks)
    status, preview = _call(
        port,
        "POST",
        "/api/music-plan/preview",
        {"constraints": {"role": "全パート", "key": "D#m", "tempo": 124, "bars": 4}},
    )
    assert status == 200
    assert preview["ok"] is True
    assert preview["paid_api"] is False
    assert preview["executed"] is False
    assert preview["live_changed"] is False
    assert preview["dry_run"]["changes"]["tracks"] == 15
    assert preview["approved"] is False
    assert "direction" not in preview
    status, directed = _call(
        port,
        "POST",
        "/api/music-plan/preview",
        {
            "constraints": {
                "role": "全パート",
                "key": "D#m",
                "tempo": 124,
                "bars": 4,
                "genre": "mutation_funk",
                "mood": "暗い",
                "prompt": "キックは短め。ミューテーションファンクを作って",
            }
        },
    )
    assert status == 200
    assert directed["ok"] is True
    assert directed["direction"]["genre"] == "mutation_funk"
    assert directed["direction"]["mood"] == "暗い"
    assert "ミューテーションファンク" in directed["direction"]["prompt"]
    assert "方向の記録" in "".join(directed["music"]["warnings"])
    assert directed["live_changed"] is False
    assert live.tracks == before
    status, approved = _call(
        port, "POST", "/api/music-plan/approve", {"plan_hash": preview["plan_hash"]}
    )
    assert status == 200
    assert approved["ok"] is True
    assert approved["approved"] is True
    assert approved["executed"] is False
    assert approved["live_changed"] is False
    assert live.tracks == before
    status, missing = _call(port, "POST", "/api/music-plan/approve", {"plan_hash": "missing"})
    assert missing["ok"] is False
    assert live.tracks == before


def test_music_plan_reject_creates_no_token_and_does_not_touch_live(studio) -> None:
    port, runtime, live = studio
    before = list(live.tracks)
    _status, preview = _call(
        port,
        "POST",
        "/api/music-plan/preview",
        {"constraints": {"role": "bass", "key": "D#m", "tempo": 110, "bars": 2}},
    )
    status, rejected = _call(
        port, "POST", "/api/music-plan/reject", {"plan_hash": preview["plan_hash"]}
    )
    assert status == 200
    assert rejected["ok"] is True
    assert rejected["rejected"] is True
    assert rejected["approved"] is False
    assert rejected["executed"] is False
    assert rejected["live_changed"] is False
    assert runtime._gate.holds(preview["plan_hash"]) is False
    assert live.tracks == before


def test_music_plan_verify_does_not_complete_an_unread_plan(studio) -> None:
    port, runtime, live = studio
    before = list(live.tracks)
    _status, preview = _call(
        port,
        "POST",
        "/api/music-plan/preview",
        {"constraints": {"role": "bass", "key": "D#m", "tempo": 110, "bars": 2}},
    )
    status, early = _call(port, "POST", "/api/music-plan/verify", {"plan_hash": preview["plan_hash"]})
    assert status == 200
    assert early["ok"] is False
    assert early["state"] == "APPROVAL_REQUIRED"
    assert early["executed"] is False
    _call(port, "POST", "/api/music-plan/approve", {"plan_hash": preview["plan_hash"]})
    status, rebound = _call(port, "POST", "/api/music-plan/verify", {"plan_hash": preview["plan_hash"]})
    assert status == 200
    assert rebound["ok"] is False
    assert rebound["state"] == "APPROVAL_REQUIRED"
    assert rebound["plan_hash"] != preview["plan_hash"]
    assert rebound["live_changed"] is False
    assert "もう一度承認" in rebound["reply"]
    assert runtime._gate.holds(rebound["plan_hash"]) is False
    assert live.tracks == before
    _call(port, "POST", "/api/music-plan/approve", {"plan_hash": rebound["plan_hash"]})
    status, finished = _call(port, "POST", "/api/music-plan/verify", {"plan_hash": rebound["plan_hash"]})
    assert status == 200
    assert finished["ok"] is True
    assert finished["state"] == "HUMAN_AUDITION_REQUIRED"
    assert finished["musical_quality_claimed"] is False
    assert finished["live_changed"] is False
    assert "実機は見ていません" in finished["reply"]
    assert live.tracks == before


def test_music_plan_live_send_explains_an_expired_read(studio) -> None:
    port, runtime, live = studio
    preview = _bass_plan(port)
    _call(port, "POST", "/api/music-plan/approve", {"plan_hash": preview["plan_hash"]})
    _status, bound = _call(port, "POST", "/api/music-plan/live", {"plan_hash": preview["plan_hash"]})
    _call(port, "POST", "/api/music-plan/approve", {"plan_hash": bound["plan_hash"]})
    runtime._gate._clock = lambda: datetime.now(UTC) + timedelta(seconds=1000)
    _status, late = _call(port, "POST", "/api/music-plan/live", {"plan_hash": bound["plan_hash"]})
    assert late["executed"] is False
    assert "15分が過ぎました" in late["reply"]
    assert "the plan expired" not in late["reply"]
    assert live.tracks == []
    _status, reread = _call(port, "POST", "/api/music-plan/live", {"plan_hash": bound["plan_hash"]})
    assert reread["plan_hash"] != bound["plan_hash"]


def _bass_plan(port: int) -> dict:
    _status, preview = _call(
        port,
        "POST",
        "/api/music-plan/preview",
        {"constraints": {"role": "bass", "key": "D#m", "tempo": 110, "bars": 2}},
    )
    return preview


def test_music_plan_reaches_live_only_after_reading_and_a_second_approval(studio) -> None:
    port, runtime, live = studio
    live.add_track("User Drums")
    live.add_scene("Verse")
    preview = _bass_plan(port)
    _status, early = _call(port, "POST", "/api/music-plan/live", {"plan_hash": preview["plan_hash"]})
    assert early["state"] == "APPROVAL_REQUIRED"
    assert early["executed"] is False
    assert [track["name"] for track in live.tracks] == ["User Drums"]

    _call(port, "POST", "/api/music-plan/approve", {"plan_hash": preview["plan_hash"]})
    _status, bound = _call(port, "POST", "/api/music-plan/live", {"plan_hash": preview["plan_hash"]})
    assert bound["plan_hash"] != preview["plan_hash"]
    assert bound["live_changed"] is False
    assert "もう一度承認" in bound["reply"]
    assert runtime._gate.holds(bound["plan_hash"]) is False
    assert [track["name"] for track in live.tracks] == ["User Drums"]

    _status, unapproved = _call(port, "POST", "/api/music-plan/live", {"plan_hash": bound["plan_hash"]})
    assert unapproved["executed"] is False
    assert len(live.tracks) == 1

    _call(port, "POST", "/api/music-plan/approve", {"plan_hash": bound["plan_hash"]})
    _status, sent = _call(port, "POST", "/api/music-plan/live", {"plan_hash": bound["plan_hash"]})
    assert sent["ok"] is True
    assert sent["state"] == "HUMAN_AUDITION_REQUIRED"
    assert "COMPLETED" not in sent["states"]
    assert sent["live_changed"] is True
    assert sent["save"] is False
    assert sent["musical_quality_claimed"] is False
    assert "音源は入れていない" in sent["reply"]
    assert live.tracks[1]["device_names"] == []
    assert live.tracks[0]["name"] == "User Drums"
    assert live.tracks[1]["name"].startswith("KIHACHI Bass ")
    assert live.tracks[1]["name"].endswith("[KIHACHI]")
    assert live.scenes[0]["name"] == "Verse"
    assert len(live.scenes) == 2
    assert live.session_clips[(1, 1)].notes

    _status, again = _call(port, "POST", "/api/music-plan/live", {"plan_hash": bound["plan_hash"]})
    assert again["executed"] is False
    assert len(live.tracks) == 2


def test_music_plan_is_not_read_into_live_while_it_plays(studio) -> None:
    port, _runtime, live = studio
    preview = _bass_plan(port)
    _call(port, "POST", "/api/music-plan/approve", {"plan_hash": preview["plan_hash"]})
    live.is_playing = True
    _status, result = _call(port, "POST", "/api/music-plan/live", {"plan_hash": preview["plan_hash"]})
    assert result["executed"] is False
    assert result["plan_hash"] == preview["plan_hash"]
    assert "再生中" in result["reply"]
    assert live.tracks == []


def test_music_plan_does_not_read_live_when_disconnected(studio) -> None:
    port, runtime, live = studio
    preview = _bass_plan(port)
    _call(port, "POST", "/api/music-plan/approve", {"plan_hash": preview["plan_hash"]})
    runtime._snapshot = lambda: (None, {"error": "接続がありません"})
    _status, result = _call(port, "POST", "/api/music-plan/live", {"plan_hash": preview["plan_hash"]})
    assert result["executed"] is False
    assert result["live_changed"] is False
    assert "読めませんでした" in result["reply"]
    assert live.tracks == []


def test_music_plan_keeps_live_binding_when_playback_blocks_the_send(studio) -> None:
    port, _runtime, live = studio
    preview = _bass_plan(port)
    _call(port, "POST", "/api/music-plan/approve", {"plan_hash": preview["plan_hash"]})
    _status, bound = _call(port, "POST", "/api/music-plan/live", {"plan_hash": preview["plan_hash"]})
    _call(port, "POST", "/api/music-plan/approve", {"plan_hash": bound["plan_hash"]})
    live.is_playing = True
    _status, blocked = _call(port, "POST", "/api/music-plan/live", {"plan_hash": bound["plan_hash"]})
    assert blocked["executed"] is False
    assert blocked["plan_hash"] == bound["plan_hash"]
    assert "承認は残しています" in blocked["reply"]
    assert live.tracks == []
    live.is_playing = False
    _status, sent = _call(port, "POST", "/api/music-plan/live", {"plan_hash": bound["plan_hash"]})
    assert sent["ok"] is True
    assert sent["live_changed"] is True
    assert live.tracks[0]["name"].startswith("KIHACHI Bass ")


def test_music_plan_is_not_read_into_live_while_it_records(studio) -> None:
    port, _runtime, live = studio
    preview = _bass_plan(port)
    _call(port, "POST", "/api/music-plan/approve", {"plan_hash": preview["plan_hash"]})
    live.is_recording = True
    _status, result = _call(port, "POST", "/api/music-plan/live", {"plan_hash": preview["plan_hash"]})
    assert result["executed"] is False
    assert result["plan_hash"] == preview["plan_hash"]
    assert "録音中" in result["reply"]
    assert live.tracks == []


def test_music_plan_keeps_live_binding_when_recording_blocks_the_send(studio) -> None:
    port, _runtime, live = studio
    preview = _bass_plan(port)
    _call(port, "POST", "/api/music-plan/approve", {"plan_hash": preview["plan_hash"]})
    _status, bound = _call(port, "POST", "/api/music-plan/live", {"plan_hash": preview["plan_hash"]})
    _call(port, "POST", "/api/music-plan/approve", {"plan_hash": bound["plan_hash"]})
    live.is_recording = True
    _status, blocked = _call(port, "POST", "/api/music-plan/live", {"plan_hash": bound["plan_hash"]})
    assert blocked["executed"] is False
    assert blocked["plan_hash"] == bound["plan_hash"]
    assert "承認は残しています" in blocked["reply"]
    assert live.tracks == []
    live.is_recording = False
    _status, sent = _call(port, "POST", "/api/music-plan/live", {"plan_hash": bound["plan_hash"]})
    assert sent["ok"] is True
    assert sent["live_changed"] is True
    assert live.tracks[0]["name"].startswith("KIHACHI Bass ")


def test_music_plan_keeps_live_binding_when_another_apply_is_running(studio) -> None:
    port, runtime, live = studio
    preview = _bass_plan(port)
    _call(port, "POST", "/api/music-plan/approve", {"plan_hash": preview["plan_hash"]})
    _status, bound = _call(port, "POST", "/api/music-plan/live", {"plan_hash": preview["plan_hash"]})
    _call(port, "POST", "/api/music-plan/approve", {"plan_hash": bound["plan_hash"]})
    assert runtime._apply_lock.acquire(blocking=False)
    try:
        _status, busy = _call(port, "POST", "/api/music-plan/live", {"plan_hash": bound["plan_hash"]})
    finally:
        runtime._apply_lock.release()
    assert busy["executed"] is False
    assert "実行中" in busy["reply"]
    assert live.tracks == []
    _status, sent = _call(port, "POST", "/api/music-plan/live", {"plan_hash": bound["plan_hash"]})
    assert sent["ok"] is True
    assert sent["live_changed"] is True
    assert live.tracks[0]["name"].startswith("KIHACHI Bass ")


def test_music_plan_does_not_place_a_duplicate_track_name(studio) -> None:
    port, _runtime, live = studio
    preview = _bass_plan(port)
    _call(port, "POST", "/api/music-plan/approve", {"plan_hash": preview["plan_hash"]})
    _status, bound = _call(port, "POST", "/api/music-plan/live", {"plan_hash": preview["plan_hash"]})
    _call(port, "POST", "/api/music-plan/approve", {"plan_hash": bound["plan_hash"]})
    _call(port, "POST", "/api/music-plan/live", {"plan_hash": bound["plan_hash"]})
    again = _bass_plan(port)
    _call(port, "POST", "/api/music-plan/approve", {"plan_hash": again["plan_hash"]})
    _status, duplicate = _call(port, "POST", "/api/music-plan/live", {"plan_hash": again["plan_hash"]})
    assert duplicate["executed"] is False
    assert "同じ名前" in duplicate["reply"]
    assert duplicate["live_changed"] is False
    assert len(live.tracks) == 1


def test_music_plan_live_send_stops_when_the_set_changed(studio) -> None:
    port, _runtime, live = studio
    preview = _bass_plan(port)
    _call(port, "POST", "/api/music-plan/approve", {"plan_hash": preview["plan_hash"]})
    _status, bound = _call(port, "POST", "/api/music-plan/live", {"plan_hash": preview["plan_hash"]})
    _call(port, "POST", "/api/music-plan/approve", {"plan_hash": bound["plan_hash"]})
    live.add_track("Added By Hand")
    _status, result = _call(port, "POST", "/api/music-plan/live", {"plan_hash": bound["plan_hash"]})
    assert result["executed"] is False
    assert "Set が変わりました" in result["reply"]
    assert [track["name"] for track in live.tracks] == ["Added By Hand"]
    _status, reread = _call(port, "POST", "/api/music-plan/live", {"plan_hash": bound["plan_hash"]})
    assert reread["plan_hash"] != bound["plan_hash"]
    assert "もう一度承認" in reread["reply"]


def test_jarvis_change_request_becomes_a_revision_proposal_without_live(studio) -> None:
    port, runtime, live = studio
    candidate_id = runtime.generate(SMOKE_TEST, seed=3)["candidate"]["candidate_id"]
    runtime.select(candidate_id)
    hats = next(clip for clip in runtime._lookup(candidate_id).clips_for_part("Hats") if clip.notes)
    first, last = hats.start_bar, hats.start_bar + hats.length_bars - 1
    _status, proposal = _call(
        port, "POST", "/api/jarvis/revision", {"utterance": f"{first}-{last}小節のハットを減らして"}
    )
    assert proposal["ok"] is True
    assert proposal["note_count_after"] < proposal["note_count_before"]
    assert proposal["requires_approval"] is True
    assert proposal["live_changed"] is False
    assert runtime.selected_id() == candidate_id
    assert live.tracks == []
    _status, kick = _call(
        port, "POST", "/api/jarvis/revision", {"utterance": f"{first}-{last}小節のキックを増やして"}
    )
    assert kick["ok"] is False and "ハットだけ" in kick["error"]
    _status, later = _call(port, "POST", "/api/jarvis/revision", {"utterance": "後半のハットを増やして"})
    assert later["ok"] is True
    assert later["note_count_after"] > later["note_count_before"]
    assert later["revision"]["after"][0].startswith("17–32 小節のHats")
    assert live.tracks == []


def test_approval_center_lists_pending_actions_and_changes_nothing(studio) -> None:
    port, runtime, live = studio
    _status, empty = _call(port, "GET", "/api/approvals")
    assert empty == {"ok": True, "music_plans": [], "revisions": [], "live_changed": False}

    candidate_id = runtime.generate(SMOKE_TEST, seed=3)["candidate"]["candidate_id"]
    runtime.select(candidate_id)
    _status, proposal = _call(port, "POST", "/api/jarvis/revision", {"utterance": "後半のハットを減らして"})
    preview = _bass_plan(port)
    _call(port, "POST", "/api/music-plan/approve", {"plan_hash": preview["plan_hash"]})

    _status, listed = _call(port, "GET", "/api/approvals")
    assert [item["revision_id"] for item in listed["revisions"]] == [proposal["revision"]["revision_id"]]
    assert listed["revisions"][0]["note_count_after"] < listed["revisions"][0]["note_count_before"]
    assert [plan["plan_hash"] for plan in listed["music_plans"]] == [preview["plan_hash"]]
    assert listed["music_plans"][0]["tracks"] == ["Bass [KIHACHI]"]
    assert 0 < listed["music_plans"][0]["remaining_seconds"] <= 900
    assert runtime.selected_id() == candidate_id
    assert live.tracks == []

    _call(port, "POST", "/api/revision/decide", {"revision_id": proposal["revision"]["revision_id"], "accept": False})
    _status, after = _call(port, "GET", "/api/approvals")
    assert after["revisions"] == []

    _status, bound = _call(port, "POST", "/api/music-plan/live", {"plan_hash": preview["plan_hash"]})
    _status, waiting = _call(port, "GET", "/api/approvals")
    states = {plan["plan_hash"]: (plan["approved"], plan["live_bound"]) for plan in waiting["music_plans"]}
    assert states == {bound["plan_hash"]: (False, True)}
    assert live.tracks == []

    runtime._gate._clock = lambda: datetime.now(UTC) + timedelta(seconds=1000)
    _status, stale = _call(port, "GET", "/api/approvals")
    assert stale["music_plans"] == []


def test_approval_center_keeps_a_live_bound_plan_when_previews_overflow(studio) -> None:
    port, _runtime, live = studio
    preview = _bass_plan(port)
    _call(port, "POST", "/api/music-plan/approve", {"plan_hash": preview["plan_hash"]})
    _status, bound = _call(port, "POST", "/api/music-plan/live", {"plan_hash": preview["plan_hash"]})
    for _ in range(8):
        _bass_plan(port)
    _status, waiting = _call(port, "GET", "/api/approvals")
    states = {plan["plan_hash"]: (plan["approved"], plan["live_bound"]) for plan in waiting["music_plans"]}
    assert states == {bound["plan_hash"]: (False, True)}
    assert live.tracks == []


def test_jarvis_route_reports_the_open_candidate_without_writing(studio) -> None:
    port, runtime, live = studio
    status, idle = _call(port, "GET", "/api/jarvis")
    assert status == 200 and idle["state"] == "IDLE" and idle["live_changed"] is False
    status, empty = _call(port, "POST", "/api/jarvis/turn", {"utterance": "今の曲どうなってる？"})
    assert status == 200 and empty["state"] == "COMPLETED" and empty["executed"] is False
    assert live.tracks == []
    runtime.generate(SMOKE_TEST, seed=3)
    status, result = _call(port, "POST", "/api/jarvis/turn", {"utterance": "今の曲どうなってる？"})
    assert result["state"] == "COMPLETED"
    assert result["context"]["tempo"] == 120
    status, blocked = _call(
        port, "POST", "/api/jarvis/turn", {"utterance": "512から544のベースを少し減らして"}
    )
    assert blocked["state"] == "APPROVAL_REQUIRED"
    assert blocked["executed"] is False
    assert blocked["live_changed"] is False
    assert live.tracks == []


def test_jarvis_midi_clip_routes_read_facts_and_preview_without_writing(studio, monkeypatch) -> None:
    port, _runtime, live = studio
    notes = [
        {"pitch": 36, "start_time": 0.0, "duration": 0.5, "velocity": 110},
        {"pitch": 36, "start_time": 1.0, "duration": 0.5, "velocity": 100},
        {"pitch": 36, "start_time": 4.0, "duration": 0.5, "velocity": 105},
        {"pitch": 36, "start_time": 5.0, "duration": 0.5, "velocity": 95},
    ]

    def fake_clip_notes(track_index=None, track_name=None, clip_index=0):
        if track_index == 0 and clip_index == 0:
            return {
                "ok": True,
                "status": "SUCCESS",
                "complete": True,
                "live_changed": False,
                "track_index": 0,
                "track": "KICK",
                "clip_index": 0,
                "clip": "Kick 2Bar",
                "length_beats": 8.0,
                "notes": [dict(n) for n in notes],
                "note_count": len(notes),
                "truncated": False,
            }
        return {
            "ok": False,
            "status": "PARTIAL",
            "complete": False,
            "live_changed": False,
            "truncated": True,
            "notes": [dict(n) for n in notes],
            "note_count": 5000,
            "length_beats": 8.0,
        }

    monkeypatch.setattr("kihachi_mcp.services.jarvis.clip_review.get_midi_clip_notes", fake_clip_notes)

    status, read_res = _call(
        port,
        "POST",
        "/api/jarvis/turn",
        {"utterance": "トラック0のクリップ0を評価して"},
    )
    assert status == 200
    assert read_res["ok"] is True
    assert read_res["state"] == "COMPLETED"
    assert read_res["complete"] is True
    assert read_res["live_changed"] is False
    assert read_res["executed"] is False
    assert read_res["clip_evaluation"]["note_count"] == 4
    assert read_res["clip_evaluation"]["bar_notes"] == [2, 2]
    assert read_res["clip_evaluation"]["timbre"] == "UNKNOWN"
    assert read_res["clip_preview"]["original_notes"] == notes

    status, preview_res = _call(
        port,
        "POST",
        "/api/jarvis/clip-preview",
        {"track_index": 0, "clip_index": 0, "direction": "reduce_density", "scope": "later_section"},
    )
    assert status == 200
    assert preview_res["ok"] is True
    assert preview_res["state"] == "APPROVAL_REQUIRED"
    assert preview_res["clip_preview"]["original_notes"] == notes
    assert preview_res["clip_preview"]["note_count_after"] == 3
    assert preview_res["clip_preview"]["applied_to_live"] is False
    assert preview_res["live_changed"] is False

    status, partial_res = _call(
        port,
        "POST",
        "/api/jarvis/clip-preview",
        {"track_index": 0, "clip_index": 1},
    )
    assert status == 200
    assert partial_res["ok"] is False
    assert partial_res["state"] == "UNKNOWN"
    assert partial_res["complete"] is False
    assert partial_res["clip_evaluation"] is None
    assert partial_res["clip_preview"] is None
    assert live.tracks == []


def test_http_confirm_target_binds_candidate_and_invalidates_on_fingerprint_change(
    studio,
) -> None:
    """B.1: POST /api/ableton/confirm-target binds to candidate and current Set fingerprint,
    and becomes invalid when Set fingerprint changes (including Arrangement changes).
    """
    from kihachi_mcp.services.live_transport_fake import FakeArrangementClip

    port, runtime, live = studio
    live.set_name = ""  # UNKNOWN set name
    candidate_id = runtime.generate(SMOKE_TEST, seed=3)["candidate"]["candidate_id"]
    other_id = runtime.generate(SMOKE_TEST, seed=4)["candidate"]["candidate_id"]
    transport = runtime._transport

    # Unknown candidate_id is rejected
    status, bad_conf = _call(
        port, "POST", "/api/ableton/confirm-target", {"candidate_id": "unknown-cand-id"}
    )
    assert status == 200 and bad_conf["ok"] is False
    assert "候補がありません" in bad_conf["error"]

    # Approve and Dry Run on UNKNOWN Set
    assert _call(port, "POST", "/api/approve", {"candidate_id": candidate_id})[1]["ok"] is True
    status, dry1 = _call(port, "POST", "/api/ableton/dry-run", {"candidate_id": candidate_id})
    assert status == 200 and dry1["ok"] is True
    assert dry1["diff_report"]["live_set_name"] == "UNKNOWN"
    assert dry1["diff_report"]["requires_target_confirmation"] is True
    assert dry1["diff_report"]["target_confirmed"] is False

    # Confirm target via HTTP
    status, conf = _call(
        port, "POST", "/api/ableton/confirm-target", {"candidate_id": candidate_id}
    )
    assert status == 200 and conf["ok"] is True
    assert conf["candidate_id"] == candidate_id
    assert conf["target_confirmed"] is True
    assert conf["set_fingerprint"] == dry1["diff_report"]["live_set_fingerprint"]

    # Confirmation is bound only to candidate_id, not other_id
    assert runtime.is_target_confirmed(other_id, conf["set_fingerprint"]) is False

    # Dry run reflects target_confirmed = True
    status, dry2 = _call(port, "POST", "/api/ableton/dry-run", {"candidate_id": candidate_id})
    assert status == 200 and dry2["diff_report"]["target_confirmed"] is True

    # Mutate Live Set (add track + Arrangement clip) -> fingerprint changes, invalidating confirmation
    track = live.add_track("External Track")
    live.arrangement_clips.append(
        FakeArrangementClip(
            track_index=track["index"],
            name="External Clip",
            start_beats=0.0,
            length_beats=16.0,
        )
    )
    status, send_after_change = _call(
        port,
        "POST",
        "/api/ableton/send",
        {"candidate_id": candidate_id, "confirmed": True},
    )
    assert status == 200 and send_after_change["ok"] is False
    assert "Live Setの状態または接続先が変更されました" in send_after_change["error"]
    assert candidate_id not in runtime._target_confirmed_records
    assert transport.applied_operation_ids == []


def test_http_delivery_status_reports_state_and_rejects_unknown_or_invalid_id(
    studio,
) -> None:
    """B.2: POST /api/ableton/delivery-status returns accurate state for known candidates
    and never returns ok=True for unknown or invalid candidate IDs.
    """
    port, runtime, _live = studio
    candidate_id = runtime.generate(SMOKE_TEST, seed=3)["candidate"]["candidate_id"]
    transport = runtime._transport

    # Unknown or empty IDs must return ok=False
    for invalid_id in ("", "nonexistent_id", "../bad/path"):
        status, bad = _call(
            port, "POST", "/api/ableton/delivery-status", {"candidate_id": invalid_id}
        )
        assert status == 200 and bad["ok"] is False
        assert "候補がありません" in bad["error"]

    # Valid candidate before send is IDLE
    status, idle = _call(
        port, "POST", "/api/ableton/delivery-status", {"candidate_id": candidate_id}
    )
    assert status == 200 and idle["ok"] is True
    assert idle["candidate_id"] == candidate_id
    assert idle["delivery_status"] == "IDLE"
    assert transport.applied_operation_ids == []

    # Approve, Dry Run, and Send -> delivery_status transitions to VERIFIED
    _call(port, "POST", "/api/approve", {"candidate_id": candidate_id})
    _call(port, "POST", "/api/ableton/dry-run", {"candidate_id": candidate_id})
    status, sent = _call(
        port,
        "POST",
        "/api/ableton/send",
        {"candidate_id": candidate_id, "confirmed": True},
    )
    assert status == 200 and sent["ok"] is True
    status, verified = _call(
        port, "POST", "/api/ableton/delivery-status", {"candidate_id": candidate_id}
    )
    assert status == 200 and verified["ok"] is True
    assert verified["candidate_id"] == candidate_id
    assert verified["delivery_status"] == "VERIFIED"


def test_http_send_refused_without_dry_run_or_without_unknown_set_target_confirmation(
    studio,
) -> None:
    """B.3 & B.4: POST /api/ableton/send over HTTP refuses when Dry Run was not run
    or when Set name is UNKNOWN without target confirmation, with 0 Live mutations.
    """
    port, runtime, live = studio
    transport = runtime._transport
    candidate_id = runtime.generate(SMOKE_TEST, seed=3)["candidate"]["candidate_id"]
    _call(port, "POST", "/api/approve", {"candidate_id": candidate_id})

    # B.3: Send without Dry Run -> refused, 0 mutations
    status, no_dry = _call(
        port,
        "POST",
        "/api/ableton/send",
        {"candidate_id": candidate_id, "confirmed": True},
    )
    assert status == 200 and no_dry["ok"] is False
    assert "Dry Runが実行されていません" in no_dry["error"]
    assert transport.applied_operation_ids == []
    assert live.tracks == []

    # B.4: Set name is UNKNOWN and target is not confirmed -> refused, 0 mutations
    live.set_name = ""
    status, dry = _call(
        port, "POST", "/api/ableton/dry-run", {"candidate_id": candidate_id}
    )
    assert status == 200 and dry["ok"] is True
    assert dry["safety_verdict"] == "PASS"
    assert dry["diff_report"]["requires_target_confirmation"] is True
    assert dry["diff_report"]["target_confirmed"] is False

    status, unconfirmed_target = _call(
        port,
        "POST",
        "/api/ableton/send",
        {"candidate_id": candidate_id, "confirmed": True},
    )
    assert status == 200 and unconfirmed_target["ok"] is False
    assert unconfirmed_target.get("requires_target_confirmation") is True
    assert "Live Set名がUNKNOWN" in unconfirmed_target["error"]
    assert transport.applied_operation_ids == []
    assert live.tracks == []


def test_http_send_never_retries_from_partial_unknown_or_applying_states(
    studio,
) -> None:
    """B.5: Partial application, crash-recovered UNKNOWN, and in-flight APPLYING states
    strictly block resend over HTTP with 0 additional Live mutations.
    """
    port, runtime, _live = studio
    transport = runtime._transport

    # 1. PARTIAL state from mid-flight disconnect
    cand_partial = runtime.generate(SMOKE_TEST, seed=11)["candidate"]["candidate_id"]
    _call(port, "POST", "/api/approve", {"candidate_id": cand_partial})
    _call(port, "POST", "/api/ableton/dry-run", {"candidate_id": cand_partial})
    transport.disconnect_after = 2
    status, partial_send = _call(
        port,
        "POST",
        "/api/ableton/send",
        {"candidate_id": cand_partial, "confirmed": True},
    )
    transport.disconnect_after = None
    assert status == 200 and partial_send["ok"] is False
    assert partial_send["delivery_status"] == "PARTIAL"
    ops_after_partial = list(transport.applied_operation_ids)
    assert len(ops_after_partial) == 2

    status, status_partial = _call(
        port, "POST", "/api/ableton/delivery-status", {"candidate_id": cand_partial}
    )
    assert status_partial["ok"] is True and status_partial["delivery_status"] == "PARTIAL"

    # Attempt Dry Run and Send again on PARTIAL candidate -> blocked, 0 new operations
    status, dry_after_partial = _call(
        port, "POST", "/api/ableton/dry-run", {"candidate_id": cand_partial}
    )
    assert dry_after_partial["safety_verdict"] == "FAIL"
    assert dry_after_partial["can_send"] is False
    status, resend_partial = _call(
        port,
        "POST",
        "/api/ableton/send",
        {"candidate_id": cand_partial, "confirmed": True},
    )
    assert resend_partial["ok"] is False
    assert transport.applied_operation_ids == ops_after_partial

    # 2. Crash-recovered APPLYING -> UNKNOWN state on disk
    cand_crash = runtime.generate(SMOKE_TEST, seed=12)["candidate"]["candidate_id"]
    _call(port, "POST", "/api/approve", {"candidate_id": cand_crash})
    audit_path = runtime._delivery_audit_path(cand_crash)
    assert audit_path is not None
    audit_path.write_text(
        json.dumps({"candidate_id": cand_crash, "delivery_status": "APPLYING"}),
        encoding="utf-8",
    )
    runtime._delivery_audit_records.pop(cand_crash, None)

    status, status_unknown = _call(
        port, "POST", "/api/ableton/delivery-status", {"candidate_id": cand_crash}
    )
    assert status == 200 and status_unknown["ok"] is True
    assert status_unknown["delivery_status"] == "UNKNOWN"
    assert status_unknown.get("recovered_from_crash") is True

    status, dry_unknown = _call(
        port, "POST", "/api/ableton/dry-run", {"candidate_id": cand_crash}
    )
    assert dry_unknown["safety_verdict"] == "FAIL"
    assert dry_unknown["can_send"] is False
    status, send_unknown = _call(
        port,
        "POST",
        "/api/ableton/send",
        {"candidate_id": cand_crash, "confirmed": True},
    )
    assert send_unknown["ok"] is False
    assert transport.applied_operation_ids == ops_after_partial

    # 3. In-memory APPLYING state
    cand_applying = runtime.generate(SMOKE_TEST, seed=13)["candidate"]["candidate_id"]
    _call(port, "POST", "/api/approve", {"candidate_id": cand_applying})
    _call(port, "POST", "/api/ableton/dry-run", {"candidate_id": cand_applying})
    runtime._delivery_audit_records[cand_applying] = {
        "candidate_id": cand_applying,
        "delivery_status": "APPLYING",
    }
    status, send_applying = _call(
        port,
        "POST",
        "/api/ableton/send",
        {"candidate_id": cand_applying, "confirmed": True},
    )
    assert status == 200 and send_applying["ok"] is False
    assert "すでに適用を試行済みです" in send_applying["error"]
    assert transport.applied_operation_ids == ops_after_partial


def test_index_html_disables_apply_when_change_tempo_or_skip_instruments_toggled() -> None:
    """Focus Area C: Changing #change-tempo or #skip-instruments immediately disables #apply."""
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    assert '$("change-tempo").onchange = () => { $("apply").disabled = true; };' in html
    assert '$("skip-instruments").onchange = () => { $("apply").disabled = true; };' in html
