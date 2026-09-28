import io
import json
from urllib.error import HTTPError
from urllib.request import Request

import pytest

from kihachi_mcp.services.reference_analysis import ReferenceError
from kihachi_mcp.services.reference_library import ReferenceLibrary
from kihachi_mcp.services.reference_sources import ReferenceSources, _SafeRedirect


class Opener:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.requests = []

    def open(self, request, timeout):
        self.requests.append(request)
        data = self.payloads.pop(0)
        if isinstance(data, Exception):
            raise data
        return io.BytesIO(json.dumps(data).encode() if isinstance(data, dict) else data)


def fs_row():
    return {
        "id": 123,
        "name": "Kick",
        "username": "artist",
        "license": "CC0",
        "duration": 1.0,
        "type": "wav",
    }


def jm_row(allowed=True):
    return {
        "id": "456",
        "name": "Reference",
        "artist_name": "Artist",
        "license_ccurl": "https://creativecommons.org/licenses/by/4.0/",
        "duration": 30,
        "audiodownload_allowed": allowed,
        "audiodownload": "https://prod-1.storage.jamendo.com/download/track/456/flac/"
        if allowed
        else "",
    }


def jamendo_response(allowed=True):
    return {"headers": {"status": "success"}, "results": [jm_row(allowed)]}


def test_api_key_can_search_but_cannot_download_original():
    opener = Opener([{"results": [fs_row()]}])
    sources = ReferenceSources({"FREESOUND_API_KEY": "fake-secret"}, lambda _: opener)
    result = sources.search("freesound", "kick")
    assert "fake-secret" not in json.dumps(result)
    assert "fake-secret" not in json.dumps(sources.status())
    assert result["results"][0]["download_allowed"] is False
    assert opener.requests[0].get_header("Authorization") == "Token fake-secret"
    assert "fake-secret" not in opener.requests[0].full_url
    with pytest.raises(ReferenceError, match="FREESOUND_ACCESS_TOKEN"):
        sources.import_remote(
            None, {"source": "freesound", "remote_id": "123", "rights_confirmed": True}
        )
    assert len(opener.requests) == 1


@pytest.mark.parametrize("provider", ["freesound", "jamendo"])
def test_original_download_joins_common_library_and_preserves_provider_snapshot(
    tmp_path, provider
):
    row = fs_row() if provider == "freesound" else jm_row()
    response = row if provider == "freesound" else jamendo_response()
    opener = Opener([response, b"test audio bytes"])
    keys = {"FREESOUND_ACCESS_TOKEN": "fake-oauth", "JAMENDO_CLIENT_ID": "fake-client"}
    sources = ReferenceSources(keys, lambda _: opener)
    library = ReferenceLibrary(
        tmp_path / "library", lambda _: {"metrics": {}, "warnings": []}
    )
    result = sources.import_remote(
        library,
        {
            "source": provider,
            "remote_id": str(row["id"]),
            "expected_license": row.get("license", row.get("license_ccurl")),
            "rights_confirmed": True,
            "kind": "track",
            "genres": "dub_techno",
        },
    )
    assert result["ok"]
    entry = result["entry"]
    assert entry["provider_metadata"]["remote_id"] == str(row["id"])
    assert "retrieved_at" in entry["provider_metadata"]
    assert "_download" not in json.dumps(entry)
    assert "fake-oauth" not in json.dumps(entry)
    assert "fake-client" not in json.dumps(entry)
    edited = library.update(entry["id"], {"notes": "checked"})["entry"]
    assert edited["provider_metadata"] == entry["provider_metadata"]


def test_jamendo_denied_download_never_fetches_audio():
    opener = Opener([jamendo_response(False)])
    sources = ReferenceSources({"JAMENDO_CLIENT_ID": "fake-client"}, lambda _: opener)
    with pytest.raises(ReferenceError, match="ダウンロードできません"):
        sources.import_remote(
            None, {"source": "jamendo", "remote_id": "456", "rights_confirmed": True}
        )
    assert len(opener.requests) == 1


def test_changed_license_requires_review_before_download():
    opener = Opener([fs_row()])
    sources = ReferenceSources(
        {"FREESOUND_ACCESS_TOKEN": "fake-token"}, lambda _: opener
    )
    with pytest.raises(ReferenceError, match="利用条件が検索時と異なります"):
        sources.import_remote(
            None,
            {
                "source": "freesound",
                "remote_id": "123",
                "expected_license": "different",
                "rights_confirmed": True,
            },
        )
    assert len(opener.requests) == 1


def test_redirects_do_not_leak_authorization_and_external_redirect_is_refused():
    request = Request(
        "https://freesound.org/apiv2/sounds/123/download/",
        headers={"Authorization": "Bearer fake-secret"},
    )
    handler = _SafeRedirect("freesound")
    redirected = handler.redirect_request(
        request, None, 302, "", {}, "https://cdn.freesound.org/audio.wav"
    )
    assert redirected.get_header("Authorization") is None
    with pytest.raises(ReferenceError):
        handler.redirect_request(
            request, None, 302, "", {}, "https://evil.example/audio.wav"
        )
    with pytest.raises(ReferenceError):
        handler.redirect_request(
            request, None, 302, "", {}, "http://127.0.0.1/audio.wav"
        )


def test_auth_errors_are_redacted_and_not_retried():
    error = HTTPError(
        "https://api.jamendo.com/?client_id=fake-secret", 403, "fake-secret", {}, None
    )
    opener = Opener([error])
    sources = ReferenceSources({"JAMENDO_CLIENT_ID": "fake-secret"}, lambda _: opener)
    with pytest.raises(ReferenceError) as result:
        sources.search("jamendo", "techno")
    assert "fake-secret" not in str(result.value)
    assert len(opener.requests) == 1


def test_config_file_reads_only_expected_fields_and_env_wins(tmp_path):
    config = tmp_path / "api.json"
    config.write_text(
        json.dumps({"JAMENDO_CLIENT_ID": "file-client", "unrelated": "unused"})
    )
    sources = ReferenceSources({"KIHACHI_REFERENCE_API_CONFIG": str(config)})
    assert sources.status()["jamendo"]["search_configured"] is True
    assert "file-client" not in json.dumps(sources.status())
    env_sources = ReferenceSources(
        {"KIHACHI_REFERENCE_API_CONFIG": str(config), "JAMENDO_CLIENT_ID": "env-client"}
    )
    assert env_sources._key("JAMENDO_CLIENT_ID") == "env-client"


def test_expired_oauth_rotates_tokens_and_preserves_other_settings(tmp_path):
    path = tmp_path / "credentials.json"
    path.write_text(
        json.dumps(
            {
                "FREESOUND_API_KEY": "secret",
                "FREESOUND_CLIENT_ID": "client",
                "FREESOUND_ACCESS_TOKEN": "old",
                "FREESOUND_REFRESH_TOKEN": "refresh",
                "FREESOUND_TOKEN_EXPIRES_AT": 1,
                "JAMENDO_CLIENT_ID": "keep",
            }
        )
    )
    opener = Opener(
        [
            {"access_token": "new", "refresh_token": "rotated", "expires_in": 86400},
            {"results": [fs_row()]},
            {"results": [fs_row()]},
        ]
    )
    sources = ReferenceSources(
        {"KIHACHI_REFERENCE_API_CONFIG": str(path)}, lambda _: opener
    )
    sources.search("freesound", "kick")
    sources.search("freesound", "kick")
    saved = json.loads(path.read_text())
    assert saved["FREESOUND_REFRESH_TOKEN"] == "rotated"
    assert saved["JAMENDO_CLIENT_ID"] == "keep"
    assert path.stat().st_mode & 0o777 == 0o600
    assert len(opener.requests) == 3
    assert opener.requests[1].get_header("Authorization") == "Bearer new"
    assert opener.requests[0].get_method() == "POST"


def test_failed_refresh_keeps_original_credentials_and_redacts_error(tmp_path):
    path = tmp_path / "credentials.json"
    original = json.dumps(
        {
            "FREESOUND_API_KEY": "secret",
            "FREESOUND_CLIENT_ID": "client",
            "FREESOUND_ACCESS_TOKEN": "old",
            "FREESOUND_REFRESH_TOKEN": "refresh",
            "FREESOUND_TOKEN_EXPIRES_AT": 1,
        }
    )
    path.write_text(original)
    opener = Opener(
        [HTTPError("https://freesound.org/", 400, "secret refresh", {}, None)]
    )
    sources = ReferenceSources(
        {"KIHACHI_REFERENCE_API_CONFIG": str(path)}, lambda _: opener
    )
    with pytest.raises(ReferenceError, match="再認証") as error:
        sources.search("freesound", "kick")
    assert "secret" not in str(error.value)
    assert path.read_text() == original
    assert len(opener.requests) == 1
