import http.client
import json
import threading
import uuid
from http.server import ThreadingHTTPServer
from urllib.parse import urlencode

import pytest

from kihachi_mcp.services.reference_library import ReferenceLibrary
from kihachi_mcp.services.reference_sources import ReferenceSources
from kihachi_mcp.services.sample_catalog import SampleCatalog
from kihachi_mcp.studio.app import StudioApp, _handler_for


@pytest.fixture
def server(tmp_path):
    class NoLive:
        def __getattr__(self, name):
            raise AssertionError(f"Reference library must not call Live: {name}")

    library = ReferenceLibrary(
        tmp_path / "library", lambda _: {"metrics": {}, "warnings": []}
    )
    sample_catalog = SampleCatalog(tmp_path / "sample-catalog")
    app = StudioApp(
        runtime=NoLive(),
        reference_library=library,
        reference_sources=ReferenceSources({}),
        sample_catalog=sample_catalog,
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler_for(app))
    server.sample_catalog = sample_catalog
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, library
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def request(server, method, path, body=None, headers=None):
    client = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=10)
    try:
        client.request(method, path, body=body, headers=headers or {})
        response = client.getresponse()
        return response.status, response.read()
    finally:
        client.close()


def test_page_and_upload_round_trip_without_live_calls(server):
    http, library = server
    status, page = request(http, "GET", "/references")
    assert status == 200
    assert "ローカル解析して保存" in page.decode()
    metadata = {
        "title": "日本語の参考",
        "source": "freesound",
        "kind": "loop",
        "source_url": "https://freesound.org/people/test/sounds/1/",
        "license": "CC0",
        "genres": "dub_techno",
        "rights_confirmed": True,
    }
    query = urlencode({"filename": "test.wav", "metadata": json.dumps(metadata)})
    headers = {
        "Content-Type": "application/octet-stream",
        "Origin": f"http://127.0.0.1:{http.server_port}",
    }
    status, payload = request(
        http, "POST", "/api/references/upload?" + query, b"fake audio content", headers
    )
    assert status == 200
    result = json.loads(payload)
    assert result["entry"]["title"] == metadata["title"]
    assert result["entry"]["source"] == "freesound"
    assert len(library.entries()) == 1
    status, payload = request(http, "GET", "/api/references/list")
    assert status == 200
    assert len(json.loads(payload)["entries"]) == 1


def test_dialogue_http_only_returns_a_reviewable_brief(server):
    http, _library = server
    session = uuid.uuid4().hex
    headers = {
        "Content-Type": "application/json",
        "Origin": f"http://127.0.0.1:{http.server_port}",
    }
    status, payload = request(
        http,
        "POST",
        "/api/dialogue/turn",
        json.dumps({"session_id": session, "utterance": "120 BPMで", "brief": "Funk"}),
        headers,
    )
    result = json.loads(payload)
    assert status == 200
    assert result["proposal"]["brief"] == "Funk\n120 BPMで"
    assert result["live_changed"] is False
    status, payload = request(
        http,
        "POST",
        "/api/dialogue/accept",
        json.dumps(
            {
                "session_id": session,
                "proposal_id": result["proposal"]["id"],
                "brief": "Funk",
            }
        ),
        headers,
    )
    assert status == 200
    assert json.loads(payload)["generation_started"] is False
    status, _ = request(
        http,
        "POST",
        "/api/dialogue/turn",
        "{}",
        {**headers, "Origin": "https://example.com"},
    )
    assert status == 403


def test_voice_endpoint_reports_local_only_and_refuses_bad_audio(server):
    http, _library = server
    status, payload = request(http, "GET", "/api/dialogue/voice/status")
    assert status == 200
    assert json.loads(payload)["cloud_fallback"] is False
    headers = {
        "Content-Type": "audio/wav",
        "Origin": f"http://127.0.0.1:{http.server_port}",
    }
    status, payload = request(
        http, "POST", "/api/dialogue/transcribe", b"too short", headers
    )
    assert status == 400
    assert json.loads(payload)["ok"] is False


@pytest.mark.parametrize(
    "headers",
    [
        {"Content-Type": "application/json", "Origin": "https://evil.example"},
        {"Content-Type": "application/json", "Host": "evil.example"},
        {"Content-Type": "text/plain"},
    ],
)
def test_cross_origin_and_simple_form_posts_are_refused(server, headers):
    http, library = server
    status, _ = request(http, "POST", "/api/references/import", b"{}", headers)
    assert status in {403, 415}
    assert not library.directory.exists()


def test_invalid_upload_fails_without_saving(server):
    http, library = server
    status, _ = request(
        http,
        "POST",
        "/api/references/upload?filename=.env",
        b"never store this",
        {"Content-Type": "application/octet-stream"},
    )
    assert status == 400
    assert not library.directory.exists()


def test_tempo_proposal_is_read_only_and_never_calls_runtime(server):
    http, library = server
    payload = json.dumps(
        {"brief": "暗いテクノを作る", "genre": "dub_techno", "kind": "track"}
    )
    status, response = request(
        http,
        "POST",
        "/api/references/tempo-proposal",
        payload,
        {"Content-Type": "application/json"},
    )
    data = json.loads(response)
    assert status == 200
    assert data["generation_applied"] is False
    assert data["changed"] is False
    assert library.entries() == []


def test_mutashon_alias_is_exposed_as_mutation_funk_after_local_import(server, tmp_path):
    http, library = server
    path = tmp_path / "song.wav"
    path.write_bytes(b"fixture audio")
    library.import_file(
        path,
        {
            "title": "Original",
            "source": "local",
            "kind": "track",
            "license": "自作",
            "rights_confirmed": True,
            "genres": "Mutashon Funk",
        },
    )
    status, response = request(http, "GET", "/api/references/genres")
    assert status == 200
    assert {"slug": "mutation_funk", "name": "Mutation Funk"} in json.loads(response)[
        "genres"
    ]
    status, response = request(
        http, "GET", "/api/references/compare?genre=Mutashon%20Funk&kind=track"
    )
    assert status == 200
    assert json.loads(response)["count"] == 1


def test_audio_preview_streams_only_managed_copy_and_supports_range(server, tmp_path):
    http, library = server
    original = tmp_path / "original.wav"
    original.write_bytes(b"RIFFtest audio bytes")
    entry = library.import_file(
        original,
        {
            "title": "test",
            "source": "local",
            "kind": "track",
            "license": "自作",
            "rights_confirmed": True,
            "genres": "Mutashon Funk",
        },
    )["entry"]
    original.write_bytes(b"changed source file")
    path = "/api/references/audio/" + entry["id"]
    status, body = request(http, "GET", path)
    assert status == 200 and body == b"RIFFtest audio bytes"
    status, body = request(http, "GET", path, headers={"Range": "bytes=4-7"})
    assert status == 206 and body == b"test"
    status, _ = request(http, "GET", path, headers={"Sec-Fetch-Site": "cross-site"})
    assert status == 403
    status, _ = request(http, "GET", path, headers={"Range": "bytes=999-"})
    assert status == 416
    status, _ = request(http, "GET", "/api/references/audio/../status")
    assert status == 404


def test_sample_preview_streams_only_an_indexed_file(server, tmp_path):
    http, _library = server
    source = tmp_path / "samples"
    source.mkdir()
    sample = source / "Deep Kick.wav"
    sample.write_bytes(b"RIFFsample preview")
    http.sample_catalog.index(str(source))
    result = http.sample_catalog.search("kick")["results"][0]
    path = "/api/references/sample-catalog/audio/" + result["sample_id"]

    status, body = request(http, "GET", path)
    assert status == 200 and body == b"RIFFsample preview"
    status, body = request(http, "GET", path, headers={"Range": "bytes=4-9"})
    assert status == 206 and body == b"sample"
    status, _ = request(http, "GET", path, headers={"Sec-Fetch-Site": "cross-site"})
    assert status == 403
    status, _ = request(http, "GET", path + "?path=/etc/passwd")
    assert status == 403


def test_sample_preview_rejects_a_file_replaced_by_symlink(server, tmp_path):
    http, _library = server
    source = tmp_path / "samples"
    source.mkdir()
    sample = source / "Kick.wav"
    sample.write_bytes(b"RIFForiginal")
    outside = tmp_path / "outside.wav"
    outside.write_bytes(b"RIFFoutside")
    http.sample_catalog.index(str(source))
    sample_id = http.sample_catalog.search("kick")["results"][0]["sample_id"]
    sample.unlink()
    sample.symlink_to(outside)

    status, _ = request(
        http, "GET", "/api/references/sample-catalog/audio/" + sample_id
    )
    assert status == 404
