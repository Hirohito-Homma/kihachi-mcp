"""Local Japanese studio UI. No cloud APIs, no model downloads."""

from __future__ import annotations

import json
import traceback
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from kihachi_mcp.models.live_contract import OP_CREATE_MIDI_TRACK, STATUS_VERIFIED
from kihachi_mcp.models.live_mutation import LiveMutationPlan
from kihachi_mcp.models.music_plan import MusicPlan
from kihachi_mcp.services.brief_coverage import model_filled_fields, read_coverage
from kihachi_mcp.services.candidate_audio_preview import candidate_preview_wav
from kihachi_mcp.services.jarvis import JarvisOrchestrator, context_from_runtime
from kihachi_mcp.services.jarvis.intent import parse_intent
from kihachi_mcp.services.jarvis.revision import revision_request
from kihachi_mcp.services.jarvis.fake_execution import (
    rebind_uninspected_plan,
    run_approved_plan_on_fake,
)
from kihachi_mcp.services.jarvis.verification import settle_receipt
from kihachi_mcp.services.live_candidate_context import brief_from_live
from kihachi_mcp.services.live_transport_fake import FakeLiveSet, FakeLiveTransport
from kihachi_mcp.services.music_plan_preview import prepare_music_plan
from kihachi_mcp.services.live_paths import candidate_store_dir
from kihachi_mcp.services.reference_analysis import ReferenceError
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
        self.jarvis = JarvisOrchestrator(context_reader=lambda: context_from_runtime(self.runtime))
        self._music_plans: dict[str, LiveMutationPlan] = {}
        self._music_sources: dict[str, tuple[MusicPlan | None, dict[str, Any] | None]] = {}
        self._live_bound: set[str] = set()
        self.plan_simulator = FakeLiveTransport(FakeLiveSet(tempo=110.0))

    def hold_music_plan(
        self,
        plan: LiveMutationPlan,
        music: MusicPlan | None = None,
        instruments: dict[str, Any] | None = None,
        *,
        live_bound: bool = False,
    ) -> None:
        """Remember one previewed plan so a person can approve that exact hash."""
        self._music_plans[plan.plan_hash] = plan
        self._music_sources[plan.plan_hash] = (music, instruments)
        if live_bound:
            self._live_bound.add(plan.plan_hash)
        while len(self._music_plans) > 8:
            oldest = next((key for key in self._music_plans if key not in self._live_bound), None)
            if oldest is None:
                break
            self.drop_music_plan(oldest)

    def held_music_plan(self, plan_hash: str) -> LiveMutationPlan | None:
        return self._music_plans.get(plan_hash)

    def music_source(self, plan_hash: str) -> tuple[MusicPlan | None, dict[str, Any] | None]:
        return self._music_sources.get(plan_hash, (None, None))

    def is_live_bound(self, plan_hash: str) -> bool:
        return plan_hash in self._live_bound

    def forget_live_binding(self, plan_hash: str) -> None:
        self._live_bound.discard(plan_hash)

    def drop_music_plan(self, plan_hash: str) -> None:
        """Forget a superseded preview. Approval tokens stay in the gate until they expire."""
        self._music_plans.pop(plan_hash, None)
        self._music_sources.pop(plan_hash, None)
        self._live_bound.discard(plan_hash)

    def pending_actions(self) -> dict[str, Any]:
        """Part plans waiting to be approved again or sent, and undecided revisions. Nothing is changed."""
        gate = self.runtime._gate
        plans = []
        for plan_hash, plan in self._music_plans.items():
            approved = gate.holds(plan_hash)
            bound = plan_hash in self._live_bound
            if gate.is_expired(plan) or not (approved or bound):
                continue
            plans.append(
                {
                    "plan_hash": plan_hash,
                    "approved": approved,
                    "live_bound": bound,
                    "remaining_seconds": _remaining_seconds(plan),
                    "tracks": [
                        operation.arguments.get("name")
                        for operation in plan.operations
                        if operation.op == OP_CREATE_MIDI_TRACK
                    ],
                }
            )
        return {
            "ok": True,
            "music_plans": plans,
            "revisions": self.runtime.pending_revisions(),
            "live_changed": False,
        }

    def serve_forever(self, host: str = HOST, port: int = PORT) -> None:
        """Start the blocking HTTP server."""
        handler = _handler_for(self)
        server = ThreadingHTTPServer((host, port), handler)
        try:
            server.serve_forever()
        finally:
            server.server_close()
            self.runtime.close()


def _remaining_seconds(plan: LiveMutationPlan) -> int | None:
    """Seconds until this plan's inspect window closes. Unknown expiry is None."""
    if not plan.expires_at:
        return None
    try:
        expires = datetime.fromisoformat(plan.expires_at)
    except ValueError:
        return 0
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=UTC)
    return max(0, int((expires - datetime.now(UTC)).total_seconds()))


def _verify_music_plan(app: StudioApp, runtime: StudioRuntime, plan: LiveMutationPlan) -> dict[str, Any]:
    """Phase 4 on the in-memory set. Real Live is never contacted."""
    base = {
        "executed": False,
        "live_changed": False,
        "save": False,
        "paid_api": False,
        "musical_quality_claimed": False,
        "plan_hash": plan.plan_hash,
    }
    if not runtime._gate.holds(plan.plan_hash):
        return {
            **base,
            "ok": False,
            "state": "APPROVAL_REQUIRED",
            "reply": "承認されていない計画は検証で完了にしません。Liveには送っていません。",
        }
    if app.is_live_bound(plan.plan_hash):
        return {
            **base,
            "ok": False,
            "state": "APPROVAL_REQUIRED",
            "reply": "この計画は実機の Live に合わせてあります。偽の Set では試しません。",
        }
    if plan.set_fingerprint in ("", "uninspected"):
        rebound = rebind_uninspected_plan(plan, app.plan_simulator)
        app.hold_music_plan(rebound, *app.music_source(plan.plan_hash))
        if rebound.plan_hash != plan.plan_hash:
            app.drop_music_plan(plan.plan_hash)
        return {
            **base,
            "ok": False,
            "state": "APPROVAL_REQUIRED",
            "plan_hash": rebound.plan_hash,
            "reply": "偽の Set に合わせました。前の承認は無効です。もう一度承認してください。実機には送っていません。",
        }
    finished = run_approved_plan_on_fake(plan, runtime._gate, app.plan_simulator)
    finished["live_changed"] = False
    finished["save"] = False
    finished["paid_api"] = False
    finished["plan_hash"] = plan.plan_hash
    return finished


_BLOCKED_REASONS = (
    ("changed after", "読んだあとに Live の Set が変わりました"),
    ("transport is running", "Live が再生中です"),
    ("recording", "Live が録音中です"),
    ("expired", "Live を読んでから15分が過ぎました"),
)


def _send_music_plan_to_live(app: StudioApp, runtime: StudioRuntime, plan: LiveMutationPlan) -> dict[str, Any]:
    """The first press reads the real Set; after a new approval the second press sends once."""
    base = {
        "ok": False,
        "executed": False,
        "live_changed": False,
        "save": False,
        "paid_api": False,
        "musical_quality_claimed": False,
        "plan_hash": plan.plan_hash,
    }
    if not runtime._gate.holds(plan.plan_hash):
        return {**base, "state": "APPROVAL_REQUIRED", "reply": "承認されていない計画は実機に送りません。"}
    if not app.is_live_bound(plan.plan_hash):
        music, instruments = app.music_source(plan.plan_hash)
        if music is None:
            return {
                **base,
                "state": "UNKNOWN",
                "reply": "元の計画がありません。もう一度「計画を確認」を押してください。実機には送っていません。",
            }
        bound, payload = runtime.bind_music_plan_to_live(music, instruments)
        if bound is not None:
            app.hold_music_plan(bound, music, instruments, live_bound=True)
            if bound.plan_hash != plan.plan_hash:
                app.drop_music_plan(plan.plan_hash)
        return {**base, **payload}
    receipt, refusal = runtime.execute_music_plan(plan)
    if receipt is None:
        if "実行中" not in (refusal or ""):
            app.forget_live_binding(plan.plan_hash)
        return {**base, "state": "APPROVAL_REQUIRED", "reply": refusal}
    error = receipt.error or ""
    transport_busy = "transport is running" in error or "recording" in error
    if receipt.attempted_operations or not transport_busy:
        app.forget_live_binding(plan.plan_hash)
    if not receipt.attempted_operations:
        reason = next(
            (text for marker, text in _BLOCKED_REASONS if marker in error),
            error or "理由は不明です",
        )
        retry = (
            "止めてから、同じボタンをもう一度押してください。承認は残しています。"
            if transport_busy
            else "もう一度押すと Live を読み直します。"
        )
        return {
            **base,
            "status": receipt.status,
            "state": "APPROVAL_REQUIRED",
            "reply": f"送れませんでした（{reason}）。Live は変えていません。{retry}",
        }
    changed = bool(receipt.completed_operations)
    machine, message = settle_receipt(receipt.status)
    if receipt.status == STATUS_VERIFIED:
        message = (
            "実機の Live は計画どおり応答しました。音源は入れていないので、トラックに音源を載せてから聴いてください。"
            "音の完成ではありません。保存はしていません。"
        )
    elif changed:
        message = f"{message} 途中まで Live を変えています。自動では再送しません。Live で確かめてください。"
    return {
        **base,
        "ok": receipt.status == STATUS_VERIFIED,
        "status": receipt.status,
        "state": machine.state,
        "states": list(machine.history),
        "reply": message,
        "executed": changed,
        "live_changed": changed,
    }


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
            if parsed.path == "/api/jarvis":
                self._send_json(app.jarvis.status())
                return
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
            if parsed.path == "/api/live/overview":
                if runtime._transport is None:
                    self._send_json({"ok": False, "error": "Liveブリッジが起動していません"}, 503)
                    return
                try:
                    snapshot = runtime._inspector.snapshot(
                        include_arrangement=True,
                        count_session_notes=False,
                        include_session_clips=True,
                    )
                    self._send_json({
                        "ok": True,
                        "live_set": {
                            "tempo": snapshot.tempo,
                            "time_signature": f"{snapshot.signature_numerator}/{snapshot.signature_denominator}",
                            "tracks": [
                                {
                                    "name": t.name,
                                    "kind": t.kind,
                                    "clip_count": len(t.clips),
                                }
                                for t in snapshot.tracks
                            ],
                            "scene_count": len(snapshot.scenes),
                            "cue_points": list(snapshot.cue_points),
                        },
                        "live_changed": False,
                    })
                except Exception as exc:  # noqa: BLE001 - one read, no retry
                    self._send_json({"ok": False, "error": f"Liveを読み取れませんでした: {exc}"}, 503)
                return
            if parsed.path == "/api/approvals":
                self._send_json(app.pending_actions())
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
                if len(parts) >= 4 and parts[3] in {"preview.wav", "preview"}:
                    candidate = runtime.get_candidate(candidate_id)
                    if candidate is None:
                        self._send_json({"ok": False, "error": "候補がありません"}, 404)
                        return
                    self._send_bytes(
                        candidate_preview_wav(candidate), "audio/wav",
                        f'inline; filename="kihachi-{candidate_id[:8]}-preview.wav"',
                    )
                    return
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
            if parsed.path == "/api/jarvis/turn":
                body = self._read_json()
                self._send_json(
                    app.jarvis.ask(
                        str(body.get("utterance") or ""),
                        track_index=body.get("track_index"),
                        track_name=body.get("track_name"),
                        clip_index=body.get("clip_index"),
                    )
                )
                return
            if parsed.path == "/api/jarvis/clip-preview":
                body = self._read_json()
                self._send_json(
                    app.jarvis.inspect_midi_clip(
                        track_index=body.get("track_index"),
                        track_name=body.get("track_name"),
                        clip_index=body.get("clip_index"),
                        utterance=str(body.get("utterance") or ""),
                        direction=body.get("direction"),
                        scope=body.get("scope"),
                        bar_range=body.get("bar_range"),
                    )
                )
                return
            if parsed.path == "/api/jarvis/revision":
                body = self._read_json()
                selected = runtime.selected_id()
                project = runtime.project(selected) if selected else {}
                song = {
                    "bars": (project.get("songspec") or {}).get("bars"),
                    "sections": (project.get("arrangement") or {}).get("sections") or [],
                }
                request = revision_request(parse_intent(str(body.get("utterance") or "")), song)
                if not request["ok"]:
                    self._send_json({**request, "live_changed": False})
                    return
                result = runtime.propose_revision(
                    selected,
                    scopes=request["scopes"],
                    bars=request["bars"],
                    parts=request["parts"],
                )
                self._send_json({**result, "live_changed": False})
                return
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
            if parsed.path == "/api/samples/replace-preview":
                try:
                    sample_path = app.sample_catalog.audio_path(
                        str(body.get("sample_id") or "")
                    )
                except ReferenceError as exc:
                    self._send_json({"ok": False, "error": str(exc)}, 400)
                    return
                self._send_json(
                    runtime.sample_replacement_preview(
                        str(body.get("candidate_id") or runtime.selected_id()),
                        str(sample_path),
                    )
                )
                return
            if parsed.path == "/api/samples/replace":
                self._send_json(
                    runtime.apply_sample_replacement(
                        str(body.get("replacement_id") or ""),
                        confirmed=body.get("confirmed") is True,
                    )
                )
                return
            if parsed.path == "/api/music-plan/preview":
                posted = body.get("constraints")
                prepared = prepare_music_plan(posted if isinstance(posted, dict) else None)
                if prepared.live is not None:
                    app.hold_music_plan(prepared.live, prepared.music, prepared.instruments)
                self._send_json(prepared.payload)
                return
            if parsed.path == "/api/music-plan/approve":
                plan = app.held_music_plan(str(body.get("plan_hash") or ""))
                if plan is None:
                    self._send_json(
                        {
                            "ok": False,
                            "error": "先に計画を確認してください。",
                            "executed": False,
                            "live_changed": False,
                            "save": False,
                            "paid_api": False,
                        }
                    )
                    return
                self._send_json(runtime.record_music_plan_approval(plan))
                return
            if parsed.path == "/api/music-plan/reject":
                plan = app.held_music_plan(str(body.get("plan_hash") or ""))
                if plan is None:
                    self._send_json(
                        {
                            "ok": False,
                            "error": "先に計画を確認してください。",
                            "approved": False,
                            "rejected": False,
                            "executed": False,
                            "live_changed": False,
                            "save": False,
                            "paid_api": False,
                        }
                    )
                    return
                self._send_json(runtime.reject_music_plan(plan))
                return
            if parsed.path == "/api/music-plan/verify":
                plan = app.held_music_plan(str(body.get("plan_hash") or ""))
                if plan is None:
                    self._send_json(
                        {
                            "ok": False,
                            "state": "UNKNOWN",
                            "reply": "先に計画を確認してください。完了にはしていません。",
                            "executed": False,
                            "live_changed": False,
                            "save": False,
                            "paid_api": False,
                            "musical_quality_claimed": False,
                        }
                    )
                    return
                self._send_json(_verify_music_plan(app, runtime, plan))
                return
            if parsed.path == "/api/music-plan/live":
                plan = app.held_music_plan(str(body.get("plan_hash") or ""))
                if plan is None:
                    self._send_json(
                        {
                            "ok": False,
                            "state": "UNKNOWN",
                            "reply": "先に計画を確認してください。実機には送っていません。",
                            "executed": False,
                            "live_changed": False,
                            "save": False,
                            "paid_api": False,
                            "musical_quality_claimed": False,
                        }
                    )
                    return
                self._send_json(_send_music_plan_to_live(app, runtime, plan))
                return
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
            if parsed.path == "/api/live/candidate":
                if runtime._transport is None:
                    self._send_json({"ok": False, "error": "Liveブリッジが起動していません"}, 503)
                    return
                if runtime._settings.get("ai_provider") == "openai":
                    self._send_json({"ok": False, "error": "Liveからの候補生成はローカルAI設定で実行してください"}, 400)
                    return
                try:
                    snapshot = runtime._inspector.snapshot(
                        include_arrangement=True,
                        count_session_notes=False,
                        include_session_clips=True,
                    )
                    context = brief_from_live(snapshot, str(body.get("brief") or ""))
                except ValueError as exc:
                    self._send_json({"ok": False, "error": str(exc)}, 400)
                    return
                except Exception as exc:  # noqa: BLE001 - one read, no retry
                    self._send_json({"ok": False, "error": f"Liveを読み取れませんでした: {exc}"}, 503)
                    return
                result = runtime.queue_generate(str(context["brief"]))
                self._send_json({**result, "live_context": context, "live_changed": False})
                return
            if parsed.path == "/api/generate":
                brief = str(body.get("brief") or "")
                seed = body.get("seed")
                seed = int(seed) if seed is not None else None
                self._send_json(runtime.queue_generate(brief, seed=seed, confirmed_paid=bool(body.get("confirmed_paid"))))
                return
            if parsed.path == "/api/regenerate":
                candidate_id = str(body.get("candidate_id") or "")
                self._send_json(runtime.queue_regenerate(candidate_id, confirmed_paid=bool(body.get("confirmed_paid"))))
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
    elif parsed.path == "/api/ai/cost":
        handler._send_json(runtime.ai_cost_status((query.get("brief") or [""])[0]))
    elif parsed.path == "/api/ai/connection":
        handler._send_json(runtime.openai_connection_status())
    elif parsed.path == "/api/ollama":
        handler._send_json(runtime.ollama_status())
    elif parsed.path == "/api/diagnostics":
        handler._send_json(runtime.diagnostics())
    elif parsed.path == "/api/ableton/capabilities":
        handler._send_json(runtime.live_capabilities())
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
    elif path == "/api/ableton/sidechain":
        handler._send_json(
            runtime.apply_sidechain(candidate_id, confirmed=bool(body.get("confirmed")))
        )
    elif path == "/api/ableton/retune":
        handler._send_json(
            runtime.apply_retune(
                candidate_id,
                tuple(str(part) for part in body.get("parts") or ()),
                confirmed=bool(body.get("confirmed")),
            )
        )
    elif path == "/api/ableton/mix":
        handler._send_json(runtime.apply_mix(candidate_id, confirmed=bool(body.get("confirmed"))))
    elif path == "/api/ableton/device":
        handler._send_json(
            runtime.read_device(str(body.get("track") or ""), int(body.get("device_index") or 0))
        )
    elif path == "/api/ableton/master":
        handler._send_json(
            runtime.apply_master(
                confirmed=bool(body.get("confirmed")), retune=bool(body.get("retune"))
            )
        )
    elif path == "/api/measure":
        handler._send_json(runtime.measure_mix(str(body.get("path") or ""), candidate_id))
    elif path == "/api/ableton/effects/verify":
        handler._send_json(runtime.verify_effects(candidate_id))
    elif path == "/api/ableton/probe-devices":
        handler._send_json(runtime.probe_device_parameters(confirmed=bool(body.get("confirmed"))))
    elif path == "/api/settings":
        changes = body.get("settings")
        handler._send_json(
            runtime.update_settings(changes if isinstance(changes, dict) else {})
        )
    elif path == "/api/midi/preview":
        handler._send_json(
            runtime.midi_composer_preview(
                body.get("constraints") if isinstance(body.get("constraints"), dict) else {},
                str(body.get("operation") or "generate"),
                body.get("source_notes") if isinstance(body.get("source_notes"), list) else None,
            )
        )
    elif path == "/api/midi/compose":
        handler._send_json(
            runtime.compose_midi_part(
                body.get("constraints") if isinstance(body.get("constraints"), dict) else {},
                str(body.get("operation") or "generate"),
                body.get("source_notes") if isinstance(body.get("source_notes"), list) else None,
                confirmed_paid=bool(body.get("confirmed_paid")),
                seed=int(body.get("seed") or 0),
            )
        )
    elif path == "/api/midi/adopt-preview":
        handler._send_json(
            runtime.propose_composer_adoption(
                str(body.get("candidate_id") or ""),
                str(body.get("role") or ""),
                int(body.get("pattern_bars") or 0),
                body.get("notes") if isinstance(body.get("notes"), list) else [],
            )
        )
    else:
        return False
    return True
