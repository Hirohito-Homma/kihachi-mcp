"""Explicit, bounded Freesound/Jamendo downloads with server-only credentials."""

from __future__ import annotations

import json
import math
import os
import re
import threading
import time
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from kihachi_mcp.services.live_paths import bridge_state_dir
from kihachi_mcp.services.reference_analysis import (
    EXTENSIONS,
    MAX_SECONDS,
    ReferenceError,
)
from kihachi_mcp.services.reference_library import MAX_BYTES

_OAUTH_LOCK = threading.Lock()

KEYS = ("FREESOUND_API_KEY", "FREESOUND_ACCESS_TOKEN", "JAMENDO_CLIENT_ID", "YOUTUBE_DATA_API_KEY")


def _allowed(url: str, source: str) -> bool:
    parsed = urlparse(url)
    domain = {
        "freesound": "freesound.org",
        "jamendo": "jamendo.com",
        "youtube": "www.googleapis.com",
    }.get(source)
    host = parsed.hostname or ""
    return (
        domain is not None
        and parsed.scheme == "https"
        and (host == domain if source == "youtube" else (host == domain or host.endswith("." + domain)))
        and not parsed.username
        and not parsed.password
        and parsed.port in (None, 443)
    )


def _autonomous_license_allowed(source: str, license_value: str) -> bool:
    """Allow automatic reference import only for permissive CC0 / CC BY terms."""
    value = str(license_value or "").strip().lower().replace("http://", "https://")
    if not value or any(term in value for term in ("nc", "noncommercial", "non-commercial", "nd", "no-derivatives", "no derivatives", "sa", "share-alike", "sharealike")):
        return False
    if "cc0" in value or "zero/1.0" in value or "publicdomain/zero" in value:
        return True
    if source not in {"freesound", "jamendo"}:
        return False
    return bool(
        re.search(r"creativecommons\.org/licenses/by/(?:2\.0|2\.5|3\.0|4\.0)(?:/|$)", value)
        or value in {"attribution", "creative commons attribution"}
    )


class _SafeRedirect(HTTPRedirectHandler):
    def __init__(self, source: str):
        self.source = source

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _allowed(newurl, self.source):
            raise ReferenceError(
                "提供元の外部への転送を拒否しました。公式サイトから取得してください"
            )
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if (
            redirected is not None
            and urlparse(newurl).netloc != urlparse(req.full_url).netloc
        ):
            redirected.remove_header("Authorization")
        return redirected


class ReferenceSources:
    def __init__(self, environ=None, opener_factory=None):
        self._environ = os.environ if environ is None else environ
        self._default_config = environ is None
        self._opener_factory = opener_factory or (
            lambda source: build_opener(_SafeRedirect(source))
        )

    def status(self) -> dict:
        # Readiness is not proof of authentication; never return key values.
        return {
            "freesound": {
                "search_configured": bool(
                    self._key("FREESOUND_API_KEY")
                    or self._key("FREESOUND_ACCESS_TOKEN")
                ),
                "download_configured": bool(self._key("FREESOUND_ACCESS_TOKEN")),
            },
            "jamendo": {
                "search_configured": bool(self._key("JAMENDO_CLIENT_ID")),
                "download_configured": bool(self._key("JAMENDO_CLIENT_ID")),
            },
            "youtube": {"search_configured": bool(self._key("YOUTUBE_DATA_API_KEY"))},
        }

    def _key(self, name: str) -> str:
        value = self._environ.get(name, "").strip()
        config_path = self._environ.get("KIHACHI_REFERENCE_API_CONFIG")
        if not config_path and self._default_config:
            default_path = Path(bridge_state_dir()) / "reference-api.json"
            if default_path.is_file():
                config_path = str(default_path)
        if value or not config_path:
            return value
        try:
            path = Path(config_path).expanduser()
            if path.stat().st_size > 16_384:
                raise ValueError
            data = json.loads(path.read_text())
            value = data.get(name, "")
            if not isinstance(value, str):
                raise TypeError
            return value.strip()
        except (OSError, ValueError, TypeError, AttributeError):
            raise ReferenceError(
                "参考音源APIの設定ファイルを読み取れません。JSON形式とパスを確認してください"
            ) from None

    def _access_token(self) -> str:
        # Explicit environment tokens remain under the caller's management.
        if self._environ.get("FREESOUND_ACCESS_TOKEN"):
            return self._key("FREESOUND_ACCESS_TOKEN")
        path_value = self._environ.get("KIHACHI_REFERENCE_API_CONFIG")
        if not path_value and self._default_config:
            path_value = str(Path(bridge_state_dir()) / "reference-api.json")
        if not path_value or not Path(path_value).expanduser().is_file():
            return self._key("FREESOUND_ACCESS_TOKEN")
        with _OAUTH_LOCK:
            path = Path(path_value).expanduser()
            try:
                if path.stat().st_size > 16_384:
                    raise ValueError
                config = json.loads(path.read_text())
                expiry = config.get("FREESOUND_TOKEN_EXPIRES_AT")
                if expiry is None or float(expiry) > time.time() + 60:
                    return self._key("FREESOUND_ACCESS_TOKEN")
                fields = {
                    "client_id": config.get("FREESOUND_CLIENT_ID"),
                    "client_secret": self._key("FREESOUND_API_KEY"),
                    "refresh_token": config.get("FREESOUND_REFRESH_TOKEN"),
                    "grant_type": "refresh_token",
                }
                if not all(isinstance(v, str) and v for v in fields.values()):
                    raise ValueError
                request = Request(
                    "https://freesound.org/apiv2/oauth2/access_token/",
                    data=urlencode(fields).encode(),
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
                with self._opener_factory("freesound").open(
                    request, timeout=30
                ) as response:
                    result = json.loads(response.read(16_385))
                if not all(
                    isinstance(result.get(k), str) and result[k]
                    for k in ("access_token", "refresh_token")
                ):
                    raise ValueError
                lifetime = int(result["expires_in"])
                if lifetime <= 0:
                    raise ValueError
                config.update(
                    FREESOUND_ACCESS_TOKEN=result["access_token"],
                    FREESOUND_REFRESH_TOKEN=result["refresh_token"],
                    FREESOUND_TOKEN_EXPIRES_AT=int(time.time()) + lifetime,
                )
                temporary = None
                try:
                    with NamedTemporaryFile(
                        mode="w",
                        dir=path.parent,
                        prefix=".reference-api-",
                        delete=False,
                    ) as output:
                        temporary = Path(output.name)
                        os.chmod(temporary, 0o600)
                        json.dump(config, output)
                    os.replace(temporary, path)
                finally:
                    if temporary is not None:
                        temporary.unlink(missing_ok=True)
                return result["access_token"]
            except (OSError, ValueError, TypeError, KeyError, AttributeError):
                raise ReferenceError(
                    "Freesoundの認証更新に失敗しました。再認証が必要です。自動再試行はしません"
                ) from None

    def _request(self, source: str, url: str, headers=None):
        if not _allowed(url, source):
            raise ReferenceError("許可された提供元のHTTPS URLではありません")
        try:
            return self._opener_factory(source).open(
                Request(
                    url, headers={"User-Agent": "KIHACHI-Studio/0.1", **(headers or {})}
                ),
                timeout=30,
            )
        except HTTPError as exc:
            if source == "youtube" and exc.code in (400, 403):
                try:
                    payload = json.loads(exc.read(16_384))
                    error = payload.get("error", {})
                    reasons = [
                        str(item.get("reason", ""))
                        for item in error.get("errors", [])
                        if isinstance(item, dict)
                    ]
                    reasons.extend(
                        str(item.get("reason", ""))
                        for item in error.get("details", [])
                        if isinstance(item, dict)
                    )
                    status = str(error.get("status", ""))
                except (OSError, ValueError, TypeError, AttributeError):
                    reasons, status = [], ""
                if any(reason in {"accessNotConfigured", "SERVICE_DISABLED"} for reason in reasons) or status == "PERMISSION_DENIED":
                    raise ReferenceError("YouTube Data API v3がこのGoogle Cloudプロジェクトで無効です。同じプロジェクトでAPIを有効化してください") from None
                if any("REFERER" in reason.upper() or "IP_ADDRESS" in reason.upper() for reason in reasons):
                    raise ReferenceError("YouTube APIキーのアプリケーション制限が検索元を拒否しました。HTTPリファラー制限を外すか、サーバーの送信元IPを許可してください") from None
                if any(reason in {"keyInvalid", "API_KEY_INVALID"} for reason in reasons):
                    raise ReferenceError("YouTube APIキーが無効です。正しいキーをローカル設定へ登録し直してください") from None
                if any(reason in {"quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded", "userRateLimitExceeded"} for reason in reasons):
                    raise ReferenceError("YouTube Data APIの割り当て上限に達しています。Google Cloud Consoleの割り当てを確認してください") from None
                if exc.code == 403:
                    raise ReferenceError("YouTube APIが403で拒否しました。Cloud ConsoleでAPI有効化とキー制限を確認してください") from None
                raise ReferenceError("YouTube APIが400で拒否しました。APIキーと検索パラメータを確認してください") from None
            if exc.code in (401, 403):
                raise ReferenceError(
                    "認証・アクセス権を確認してください。キーは画面に貼らないでください"
                ) from None
            if exc.code == 429:
                raise ReferenceError(
                    "APIの利用上限です。自動再試行はしません"
                ) from None
            raise ReferenceError(
                f"提供元がHTTP {exc.code}を返しました。自動再試行はしません"
            ) from None
        except (URLError, TimeoutError, OSError, ValueError):
            raise ReferenceError(
                "提供元へ接続できません。通信設定を確認してください"
            ) from None

    def _json(self, source: str, path: str, params: dict) -> dict:
        headers = {}
        if source == "freesound":
            token, key = (
                self._access_token(),
                self._key("FREESOUND_API_KEY"),
            )
            if not (token or key):
                raise ReferenceError(
                    "FREESOUND_API_KEY または FREESOUND_ACCESS_TOKEN を設定してください"
                )
            headers["Authorization"] = "Bearer " + token if token else "Token " + key
            base = "https://freesound.org/apiv2/"
        elif source == "jamendo":
            key = self._key("JAMENDO_CLIENT_ID")
            if not key:
                raise ReferenceError("JAMENDO_CLIENT_ID を設定してください")
            params = {
                **params,
                "client_id": key,
                "format": "json",
                "audiodlformat": "flac",
            }
            base = "https://api.jamendo.com/v3.0/"
        elif source == "youtube":
            key = self._key("YOUTUBE_DATA_API_KEY")
            if not key:
                raise ReferenceError("YOUTUBE_DATA_API_KEY を設定してください")
            params = {**params, "key": key}
            base = "https://www.googleapis.com/youtube/v3/"
        else:
            raise ReferenceError("Freesound、Jamendo、YouTubeのいずれかを選択してください")
        try:
            with self._request(
                source, base + path + "?" + urlencode(params), headers
            ) as response:
                data = response.read(2_000_001)
            if len(data) > 2_000_000:
                raise ReferenceError("APIの応答が大きすぎます")
            result = json.loads(data)
            if not isinstance(result, dict):
                raise TypeError
            if (
                source == "jamendo"
                and result.get("headers", {}).get("status") != "success"
            ):
                raise ReferenceError(
                    "Jamendo APIがエラーを返しました。設定や利用上限を確認してください"
                )
            return result
        except ReferenceError:
            raise
        except (ValueError, KeyError, TypeError, TimeoutError, OSError):
            raise ReferenceError("APIの応答を読み取れません") from None

    def search(self, source: str, query: str) -> dict:
        if not isinstance(query, str) or not 1 <= len(query.strip()) <= 120:
            raise ReferenceError("検索語は1〜120文字で入力してください")
        if source == "youtube":
            data = self._json(
                source,
                "search",
                {"part": "snippet", "type": "video", "maxResults": 10, "q": query.strip()},
            )
            try:
                results = []
                for item in data.get("items", []):
                    video_id = item["id"]["videoId"]
                    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
                        continue
                    snippet = item["snippet"]
                    results.append({
                        "source": "youtube", "remote_id": video_id,
                        "title": str(snippet.get("title", ""))[:200],
                        "creator": str(snippet.get("channelTitle", ""))[:200],
                        "license": "YouTube公式プレイヤーで視聴（音源取得なし）",
                        "source_url": f"https://www.youtube.com/watch?v={video_id}",
                        "duration_seconds": 0,
                        "download_allowed": False,
                        "download_note": "この結果から音声は取得しません。聴取メモを登録してください。",
                    })
            except (ValueError, KeyError, TypeError):
                raise ReferenceError("YouTube検索結果の形式を読み取れません") from None
            return {
                "ok": True,
                "results": results,
                "notice": "最大10件。公式プレイヤーで試聴後、曲と利用条件を確認して参照登録してください。",
            }
        if source == "freesound":
            data = self._json(
                source,
                "search/",
                {
                    "query": query.strip(),
                    "page_size": 10,
                    "fields": "id,name,username,license,url,duration,type,tags",
                },
            )
        else:
            data = self._json(
                source,
                "tracks/",
                {
                    "search": query.strip(),
                    "limit": 10,
                    "type": "single albumtrack",
                    "include": "musicinfo",
                },
            )
        try:
            results = [self._normalize(source, row) for row in data.get("results", [])]
        except (ValueError, KeyError, TypeError):
            raise ReferenceError("検索結果の形式を読み取れません") from None
        return {
            "ok": True,
            "results": [self._public(row) for row in results],
            "notice": "最大10件。取り込み前に各作品とAPIの利用条件を確認してください",
        }

    def _normalize(self, source: str, row: dict) -> dict:
        identifier = str(row["id"])
        if not re.fullmatch(r"[1-9][0-9]{0,11}", identifier):
            raise ValueError
        duration = float(row["duration"])
        if not math.isfinite(duration):
            raise ValueError
        if source == "freesound":
            suffix = "." + str(row.get("type", "")).lower()
            info = {
                "source": source,
                "remote_id": identifier,
                "title": str(row["name"]),
                "creator": str(row["username"]),
                "license": str(row["license"]),
                "source_url": f"https://freesound.org/s/{identifier}/",
                "duration_seconds": duration,
                "download_allowed": bool(self._key("FREESOUND_ACCESS_TOKEN"))
                and suffix in EXTENSIONS,
                "download_note": "原音源の取得にはOAuthアクセストークンが必要です",
                "_download": f"https://freesound.org/apiv2/sounds/{identifier}/download/",
                "_suffix": suffix,
            }
        else:
            info = {
                "source": source,
                "remote_id": identifier,
                "title": str(row["name"]),
                "creator": str(row["artist_name"]),
                "license": str(row.get("license_ccurl", "")),
                "source_url": f"https://www.jamendo.com/track/{identifier}",
                "duration_seconds": duration,
                "download_allowed": row.get("audiodownload_allowed") is True
                and bool(row.get("audiodownload")),
                "download_note": "作者がダウンロードを許可している作品のみ取得できます",
                "_download": row.get("audiodownload", ""),
                "_suffix": ".flac",
            }
        if not 0 < duration <= MAX_SECONDS or not info["license"]:
            info["download_allowed"] = False
            info["download_note"] = "長さの上限を超えるか、利用条件が取得できません"
        return info

    @staticmethod
    def _public(info: dict) -> dict:
        return {key: value for key, value in info.items() if not key.startswith("_")}

    def import_remote(self, library, body: dict) -> dict:
        source, identifier = body.get("source"), str(body.get("remote_id", ""))
        if source not in {"freesound", "jamendo"} or not re.fullmatch(
            r"[1-9][0-9]{0,11}", identifier
        ):
            raise ReferenceError("検索結果から音源を選んでください")
        autonomous = body.get("autonomous") is True
        if not autonomous and body.get("rights_confirmed") is not True:
            raise ReferenceError("取得する作品とAPIの利用条件を確認してください")
        if source == "freesound" and not self._key("FREESOUND_ACCESS_TOKEN"):
            raise ReferenceError(
                "原音源の取得には FREESOUND_ACCESS_TOKEN が必要です。APIキーだけでは検索のみ利用できます"
            )
        try:
            row = (
                self._json(source, f"sounds/{identifier}/", {})
                if source == "freesound"
                else self._json(source, "tracks/", {"id": identifier})["results"][0]
            )
            info = self._normalize(source, row)
        except ReferenceError:
            raise
        except (ValueError, KeyError, IndexError, TypeError):
            raise ReferenceError("作品の最新情報を取得できません") from None
        if info["remote_id"] != identifier:
            raise ReferenceError("作品IDが一致しません")
        if not info["download_allowed"]:
            raise ReferenceError(
                "この作品はダウンロードできません。別の作品を選んでください"
            )
        if autonomous and not _autonomous_license_allowed(source, info["license"]):
            raise ReferenceError(
                "自動取得の許可リスト外ライセンスです。CC0またはCC BY（NC/ND/SAなし）の作品だけ自動取得します"
            )
        if body.get("expected_license") != info["license"]:
            raise ReferenceError(
                "利用条件が検索時と異なります。再検索して確認してください"
            )
        raw_genres = body.get("genres", "")
        # API callers may naturally supply multiple genres as a list, while
        # ReferenceLibrary's metadata contract stores them as comma-separated text.
        if isinstance(raw_genres, list) and all(isinstance(item, str) for item in raw_genres):
            raw_genres = ", ".join(item.strip() for item in raw_genres if item.strip())
        metadata = {
            **self._public(info),
            "kind": body.get("kind", ""),
            "genres": raw_genres,
            "rights_confirmed": True,
            "notes": body.get("notes", ""),
        }
        headers = (
            {"Authorization": "Bearer " + self._access_token()}
            if source == "freesound"
            else {}
        )
        with TemporaryDirectory(prefix="kihachi-download-") as directory:
            path = Path(directory) / ("audio" + info["_suffix"])
            try:
                with (
                    self._request(source, info["_download"], headers) as response,
                    path.open("wb") as target,
                ):
                    size = 0
                    while block := response.read(1024 * 1024):
                        size += len(block)
                        if size > MAX_BYTES:
                            raise ReferenceError(
                                "音源が256MBを超えました。取り込みを中止します"
                            )
                        target.write(block)
            except (TimeoutError, OSError):
                raise ReferenceError(
                    "ダウンロードが中断されました。自動再試行はしません"
                ) from None
            return library.import_file(
                path,
                metadata,
                provider_snapshot=self._public(info),
                license_verification=(
                    "provider_allowlisted_auto" if autonomous else None
                ),
            )
