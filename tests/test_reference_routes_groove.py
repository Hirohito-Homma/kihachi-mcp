"""Tests for groove analysis card HTTP routes."""

import http.client
import json
import math
import threading
import wave
from http.server import ThreadingHTTPServer
from pathlib import Path

import numpy as np
import pytest

from kihachi_mcp.services.reference_library import ReferenceLibrary
from kihachi_mcp.services.reference_sources import ReferenceSources
from kihachi_mcp.services.sample_catalog import SampleCatalog
from kihachi_mcp.studio.app import StudioApp, _handler_for


def _create_synthetic_wav(path: Path, bpm: float = 120.0, seconds: float = 8.0) -> Path:
    sample_rate = 22050
    total_samples = int(sample_rate * seconds)
    audio = np.zeros(total_samples, dtype=np.float32)
    beat_samples = int(sample_rate * (60.0 / bpm))

    kick_len = int(sample_rate * 0.1)
    t_kick = np.arange(kick_len) / sample_rate
    kick_pulse = np.sin(2 * math.pi * 50 * t_kick) * np.exp(-t_kick * 30)

    pos = 0
    while pos + kick_len < total_samples:
        audio[pos : pos + kick_len] += kick_pulse * 0.7
        pos += beat_samples

    audio = audio / np.max(np.abs(audio)) * 0.8
    int_data = (audio * 32767).astype("<i2")
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(int_data.tobytes())
    return path


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    class NoLive:
        def __init__(self, candidate_dir):
            self._candidate_dir = candidate_dir
            self._candidates = {}
            self._selected_id = ""

        def _store(self, candidate):
            self._candidates[candidate.candidate_id] = candidate
            self._selected_id = candidate.candidate_id

        def select(self, candidate_id):
            self._selected_id = candidate_id
            return {"ok": True}

        def get_candidate(self, candidate_id):
            return self._candidates.get(candidate_id)

        def __getattr__(self, name):
            raise AssertionError(f"Route test must not call Live: {name}")

    candidate_dir = tmp_path / "candidates"
    candidate_dir.mkdir(parents=True, exist_ok=True)
    library = ReferenceLibrary(
        tmp_path / "library", lambda _: {"metrics": {}, "warnings": []}
    )
    sample_catalog = SampleCatalog(tmp_path / "sample-catalog")
    app = StudioApp(
        runtime=NoLive(candidate_dir),
        reference_library=library,
        reference_sources=ReferenceSources({}),
        sample_catalog=sample_catalog,
    )
    server_obj = ThreadingHTTPServer(("127.0.0.1", 0), _handler_for(app))
    thread = threading.Thread(target=server_obj.serve_forever, daemon=True)
    thread.start()
    try:
        yield server_obj, library, tmp_path
    finally:
        server_obj.shutdown()
        server_obj.server_close()
        thread.join(timeout=3)


def request(server_obj, method, path, body=None, headers=None):
    client = http.client.HTTPConnection("127.0.0.1", server_obj.server_port, timeout=10)
    all_headers = {
        "Origin": f"http://127.0.0.1:{server_obj.server_port}",
        "Content-Type": "application/json",
        **(headers or {}),
    }
    client.request(method, path, body=body, headers=all_headers)
    response = client.getresponse()
    raw = response.read()
    client.close()
    return response.status, json.loads(raw.decode("utf-8")) if raw else {}


def request_raw(server_obj, method, path, body=None, headers=None):
    client = http.client.HTTPConnection("127.0.0.1", server_obj.server_port, timeout=10)
    all_headers = {
        "Origin": f"http://127.0.0.1:{server_obj.server_port}",
        **(headers or {}),
    }
    client.request(method, path, body=body, headers=all_headers)
    response = client.getresponse()
    raw = response.read()
    client.close()
    return response.status, raw


def test_groove_analysis_card_lifecycle_via_http(server):
    server_obj, _library, tmp_path = server
    wav_path = _create_synthetic_wav(tmp_path / "test_kick.wav", bpm=120.0, seconds=8.0)

    # 1. Create analysis card
    create_body = json.dumps({
        "path": str(wav_path),
        "title": "Minimal Techno Groove",
        "start_sec": 0.0,
        "duration_sec": 8.0,
    })
    status, res = request(server_obj, "POST", "/api/references/analysis-card/create", body=create_body)
    assert status == 200
    assert res["ok"] is True
    card = res["card"]
    card_id = card["card_id"]
    assert card["user_title"] == "Minimal Techno Groove"
    assert card["bpm"]["value"] is not None

    # 2. List cards
    status, list_res = request(server_obj, "GET", "/api/references/analysis-cards")
    assert status == 200
    assert list_res["ok"] is True
    assert any(c["card_id"] == card_id for c in list_res["cards"])

    # 3. Get single card
    status, get_res = request(server_obj, "GET", f"/api/references/analysis-cards/{card_id}")
    assert status == 200
    assert get_res["ok"] is True
    assert get_res["card"]["card_id"] == card_id

    # 4. Generate variants from card
    gen_body = json.dumps({
        "card_id": card_id,
        "key": "Fm",
        "bars": 8,
    })
    status, var_res = request(server_obj, "POST", "/api/references/analysis-card/generate-variants", body=gen_body)
    assert status == 200
    assert var_res["ok"] is True
    assert len(var_res["variants"]) == 3
    assert var_res["variants"][0]["variant_id"] == "A"
    assert var_res["variants"][1]["variant_id"] == "B"
    assert var_res["variants"][2]["variant_id"] == "C"

    # 4.5. Promote variant to candidate for Studio review (Safe: does NOT mutate Live)
    promote_body = json.dumps({
        "card_id": card_id,
        "variant_id": "B",
        "key": "Fm",
        "bars": 64,
    })
    status, prom_res = request(server_obj, "POST", "/api/references/analysis-card/promote-candidate", body=promote_body)
    assert status == 200, f"Promote failed: {prom_res}"
    assert prom_res["ok"] is True
    assert prom_res["candidate_id"] is not None
    assert prom_res["variant_id"] == "B"
    assert "Liveへの変更は一切行われていません" in prom_res["live_mutation_warning"]
    assert prom_res["stored"] is True
    assert prom_res["clip_count"] > 0

    # 5. Evaluate card and save feedback
    eval_body = json.dumps({
        "card_id": card_id,
        "rating": "5",
        "adopted_variant": "variant_b",
        "notes": "Perfect groove spacing.",
        "manual_overrides": {"tempo": 122.0},
    })
    status, eval_res = request(server_obj, "POST", "/api/references/analysis-card/evaluate", body=eval_body)
    assert status == 200
    assert eval_res["ok"] is True
    assert eval_res["card"]["user_feedback"]["human_rating"] == "5"
    assert eval_res["card"]["user_feedback"]["adopted"] is True

    # 6. Check Gemini status endpoint (Safe report, no keys leaked)
    status, gemini_status = request(server_obj, "GET", "/api/references/gemini/status")
    assert status == 200
    assert "provider" in gemini_status
    assert gemini_status["provider"] == "gemini"

    # 7. Enhance with Gemini (when key is missing, safely falls back without 500 error)
    enh_body = json.dumps({"card_id": card_id, "genre": "techno", "gemini_mode": "mode_a"})
    status, enh_res = request(server_obj, "POST", "/api/references/analysis-card/enhance-gemini", body=enh_body)
    assert status == 200
    assert "fallback_used" in enh_res or enh_res.get("ok") is True

    # 8. Create another card with Gemini interpretation enabled, safely handles missing key or offline
    create_with_gemini = json.dumps({
        "path": str(wav_path),
        "title": "Groove Card with Interpretation",
        "start_sec": 0.0,
        "duration_sec": 4.0,
        "use_gemini": True,
    })
    status, res_b = request(server_obj, "POST", "/api/references/analysis-card/create", body=create_with_gemini)
    assert status == 200
    assert res_b["ok"] is True
    assert res_b["card"]["user_title"] == "Groove Card with Interpretation"
    assert res_b["card"]["gemini_status"] in ("success", "failed_fallback", "skipped_missing_key")


def test_references_page_contains_candidate_promotion_ui(server):
    server_obj, _library, _tmp_path = server
    status, raw = request_raw(server_obj, "GET", "/references")
    assert status == 200
    html = raw.decode("utf-8")
    assert "Studio候補へ登録" in html
    assert "promote-candidate" in html
    assert "未登録" in html
    assert "候補登録済み" in html
    assert "Liveへの変更はありません" in html
    assert "Studio制作画面で開く" in html
    assert "※再登録すると別の候補IDで新規登録されます" in html


def test_promote_candidate_validation_and_idempotency(server):
    server_obj, _library, tmp_path = server
    wav_path = _create_synthetic_wav(tmp_path / "test_kick2.wav", bpm=124.0, seconds=4.0)

    # 1. Validation error: missing card_id
    status, bad_res = request(server_obj, "POST", "/api/references/analysis-card/promote-candidate", body=json.dumps({}))
    assert status == 400
    assert bad_res["ok"] is False
    assert "card_id" in bad_res["error"]

    # 2. Validation error: nonexistent card_id
    status, not_found_res = request(
        server_obj,
        "POST",
        "/api/references/analysis-card/promote-candidate",
        body=json.dumps({"card_id": "nonexistent_card_123"}),
    )
    assert status == 400 or status == 404
    assert not_found_res["ok"] is False

    # 3. Create a valid card
    status, res = request(
        server_obj,
        "POST",
        "/api/references/analysis-card/create",
        body=json.dumps({"path": str(wav_path), "title": "Idempotency Test Card", "start_sec": 0.0, "duration_sec": 4.0}),
    )
    assert status == 200
    card_id = res["card"]["card_id"]

    # 4. Promote first time
    prom_body = json.dumps({"card_id": card_id, "variant_id": "A", "bars": 64})
    status, p1 = request(server_obj, "POST", "/api/references/analysis-card/promote-candidate", body=prom_body)
    assert status == 200, f"Promote first time failed: {p1}"
    assert p1["ok"] is True
    id1 = p1["candidate_id"]

    # 5. Promote second time (honest idempotency: generates distinct registered candidate)
    status, p2 = request(server_obj, "POST", "/api/references/analysis-card/promote-candidate", body=prom_body)
    assert status == 200
    assert p2["ok"] is True
    id2 = p2["candidate_id"]
    assert id1 != id2
    assert "Liveへの変更は一切行われていません" in p2["live_mutation_warning"]


def test_references_page_contains_streamlined_import_ui(server):
    server_obj, _library, _tmp_path = server
    status, raw = request_raw(server_obj, "GET", "/references")
    assert status == 200
    html = raw.decode("utf-8")
    assert "音源ファイルを選ぶ" in html
    assert "必要な情報だけ確認する" in html
    assert "登録する" in html
    assert "参考音源を登録" in html
    assert "ローカル解析して保存" in html
    assert "file-dropzone" in html
    assert "title-candidate-badge" in html
    assert "audio-file-error" in html
    assert "title-error" in html
    assert "source-url-error" in html
    assert "license-error" in html
    assert "rights-error" in html
    assert "source-extra-fields" in html
    assert "import-advanced" in html
    assert "import-success-box" in html
    assert "import-go-library" in html
    assert "import-go-analysis" in html


def test_import_upload_source_validation_rules(server):
    from urllib.parse import urlencode
    server_obj, library, tmp_path = server
    wav_path = _create_synthetic_wav(tmp_path / "test_import.wav", bpm=120.0, seconds=2.0)
    wav_bytes = wav_path.read_bytes()

    # 1. Local upload with minimal fields (license defaults to 自作)
    local_meta = {
        "title": "My Local Groove",
        "source": "local",
        "kind": "track",
        "license": "自作",
        "rights_confirmed": True,
    }
    query = urlencode({"filename": "test_import.wav", "metadata": json.dumps(local_meta)})
    headers = {"Content-Type": "application/octet-stream"}
    status, payload = request_raw(server_obj, "POST", f"/api/references/upload?{query}", body=wav_bytes, headers=headers)
    assert status == 200
    res = json.loads(payload.decode("utf-8"))
    assert res["ok"] is True
    assert res["entry"]["title"] == "My Local Groove"
    assert res["entry"]["source"] == "local"

    # 2. Freesound upload missing source_url -> 400
    bad_fs_meta = {
        "title": "Freesound Beat",
        "source": "freesound",
        "kind": "loop",
        "license": "CC0",
        "source_url": "",
        "rights_confirmed": True,
    }
    query = urlencode({"filename": "test_fs.wav", "metadata": json.dumps(bad_fs_meta)})
    status, payload = request_raw(server_obj, "POST", f"/api/references/upload?{query}", body=wav_bytes, headers=headers)
    assert status == 400
    res = json.loads(payload.decode("utf-8"))
    assert res["ok"] is False
    assert "出典サービスの作品ページURLが必要です" in res["error"]

    # 3. Freesound upload with invalid domain -> 400
    bad_domain_meta = {
        "title": "Freesound Beat",
        "source": "freesound",
        "kind": "loop",
        "license": "CC0",
        "source_url": "https://evil.com/sounds/123/",
        "rights_confirmed": True,
    }
    query = urlencode({"filename": "test_fs.wav", "metadata": json.dumps(bad_domain_meta)})
    status, payload = request_raw(server_obj, "POST", f"/api/references/upload?{query}", body=wav_bytes, headers=headers)
    assert status == 400
    res = json.loads(payload.decode("utf-8"))
    assert res["ok"] is False
    assert "出典サービスの作品ページURLが必要です" in res["error"]

    # 4. Freesound upload with valid URL -> 200
    good_fs_meta = {
        "title": "Freesound Beat",
        "source": "freesound",
        "kind": "loop",
        "license": "CC0 1.0",
        "source_url": "https://freesound.org/people/tester/sounds/12345/",
        "rights_confirmed": True,
    }
    query = urlencode({"filename": "test_fs.wav", "metadata": json.dumps(good_fs_meta)})
    status, payload = request_raw(server_obj, "POST", f"/api/references/upload?{query}", body=wav_bytes, headers=headers)
    assert status == 200
    res = json.loads(payload.decode("utf-8"))
    assert res["ok"] is True
    assert res["entry"]["source"] == "freesound"
    assert res["entry"]["license"] == "CC0 1.0"

    # 5. Missing rights_confirmed -> 400
    no_rights_meta = {
        "title": "No Rights Groove",
        "source": "local",
        "kind": "track",
        "license": "自作",
        "rights_confirmed": False,
    }
    query = urlencode({"filename": "test_nr.wav", "metadata": json.dumps(no_rights_meta)})
    status, payload = request_raw(server_obj, "POST", f"/api/references/upload?{query}", body=wav_bytes, headers=headers)
    assert status == 400
    res = json.loads(payload.decode("utf-8"))
    assert res["ok"] is False
    assert "利用条件" in res["error"]


def test_music_training_api_lifecycle_via_http(server):
    server_obj, _library, tmp_path = server
    wav_path = _create_synthetic_wav(tmp_path / "test_th.wav", bpm=126.0, seconds=8.0)

    # 1. First create an analysis card as reference material
    create_body = json.dumps({
        "path": str(wav_path),
        "title": "Deep Tech House Groove",
        "start_sec": 0.0,
        "duration_sec": 8.0,
    })
    status, res = request(server_obj, "POST", "/api/references/analysis-card/create", body=create_body)
    assert status == 200
    card_id = res["card"]["card_id"]

    # 2. Get available training themes
    status, themes_res = request(server_obj, "GET", "/api/references/training/themes")
    assert status == 200
    assert themes_res["ok"] is True
    assert len(themes_res["themes"]) >= 6

    # 3. Start training session with kick_bass_space
    start_body = json.dumps({"card_id": card_id, "theme_id": "kick_bass_space"})
    status, start_res = request(server_obj, "POST", "/api/references/training/session/start", body=start_body)
    assert status == 200
    assert start_res["ok"] is True
    assert start_res["theme"]["theme_id"] == "kick_bass_space"
    assert len(start_res["theme"]["listening_points"]) >= 3
    assert "source" in start_res
    assert start_res["source"]["tempo"] in (63.0, 126.0)

    # 4. Generate contrasting variants A/B/C for the session
    gen_body = json.dumps({"card_id": card_id, "theme_id": "kick_bass_space", "key": "D#m", "bars": 8})
    status, gen_res = request(server_obj, "POST", "/api/references/training/session/generate-variants", body=gen_body)
    assert status == 200
    assert gen_res["ok"] is True
    variants = gen_res["variants"]
    assert len(variants) == 3
    for v in variants:
        assert "novice_summary" in v
        assert "technical_summary" in v
        assert "what_to_listen" in v
        assert "live_sound_design" in v
        assert "Kick" in v["notes"]
        assert "Bass" in v["notes"]

    # 5. Promote Variant B (Interlocking) to Studio candidate
    prom_body = json.dumps({"card_id": card_id, "theme_id": "kick_bass_space", "variant_id": "B", "bars": 64})
    status, prom_res = request(server_obj, "POST", "/api/references/training/session/promote-variant", body=prom_body)
    assert status == 200
    assert prom_res["ok"] is True
    candidate_id = prom_res["candidate_id"]
    assert candidate_id is not None
    assert "Liveへの変更は一切行われていません" in prom_res["live_mutation_warning"]

    # 6. Save reflection / journal entry
    journal_body = json.dumps({
        "theme_id": "kick_bass_space",
        "card_id": card_id,
        "source_title": "Deep Tech House Groove",
        "chosen_variant_id": "B",
        "chosen_variant_name": "裏拍インターロッキング型",
        "user_feedback_tags": ["低音がスッキリした", "ノリが出た"],
        "user_notes": "キックのアタックとベースの音価を住み分けると音が格段に太くなった。",
        "key_takeaway": "キックとベースは同時に鳴らさず16分裏で会話させると音が太くなる",
        "next_action_tip": "次回の制作でもベースの休符率を40%以上確保する",
        "promoted_candidate_id": candidate_id,
    })
    status, save_res = request(server_obj, "POST", "/api/references/training/journal/save", body=journal_body)
    assert status == 200
    assert save_res["ok"] is True

    # 7. List journal entries
    status, j_list_res = request(server_obj, "GET", "/api/references/training/journal")
    assert status == 200
    assert j_list_res["ok"] is True
    assert len(j_list_res["entries"]) >= 1

    # 8. Get tips for subsequent production
    status, tips_res = request(server_obj, "GET", "/api/references/training/tips")
    assert status == 200
    assert tips_res["ok"] is True
    assert len(tips_res["tips"]) >= 1
    assert "ベースの休符率" in tips_res["tips"][0]["tip"]


def test_local_only_learning_cycle_skips_external_discovery(server):
    server_obj, _library, _tmp_path = server
    status, result = request(
        server_obj,
        "POST",
        "/api/references/learning-cycle",
        body=json.dumps({"source": "youtube", "genres": ["house"], "local_only": True}),
    )
    assert status == 200
    assert result["ok"] is True
    assert result["local_only"] is True
    assert result["discovery"] == []
    assert result["analyzed"] == []
    assert result["training"] is None


def test_autonomous_learning_cycle_searches_youtube_with_random_genre(server, monkeypatch):
    from kihachi_mcp.knowledge.genre_database import load_database
    from kihachi_mcp.studio import reference_routes

    _server_obj, library, _tmp_path = server

    class SearchRecorder:
        def __init__(self):
            self.calls = []

        def search(self, source, query):
            self.calls.append((source, query))
            return {"results": []}

    sources = SearchRecorder()
    monkeypatch.setattr(reference_routes.random, "choice", lambda values: values[0])
    result = reference_routes._run_reference_learning_cycle(
        library, sources, {"autonomous": True}
    )
    expected_genre = load_database()[0].name
    assert result["autonomous"] is True
    assert result["source"] == "youtube"
    assert result["genres"] == [expected_genre]
    assert sources.calls == [("youtube", expected_genre + " electronic music")]

    reference_routes._run_reference_learning_cycle(
        library, sources, {"autonomous": True, "genres": result["genres"], "offset": 2}
    )
    assert len(sources.calls) == 1
