"""Local Japanese studio UI. No cloud APIs, no model downloads."""

from __future__ import annotations

import json
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from kihachi_mcp.services.brief_coverage import model_filled_fields, read_coverage
from kihachi_mcp.services.live_paths import candidate_store_dir
from kihachi_mcp.services.reference_library import ReferenceLibrary
from kihachi_mcp.services.reference_sources import ReferenceSources
from kihachi_mcp.services.sample_catalog import SampleCatalog
from kihachi_mcp.services.studio_dialogue import StudioDialogue
from kihachi_mcp.services.studio_runtime import StudioRuntime
from kihachi_mcp.services.voice_io import (
    MAX_AUDIO_BYTES,
    VoiceError,
    correct_voice_terms,
    synthesize_japanese,
    transcribe_japanese_options,
    voice_authorize,
    voice_status,
)
from kihachi_mcp.studio import reference_routes

HOST = "127.0.0.1"
PORT = 8765
STATIC_DIR = Path(__file__).resolve().parent / "static"


class StudioApp:
    """Serve the studio page and JSON API on loopback."""

    def __init__(
        self,
        runtime: StudioRuntime | None = None,
        reference_library: ReferenceLibrary | None = None,
        reference_sources: ReferenceSources | None = None,
        sample_catalog: SampleCatalog | None = None,
    ) -> None:
        self.runtime = runtime or StudioRuntime(
            start_bridge=True, candidate_dir=Path(candidate_store_dir())
        )
        self.reference_library = reference_library or ReferenceLibrary()
        self.reference_sources = reference_sources or ReferenceSources()
        self.sample_catalog = sample_catalog or SampleCatalog()
        self.dialogue = StudioDialogue(sample_catalog=self.sample_catalog)

    def serve_forever(self, host: str = HOST, port: int = PORT) -> None:
        """Start the blocking HTTP server."""
        handler = _handler_for(self)
        server = ThreadingHTTPServer((host, port), handler)
        try:
            server.serve_forever()
        finally:
            server.server_close()
            self.runtime.close()


def _handler_for(app: StudioApp):
    runtime = app.runtime

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            return

        def do_GET(self) -> None:
            self._guarded(self._get)

        def do_POST(self) -> None:
            origin = self.headers.get("Origin")
            if origin is not None and origin != "http://" + self.headers.get("Host", ""):
                self._send_json({"ok": False, "error": "Studioの画面から操作してください"}, 403)
                return
            self._guarded(self._post)

        def _guarded(self, handle: Any) -> None:
            try:
                handle()
            except Exception as exc:  # noqa: BLE001 - never show a traceback to the user
                detail = "".join(traceback.format_exception_only(type(exc), exc)).strip()
                self._send_json(
                    {
                        "ok": False,
                        "error": "処理中に予期しないエラーが起きました。もう一度試すか、診断を開いてください。",
                        "technical_detail": detail,
                    },
                    500,
                )

        def _get(self) -> None:
            parsed = urlparse(self.path)
            if _workflow_get(self, runtime, parsed):
                return
            if parsed.path == "/references":
                self._send_file(
                    STATIC_DIR / "references.html", "text/html; charset=utf-8"
                )
                return
            if reference_routes.handle_get(
                self,
                app.reference_library,
                parsed,
                app.reference_sources,
                app.sample_catalog,
            ):
                return
            if parsed.path == "/":
                self._send_file(STATIC_DIR / "index.html", "text/html; charset=utf-8")
                return
            if parsed.path == "/api/health":
                self._send_json(runtime.health())
                return
            if parsed.path == "/api/dialogue/voice/status":
                self._send_json(voice_status())
                return
            if parsed.path == "/api/apply/last":
                last = runtime.last_apply()
                self._send_json(
                    last or {"ok": False, "error": "適用結果はまだありません"}
                )
                return
            if parsed.path == "/api/coverage":
                query = parse_qs(parsed.query)
                candidate_id = (query.get("candidate_id") or [""])[0]
                selected = runtime.selected_candidate()
                if not candidate_id and selected is not None:
                    candidate_id = selected.candidate_id
                self._send_json(runtime.inspect_coverage(candidate_id))
                return
            if parsed.path == "/api/job":
                self._send_json(runtime.job_status())
                return
            if parsed.path.startswith("/api/candidate/"):
                parts = parsed.path.strip("/").split("/")
                if len(parts) < 3:
                    self._send_json({"ok": False, "error": "候補がありません"}, 404)
                    return
                candidate_id = parts[2]
                if len(parts) >= 4 and parts[3] in {"midi", "midi.mid"}:
                    exported = runtime.export_midi(candidate_id)
                    if not exported.get("ok"):
                        self._send_json(exported, 404)
                        return
                    data = Path(str(exported["path"])).read_bytes()
                    self._send_bytes(
                        data,
                        "audio/midi",
                        f'attachment; filename="kihachi-{candidate_id[:8]}.mid"',
                    )
                    return
                candidate = runtime.get_candidate(candidate_id)
                if candidate is None:
                    self._send_json({"ok": False, "error": "候補がありません"}, 404)
                    return
                self._send_json(
                    {
                        "ok": True,
                        "candidate": candidate.to_dict(),
                        "coverage": read_coverage(
                            candidate.brief.original_text,
                            model_filled_fields(candidate.brief),
                            str(candidate.brief.genre.value),
                        ),
                    }
                )
                return
            self._send_json({"ok": False, "error": "not found"}, 404)

        def _post(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path.startswith("/api/dialogue/"):
                content_type = self.headers.get("Content-Type", "").split(";")[0]
                allowed_types = (
                    {"audio/webm", "audio/mp4", "audio/ogg", "audio/wav"}
                    if parsed.path == "/api/dialogue/transcribe"
                    else {"application/json"}
                )
                if content_type not in allowed_types or self.headers.get(
                    "Origin", "http://" + self.headers.get("Host", "")
                ) != "http://" + self.headers.get("Host", ""):
                    self._send_json(
                        {"ok": False, "error": "Studioの画面から操作してください"}, 403
                    )
                    return
                if parsed.path == "/api/dialogue/transcribe":
                    try:
                        length = int(self.headers.get("Content-Length", "0"))
                        if not 100 <= length <= MAX_AUDIO_BYTES:
                            raise VoiceError("15秒以内・5MB以内の音声を送ってください")
                        recognition = transcribe_japanese_options(
                            self.rfile.read(length), content_type
                        )
                    except (VoiceError, ValueError) as exc:
                        self._send_json({"ok": False, "error": str(exc)}, 400)
                        return
                    raw_transcript = recognition["transcript"]
                    transcript = correct_voice_terms(raw_transcript)
                    alternatives = list(dict.fromkeys(
                        [transcript]
                        + [correct_voice_terms(choice) for choice in recognition["alternatives"]]
                        + ([raw_transcript] if raw_transcript != transcript else [])
                    ))[:5]
                    self._send_json({
                        "ok": True,
                        "transcript": transcript,
                        "raw_transcript": raw_transcript,
                        "alternatives": alternatives,
                        "on_device": True,
                    })
                    return
                body = self._read_json()
                if parsed.path == "/api/dialogue/voice/authorize":
                    try:
                        self._send_json(voice_authorize())
                    except VoiceError as exc:
                        self._send_json({"ok": False, "error": str(exc)}, 400)
                    return
                if parsed.path == "/api/dialogue/speak":
                    try:
                        data = synthesize_japanese(body.get("text"))
                    except VoiceError as exc:
                        self._send_json({"ok": False, "error": str(exc)}, 400)
                        return
                    self.send_response(200)
                    self.send_header("Content-Type", "audio/wav")
                    self.send_header("Content-Length", str(len(data)))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(data)
                    return
                if parsed.path == "/api/dialogue/turn":
                    self._send_json(
                        app.dialogue.turn(
                            body.get("session_id"),
                            body.get("utterance"),
                            body.get("brief"),
                        )
                    )
                    return
                if parsed.path == "/api/dialogue/accept":
                    self._send_json(
                        app.dialogue.accept(
                            body.get("session_id"),
                            body.get("proposal_id"),
                            body.get("brief"),
                        )
                    )
                    return
                self._send_json({"ok": False, "error": "not found"}, 404)
                return
            if reference_routes.handle_post(
                self,
                app.reference_library,
                parsed,
                app.reference_sources,
                app.sample_catalog,
            ):
                return
            body = self._read_json()
            if _workflow_post(self, runtime, parsed, body):
                return
            if parsed.path == "/api/materialize":
                seed = body.get("seed")
                if seed is None:
                    self._send_json({"ok": False, "error": "seed が必要です"}, 400)
                    return
                brief = body.get("brief")
                if not isinstance(brief, dict):
                    self._send_json({"ok": False, "error": "brief が必要です"}, 400)
                    return
                try:
                    self._send_json(
                        runtime.materialize(
                            brief,
                            seed=int(seed),
                            candidate_id=str(body.get("candidate_id") or "") or None,
                            parent_candidate_id=str(
                                body.get("parent_candidate_id") or ""
                            ),
                        )
                    )
                except (TypeError, ValueError) as exc:
                    self._send_json({"ok": False, "error": str(exc)}, 400)
                return
            if parsed.path == "/api/generate":
                brief = str(body.get("brief") or "")
                seed = body.get("seed")
                seed = int(seed) if seed is not None else None
                self._send_json(runtime.queue_generate(brief, seed=seed))
                return
            if parsed.path == "/api/regenerate":
                candidate_id = str(body.get("candidate_id") or "")
                self._send_json(runtime.queue_regenerate(candidate_id))
                return
            if parsed.path == "/api/cancel":
                self._send_json(runtime.cancel())
                return
            if parsed.path == "/api/apply/preview":
                self._send_json(
                    runtime.apply_preview(
                        str(body.get("candidate_id") or ""),
                        change_tempo=bool(body.get("change_tempo")),
                        skip_instruments=bool(body.get("skip_instruments")),
                    )
                )
                return
            if parsed.path == "/api/arrangement/preview":
                self._send_json(
                    runtime.arrangement_preview(str(body.get("candidate_id") or ""))
                )
                return
            if parsed.path == "/api/arrangement":
                self._send_json(
                    runtime.expand_arrangement(
                        str(body.get("candidate_id") or ""),
                        confirmed=bool(body.get("confirmed")),
                    )
                )
                return
            if parsed.path == "/api/fill-drums":
                self._send_json(
                    runtime.fill_drum_samples(
                        str(body.get("candidate_id") or ""),
                        confirmed=bool(body.get("confirmed")),
                    )
                )
                return
            if parsed.path == "/api/apply":
                self._send_json(
                    runtime.send_to_ableton(
                        str(body.get("candidate_id") or ""),
                        confirmed=bool(body.get("confirmed")),
                        change_tempo=bool(body.get("change_tempo")),
                        skip_instruments=bool(body.get("skip_instruments")),
                    )
                )
                return
            self._send_json({"ok": False, "error": "not found"}, 404)

        def _read_json(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > 100_000:
                return {}
            raw = self.rfile.read(length)
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                return {}
            return data if isinstance(data, dict) else {}

        def _send_json(self, payload: dict[str, Any], status: int = 200) -> None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _send_file(self, path: Path, content_type: str) -> None:
            if not path.is_file():
                self._send_json({"ok": False, "error": "ui missing"}, 500)
                return
            data = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _send_bytes(self, data: bytes, content_type: str, disposition: str) -> None:
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Disposition", disposition)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    return Handler


def _workflow_get(handler: Any, runtime: StudioRuntime, parsed: Any) -> bool:
    """Read-only workspace, settings and diagnostics routes."""
    query = parse_qs(parsed.query)
    candidate_id = (query.get("candidate_id") or [""])[0]
    if parsed.path == "/api/projects":
        handler._send_json({"ok": True, "projects": runtime.list_projects()})
    elif parsed.path == "/api/project":
        result = runtime.project(candidate_id or runtime.selected_id())
        handler._send_json(result, 200 if result.get("ok") else 404)
    elif parsed.path == "/api/settings":
        handler._send_json({"ok": True, "settings": runtime.settings()})
    elif parsed.path == "/api/ollama":
        handler._send_json(runtime.ollama_status())
    elif parsed.path == "/api/diagnostics":
        handler._send_json(runtime.diagnostics())
    elif parsed.path == "/api/ableton/plan":
        handler._send_json(runtime.ableton_plan(candidate_id or runtime.selected_id()))
    else:
        return False
    return True


def _workflow_post(
    handler: Any, runtime: StudioRuntime, parsed: Any, body: dict[str, Any]
) -> bool:
    """Review, revision, approval and Ableton routes. Live writes need approval."""
    candidate_id = str(body.get("candidate_id") or "")
    flags = {
        "change_tempo": bool(body.get("change_tempo")),
        "skip_instruments": bool(body.get("skip_instruments")),
    }
    path = parsed.path
    if path == "/api/select":
        handler._send_json(runtime.select(candidate_id))
    elif path == "/api/review":
        handler._send_json(runtime.review(candidate_id))
    elif path == "/api/review/ignore":
        handler._send_json(runtime.ignore_issue(candidate_id, str(body.get("issue_id") or "")))
    elif path == "/api/revision":
        bars = body.get("bars")
        try:
            span = (int(bars[0]), int(bars[1])) if bars else None
        except (TypeError, ValueError, IndexError):
            handler._send_json({"ok": False, "error": "小節の範囲は 33-49 のように入力してください"}, 400)
            return True
        handler._send_json(
            runtime.propose_revision(
                candidate_id,
                issue_ids=[str(item) for item in body.get("issue_ids") or []] or None,
                scopes=[str(item) for item in body.get("scopes") or []] or None,
                bars=span,
            )
        )
    elif path == "/api/revision/decide":
        handler._send_json(
            runtime.decide_revision(str(body.get("revision_id") or ""), bool(body.get("accept")))
        )
    elif path == "/api/approve":
        handler._send_json(runtime.approve(candidate_id))
    elif path == "/api/ableton/dry-run":
        handler._send_json(runtime.dry_run(candidate_id, **flags))
    elif path == "/api/ableton/send":
        handler._send_json(
            runtime.send_to_ableton(candidate_id, confirmed=bool(body.get("confirmed")), **flags)
        )
    elif path == "/api/ableton/verify":
        handler._send_json(runtime.verify(candidate_id))
    elif path == "/api/ableton/effects":
        handler._send_json(
            runtime.apply_effects(candidate_id, confirmed=bool(body.get("confirmed")))
        )
    elif path == "/api/ableton/effects/verify":
        handler._send_json(runtime.verify_effects(candidate_id))
    elif path == "/api/ableton/probe-devices":
        handler._send_json(runtime.probe_device_parameters(confirmed=bool(body.get("confirmed"))))
    elif path == "/api/settings":
        changes = body.get("settings")
        handler._send_json(
            runtime.update_settings(changes if isinstance(changes, dict) else {})
        )
    else:
        return False
    return True
