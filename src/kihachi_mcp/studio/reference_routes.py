"""HTTP adapter for the local reference library, isolated from Live operations."""

import json
import re
import sqlite3
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import parse_qs, urlparse

from kihachi_mcp.knowledge.genre_database import load_database
from kihachi_mcp.services.reference_analysis import EXTENSIONS, ReferenceError
from kihachi_mcp.services.reference_guidance import propose_tempo
from kihachi_mcp.services.reference_library import MAX_BYTES, validate_metadata


def handle_get(handler, library, parsed, sources, sample_catalog=None) -> bool:
    if not parsed.path.startswith("/api/references/"):
        return False
    if not _local_host(handler):
        handler._send_json({"ok": False, "error": "localhostから開いてください"}, 403)
        return True
    if parsed.path.startswith("/api/references/audio/"):
        _serve_audio(handler, library, parsed)
        return True
    query = parse_qs(parsed.query)
    try:
        if parsed.path == "/api/references/status":
            result = library.status()
            result["providers"] = sources.status()
        elif parsed.path == "/api/references/sample-catalog/status" and sample_catalog:
            result = sample_catalog.status()
        elif parsed.path == "/api/references/sample-catalog/search" and sample_catalog:
            result = sample_catalog.search(
                query.get("role", ["all"])[0], query.get("query", [""])[0]
            )
        elif parsed.path == "/api/references/genres":
            result = {
                "ok": True,
                "genres": [
                    *[{"slug": g.slug, "name": g.name} for g in load_database()],
                    *[{"slug": name, "name": name} for name in library.custom_genres()],
                ],
            }
        elif parsed.path == "/api/references/list":
            result = {"ok": True, "entries": library.entries()}
        elif parsed.path == "/api/references/compare":
            result = library.compare(
                query.get("genre", [""])[0],
                query.get("kind", [""])[0],
                query.get("target", [""])[0],
                query.get("sample_role", [""])[0],
            )
        else:
            handler._send_json({"ok": False, "error": "not found"}, 404)
            return True
        handler._send_json(result)
    except ReferenceError as exc:
        handler._send_json({"ok": False, "error": str(exc)}, 400)
    except (OSError, sqlite3.Error):
        handler._send_json(
            {"ok": False, "error": "参考音源DBの保存先を読み書きできません"}, 500
        )
    return True


_AUDIO_TYPES = {
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
    ".flac": "audio/flac",
    ".aif": "audio/aiff",
    ".aiff": "audio/aiff",
    ".m4a": "audio/mp4",
    ".ogg": "audio/ogg",
}


def _serve_audio(handler, library, parsed) -> None:
    if (
        parsed.query
        or handler.headers.get("Sec-Fetch-Site", "same-origin")
        not in {"same-origin", "none"}
        or (
            handler.headers.get("Origin")
            and handler.headers["Origin"] != "http://" + handler.headers.get("Host", "")
        )
    ):
        handler._send_json(
            {"ok": False, "error": "Studioの画面から再生してください"}, 403
        )
        return
    entry_id = parsed.path.removeprefix("/api/references/audio/")
    if not re.fullmatch(r"[a-f0-9]{32}", entry_id):
        handler._send_json({"ok": False, "error": "音源が見つかりません"}, 404)
        return
    try:
        path = library.audio_path(entry_id)
        size = path.stat().st_size
        if not 0 < size <= MAX_BYTES:
            raise ReferenceError("音源ファイルのサイズが不正です")
        raw_range = handler.headers.get("Range", "")
        start, end = 0, size - 1
        if raw_range:
            match = re.fullmatch(r"bytes=(\d+)-(\d*)", raw_range)
            if not match:
                handler._send_json({"ok": False, "error": "再生範囲が不正です"}, 416)
                return
            start = int(match.group(1))
            end = int(match.group(2)) if match.group(2) else size - 1
            if start >= size or end < start or end >= size:
                handler._send_json({"ok": False, "error": "再生範囲が不正です"}, 416)
                return
        length = end - start + 1
        with path.open("rb") as audio:
            audio.seek(start)
            handler.send_response(206 if raw_range else 200)
            handler.send_header("Content-Type", _AUDIO_TYPES[path.suffix.lower()])
            handler.send_header("Content-Length", str(length))
            handler.send_header("Accept-Ranges", "bytes")
            handler.send_header("Cache-Control", "no-store")
            handler.send_header("X-Content-Type-Options", "nosniff")
            if raw_range:
                handler.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            handler.end_headers()
            while length:
                block = audio.read(min(length, 1024 * 1024))
                if not block:
                    break
                try:
                    handler.wfile.write(block)
                except (BrokenPipeError, ConnectionResetError):
                    return  # Browser stopped playback; do not treat this as a retry.
                length -= len(block)
    except ReferenceError:
        handler._send_json({"ok": False, "error": "音源が見つかりません"}, 404)
    except (OSError, sqlite3.Error):
        handler._send_json({"ok": False, "error": "音源を読み取れません"}, 500)


def _local_host(handler) -> bool:
    return urlparse("http://" + handler.headers.get("Host", "")).hostname in {
        "127.0.0.1",
        "localhost",
        "::1",
    }


def handle_post(handler, library, parsed, sources, sample_catalog=None) -> bool:
    if not parsed.path.startswith("/api/references/"):
        return False
    origin = handler.headers.get("Origin")
    if not _local_host(handler) or (
        origin and origin != "http://" + handler.headers.get("Host", "")
    ):
        handler._send_json(
            {"ok": False, "error": "Studioの同じ画面から操作してください"}, 403
        )
        return True
    upload = parsed.path == "/api/references/upload"
    content_type = handler.headers.get("Content-Type", "").split(";")[0]
    expected = "application/octet-stream" if upload else "application/json"
    if content_type != expected:
        handler._send_json({"ok": False, "error": "送信形式が違います"}, 415)
        return True
    try:
        if upload:
            result = _upload(handler, library, parsed)
        else:
            body = handler._read_json()
            if parsed.path == "/api/references/tempo-proposal":
                result = propose_tempo(library, body)
            elif (
                parsed.path == "/api/references/sample-catalog/index" and sample_catalog
            ):
                result = sample_catalog.index(body.get("path", ""))
            elif parsed.path == "/api/references/import":
                path = body.get("path")
                if (
                    not isinstance(path, str)
                    or not Path(path).expanduser().is_absolute()
                ):
                    raise ReferenceError("音源の絶対パスを指定してください")
                result = library.import_file(Path(path), body)
            elif parsed.path == "/api/references/update":
                result = library.update(str(body.get("id", "")), body)
            elif parsed.path == "/api/references/search":
                result = sources.search(
                    str(body.get("source", "")), body.get("query", "")
                )
            elif parsed.path == "/api/references/download":
                result = sources.import_remote(library, body)
            else:
                handler._send_json({"ok": False, "error": "not found"}, 404)
                return True
        handler._send_json(result)
    except (ReferenceError, ValueError) as exc:
        message = (
            str(exc)
            if isinstance(exc, ReferenceError)
            else "入力内容を確認してください"
        )
        handler._send_json({"ok": False, "error": message}, 400)
    except (OSError, sqlite3.Error):
        handler._send_json(
            {"ok": False, "error": "音源やDBの読み書きに失敗しました"}, 500
        )
    return True


def _upload(handler, library, parsed) -> dict:
    query = parse_qs(parsed.query)
    metadata = json.loads(query.get("metadata", ["{}"])[0])
    if not isinstance(metadata, dict):
        raise ReferenceError("音源情報の形式が違います")
    validate_metadata(metadata)
    suffix = Path(query.get("filename", [""])[0]).suffix.lower()
    length = int(handler.headers.get("Content-Length", "0"))
    if suffix not in EXTENSIONS or not 0 < length <= MAX_BYTES:
        raise ReferenceError("256MB以内の対応音源を選んでください")
    path = None
    try:
        with NamedTemporaryFile(
            prefix="kihachi-import-", suffix=suffix, delete=False
        ) as out:
            path = Path(out.name)
            remaining = length
            while remaining:
                chunk = handler.rfile.read(min(1024 * 1024, remaining))
                if not chunk:
                    raise ReferenceError("音源の送信が途中で止まりました")
                out.write(chunk)
                remaining -= len(chunk)
        return library.import_file(path, metadata)
    finally:
        if path:
            path.unlink(missing_ok=True)
