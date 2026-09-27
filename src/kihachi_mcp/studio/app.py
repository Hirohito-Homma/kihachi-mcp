"""Local Japanese studio UI. No cloud APIs, no model downloads."""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from kihachi_mcp.services.brief_coverage import model_filled_fields, read_coverage
from kihachi_mcp.services.live_paths import candidate_store_dir
from kihachi_mcp.services.studio_runtime import StudioRuntime

HOST = "127.0.0.1"
PORT = 8765
STATIC_DIR = Path(__file__).resolve().parent / "static"


class StudioApp:
    """Serve the studio page and JSON API on loopback."""

    def __init__(self, runtime: StudioRuntime | None = None) -> None:
        self.runtime = runtime or StudioRuntime(
            start_bridge=True, candidate_dir=Path(candidate_store_dir())
        )

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
            parsed = urlparse(self.path)
            if parsed.path == "/":
                self._send_file(STATIC_DIR / "index.html", "text/html; charset=utf-8")
                return
            if parsed.path == "/api/health":
                self._send_json(runtime.health())
                return
            if parsed.path == "/api/apply/last":
                last = runtime.last_apply()
                self._send_json(last or {"ok": False, "error": "適用結果はまだありません"})
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
                        ),
                    }
                )
                return
            self._send_json({"ok": False, "error": "not found"}, 404)

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            body = self._read_json()
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
                            parent_candidate_id=str(body.get("parent_candidate_id") or ""),
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
                    runtime.apply(
                        str(body.get("candidate_id") or ""),
                        confirmed=bool(body.get("confirmed")),
                        change_tempo=bool(body.get("change_tempo")),
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
