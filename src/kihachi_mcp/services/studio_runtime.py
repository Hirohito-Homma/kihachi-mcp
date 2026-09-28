"""Process-local studio: one inference, stored candidates, one Live apply."""

from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from kihachi_mcp.knowledge.sound_recipes import recipe_for
from kihachi_mcp.models.live_contract import managed_track_name
from kihachi_mcp.models.midi_candidate import MidiCandidate
from kihachi_mcp.services.abletongpt_kits import AbletonGPTKitLoader, KitLoadError
from kihachi_mcp.services.ai_provider import provider_from_settings, split_ollama_url
from kihachi_mcp.services.candidate_live_planner import (
    AppliedTracks,
    CandidateLivePlanner,
)
from kihachi_mcp.services.diagnostics import (
    DiagnosticsService,
    default_settings,
    remote_script_state,
)
from kihachi_mcp.services.live_approval_gate import ApprovalError, ApprovalGate
from kihachi_mcp.services.live_bridge import LiveBridgeSession, LocalhostBridgeTransport
from kihachi_mcp.services.live_execution_service import LiveExecutionService
from kihachi_mcp.services.live_state_inspector import (
    LiveStateInspector,
    LiveVersionUnsupportedError,
)
from kihachi_mcp.services.live_transport import LiveTransport, LiveTransportError
from kihachi_mcp.services.midi_candidate_builder import build_candidate, seed_from_brief
from kihachi_mcp.services.midi_writer import candidate_to_midi_bytes
from kihachi_mcp.services.ollama_status import probe_ollama
from kihachi_mcp.services.production_review import review_candidate
from kihachi_mcp.services.production_revision import revise_candidate
from kihachi_mcp.services.production_workspace import local_ableton_plan, project_view
from kihachi_mcp.services.sound_coverage import coverage_from_snapshot
from kihachi_mcp.services.studio_interpreter import (
    DEFAULT_MODEL,
    InferenceCancelled,
    InterpretationError,
    OllamaClient,
    interpret_brief_offline,
    interpret_with_fallback,
)
from kihachi_mcp.services.studio_workflow import (
    EDITABLE_SETTINGS,
    expected_from_plan,
    list_saved_projects,
    readback_verification,
    revision_record,
    save_settings,
    system_statuses,
    validate_settings,
)

JOB_IDLE = "idle"
JOB_RUNNING = "running"
JOB_DONE = "done"
JOB_ERROR = "error"
JOB_CANCELLED = "cancelled"

#: Candidate ids become file names, so only these characters are accepted.
_CANDIDATE_ID_RE = re.compile(r"[0-9A-Za-z_-]{1,64}")
#: The dot keeps this file out of the candidate id pattern.
REVISION_HISTORY_FILE = ".kihachi-revisions.json"
#: A model reply that fails validation is asked for again at most this often.
AI_ATTEMPTS = 2


class StudioRuntime:
    """Own candidates, a single Ollama job, and one Live apply at a time."""

    def __init__(
        self,
        transport: LiveTransport | None = None,
        inspector: LiveStateInspector | None = None,
        executor: LiveExecutionService | None = None,
        planner: CandidateLivePlanner | None = None,
        gate: ApprovalGate | None = None,
        ollama_factory: Any = None,
        export_dir: Path | None = None,
        start_bridge: bool = False,
        candidate_dir: Path | None = None,
        kit_loader: AbletonGPTKitLoader | None = None,
    ) -> None:
        self._lock = threading.Lock()
        self._infer_lock = threading.Lock()
        self._apply_lock = threading.Lock()
        self._candidates: dict[str, MidiCandidate] = {}
        self._selected_id = ""
        self._job: dict[str, Any] = _idle_job()
        self._client: OllamaClient | None = None
        self._ollama_factory = ollama_factory or OllamaClient
        self._planner = planner or CandidateLivePlanner()
        self._gate = gate or ApprovalGate()
        self._bridge_session: LiveBridgeSession | None = None
        self._transport = transport
        if start_bridge and transport is None:
            self._bridge_session = LiveBridgeSession()
            self._bridge_session.write_handshake()
            self._transport = LocalhostBridgeTransport(
                session=self._bridge_session,
                timeout_seconds=40.0,
            )
        self._inspector = inspector or _PlanningInspector(
            self._transport, request_id_factory=_unique_request_ids()
        )
        self._coverage_inspector = (
            inspector
            if inspector is not None
            else _CoverageInspector(
                self._transport, request_id_factory=_unique_request_ids()
            )
        )
        self._executor = executor or LiveExecutionService(
            transport=self._transport,
            inspector=self._inspector,
            approval_gate=self._gate,
            request_id_factory=_unique_request_ids(),
        )
        # Arrangement expansion reads Arrangement clips, and the Set fingerprint
        # covers them, so planning and execution must read the Set the same way.
        self._arrangement_inspector = (
            inspector
            if inspector is not None
            else _ArrangementInspector(
                self._transport, request_id_factory=_unique_request_ids()
            )
        )
        self._arrangement_executor = executor or LiveExecutionService(
            transport=self._transport,
            inspector=self._arrangement_inspector,
            approval_gate=self._gate,
            request_id_factory=_unique_request_ids(),
        )
        self._arranged_ids: set[str] = set()
        # Core Library kits need Live's browser, which only a Remote Script can
        # reach. The Studio uses AbletonGPT's when it is selected in Live.
        self._kit_loader = kit_loader or (AbletonGPTKitLoader() if start_bridge else None)
        self._export_dir = Path(
            export_dir or Path.home() / "Music" / "KIHACHI" / "exports"
        )
        # Read-back verification counts Session notes, which planning skips.
        self._verify_inspector = (
            inspector
            if inspector is not None
            else _VerifyInspector(self._transport, request_id_factory=_unique_request_ids())
        )
        self._settings = default_settings()
        self._abletongpt_cache: tuple[float, str] | None = None
        self._approved_ids: set[str] = set()
        self._ignored_issues: dict[str, set[str]] = {}
        self._pending_revisions: dict[str, dict[str, Any]] = {}
        self._pending_children: dict[str, MidiCandidate] = {}
        self._revision_history: list[dict[str, Any]] = []
        self._expected: dict[str, dict[str, Any]] = {}
        self._verifications: dict[str, dict[str, Any]] = {}
        self._last_apply: dict[str, Any] | None = None
        self._applied_ids: set[str] = set()
        # None keeps candidates in memory only, as tests and the MCP server do.
        self._candidate_dir = Path(candidate_dir) if candidate_dir else None
        if self._candidate_dir is not None:
            self._selected_id = _latest_saved_id(self._candidate_dir)
            self._revision_history = _read_json_list(
                self._candidate_dir / REVISION_HISTORY_FILE
            )

    def close(self) -> None:
        """Release the Live bridge if this runtime created it."""
        if self._bridge_session is not None and isinstance(
            self._transport, LocalhostBridgeTransport
        ):
            self._transport.close()

    def health(self) -> dict[str, Any]:
        """Return Ollama and Live connection states without mutating anything."""
        host, port = split_ollama_url(str(self._settings["ollama_url"]))
        model = str(self._settings.get("ollama_model") or DEFAULT_MODEL)
        ollama = probe_ollama(host, port, model)
        if self._settings.get("ai_provider") == "deterministic":
            ollama = {
                **ollama,
                "ok": True,
                "state": "ready",
                "message": "AIなしの既定解釈を使います",
            }
        live = self._live_health()
        abletongpt = self._abletongpt_state()
        return {
            "ollama": ollama,
            "live": live,
            "abletongpt": {"state": abletongpt},
            "inference": self.job_status(),
            "selected_candidate_id": self._selected_id,
            "candidate_ids": list(self._candidates),
            "paid_api": False,
            "model_download": False,
            "statuses": system_statuses(ollama, live, abletongpt, self._selected_id),
            "settings": self.settings(),
        }

    def _abletongpt_state(self) -> str | None:
        """Ping AbletonGPT at most every 15 s; a blocked Live makes each ping slow."""
        if self._kit_loader is None:
            return None
        now = time.monotonic()
        cached = self._abletongpt_cache
        if cached is not None and now - cached[0] < 15.0:
            return cached[1]
        state = remote_script_state(
            int(self._settings.get("abletongpt_port") or 9877), self._kit_loader.available
        )
        self._abletongpt_cache = (now, state)
        return state

    def settings(self) -> dict[str, Any]:
        """Settings the Studio shows. Nothing secret is kept here."""
        return {
            key: self._settings[key]
            for key in (
                "ai_provider",
                "ollama_url",
                "ollama_model",
                "ableton_host",
                "ableton_port",
                "abletongpt_port",
                "default_bpm",
                "default_bars",
                "default_style",
                "project_dir",
            )
            if key in self._settings
        }

    def update_settings(self, changes: dict[str, Any], persist: bool = True) -> dict[str, Any]:
        """Change the AI provider, Ollama URL or installed model. Never downloads."""
        picked = {key: changes[key] for key in EDITABLE_SETTINGS if key in changes}
        if not picked:
            return {"ok": False, "error": "変更する設定がありません"}
        candidate = {**self._settings, **picked}
        try:
            provider = provider_from_settings(candidate)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        installed = provider.list_models() if candidate.get("ai_provider") == "ollama" else []
        error = validate_settings(picked, installed)
        if error:
            return {"ok": False, "error": error}
        self._settings = candidate
        if persist:
            save_settings(self._settings)
        return {"ok": True, "settings": self.settings()}

    def ollama_status(self) -> dict[str, Any]:
        """Provider health, installed models and the selected one."""
        try:
            provider = provider_from_settings(self._settings)
        except ValueError as exc:
            return {"ok": False, "state": "invalid_url", "message": str(exc), "installed_models": []}
        report = provider.health()
        return {
            **report,
            "selected_model": self._settings.get("ollama_model"),
            "capabilities": provider.capabilities(),
        }

    def diagnostics(self) -> dict[str, Any]:
        """The same checklist `kihachi doctor` prints, with this Studio's Live state."""
        probe = self._kit_loader.available if self._kit_loader is not None else None
        return DiagnosticsService(
            self._settings, live_probe=self._live_health, remote_script_probe=probe
        ).run()

    def queue_generate(self, brief: str, seed: int | None = None) -> dict[str, Any]:
        """Start one background generate. Refuses a second concurrent job."""
        if self._infer_lock.locked():
            return {
                "ok": False,
                "error": "別の生成が実行中です。完了またはキャンセルを待ってください",
            }
        thread = threading.Thread(
            target=self.generate, args=(brief, seed), daemon=True
        )
        thread.start()
        return {"ok": True, "started": True, "job": self.job_status()}

    def queue_regenerate(self, candidate_id: str) -> dict[str, Any]:
        """Start one background regenerate from an existing candidate."""
        if self._infer_lock.locked():
            return {
                "ok": False,
                "error": "別の生成が実行中です。完了またはキャンセルを待ってください",
            }
        thread = threading.Thread(
            target=self.regenerate, args=(candidate_id,), daemon=True
        )
        thread.start()
        return {"ok": True, "started": True, "job": self.job_status()}

    def materialize(
        self,
        brief: Any,
        seed: int,
        candidate_id: str | None = None,
        parent_candidate_id: str = "",
    ) -> dict[str, Any]:
        """Build a candidate from an already-interpreted brief. Skips Ollama."""
        from kihachi_mcp.models.production_brief import ProductionBrief

        production = (
            brief if isinstance(brief, ProductionBrief) else ProductionBrief.from_dict(brief)
        )
        candidate = build_candidate(
            production,
            seed=seed,
            candidate_id=candidate_id,
            parent_candidate_id=parent_candidate_id,
        )
        self._store(candidate)
        return {
            "ok": True,
            "candidate": candidate.to_dict(),
            "musical_quality_claimed": False,
        }

    def generate(self, brief: str, seed: int | None = None) -> dict[str, Any]:
        """Interpret the brief and store a new candidate. Blocks one caller."""
        if not self._infer_lock.acquire(blocking=False):
            return {
                "ok": False,
                "error": "別の生成が実行中です。完了またはキャンセルを待ってください",
            }
        started = time.monotonic()
        with self._lock:
            self._job = {
                "state": JOB_RUNNING,
                "started_at": time.time(),
                "elapsed_seconds": 0.0,
                "error": "",
                "candidate_id": "",
            }
        try:
            host, port = split_ollama_url(str(self._settings["ollama_url"]))
            model = str(self._settings.get("ollama_model") or DEFAULT_MODEL)
            ollama = probe_ollama(host, port, model)
            if ollama["ok"] and self._settings.get("ai_provider") != "deterministic":

                def new_client() -> Any:
                    client = (
                        OllamaClient(host, port)
                        if self._ollama_factory is OllamaClient
                        else self._ollama_factory()
                    )
                    with self._lock:
                        self._client = client
                    return client

                production = interpret_with_fallback(
                    brief, new_client, model=model, attempts=AI_ATTEMPTS
                )
            else:
                production = interpret_brief_offline(brief)
            candidate = build_candidate(production, seed=seed)
            self._store(candidate)
            payload = candidate.to_dict()
            with self._lock:
                self._job = {
                    "state": JOB_DONE,
                    "started_at": self._job["started_at"],
                    "elapsed_seconds": time.monotonic() - started,
                    "error": "",
                    "candidate_id": candidate.candidate_id,
                }
            return {
                "ok": True,
                "elapsed_seconds": time.monotonic() - started,
                "candidate": payload,
                "musical_quality_claimed": False,
            }
        except InferenceCancelled:
            with self._lock:
                self._job = {
                    "state": JOB_CANCELLED,
                    "started_at": self._job["started_at"],
                    "elapsed_seconds": time.monotonic() - started,
                    "error": "推論をキャンセルしました",
                    "candidate_id": "",
                }
            return {"ok": False, "cancelled": True, "error": "推論をキャンセルしました"}
        except (InterpretationError, ValueError, OSError) as exc:
            with self._lock:
                self._job = {
                    "state": JOB_ERROR,
                    "started_at": self._job["started_at"],
                    "elapsed_seconds": time.monotonic() - started,
                    "error": str(exc),
                    "candidate_id": "",
                }
            return {"ok": False, "error": str(exc)}
        finally:
            with self._lock:
                self._client = None
            self._infer_lock.release()

    def regenerate(self, candidate_id: str) -> dict[str, Any]:
        """Build a new candidate from the same brief with a new seed."""
        current = self._lookup(candidate_id)
        if current is None:
            return {"ok": False, "error": "指定した候補がありません"}
        next_seed = current.seed + 1
        return self.generate(current.brief.original_text, seed=next_seed)

    def cancel(self) -> dict[str, Any]:
        """Ask the in-flight Ollama request to stop."""
        with self._lock:
            client = self._client
        if client is None:
            return {"ok": False, "error": "実行中の生成はありません"}
        client.cancel()
        return {"ok": True, "message": "キャンセルを要求しました"}

    def job_status(self) -> dict[str, Any]:
        """Return generation progress, including elapsed time."""
        with self._lock:
            job = dict(self._job)
        if job["state"] == JOB_RUNNING:
            job["elapsed_seconds"] = time.time() - float(job["started_at"])
        return job

    def get_candidate(self, candidate_id: str) -> MidiCandidate | None:
        """Return a stored candidate."""
        return self._lookup(candidate_id)

    def selected_candidate(self) -> MidiCandidate | None:
        """Return the candidate currently shown as selected."""
        return self._lookup(self._selected_id)

    def selected_id(self) -> str:
        return self._selected_id

    def select(self, candidate_id: str) -> dict[str, Any]:
        """Open a saved project in the workspace. Nothing is regenerated."""
        candidate = self._lookup(candidate_id)
        if candidate is None:
            return {"ok": False, "error": "指定した候補がありません"}
        self._selected_id = candidate.candidate_id
        return {"ok": True, "candidate": candidate.to_dict()}

    def export_midi(self, candidate_id: str, directory: Path | None = None) -> dict[str, Any]:
        """Write a Standard MIDI File for the stored candidate."""
        candidate = self._lookup(candidate_id)
        if candidate is None:
            return {"ok": False, "error": "指定した候補がありません"}
        folder = Path(directory or self._export_dir)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"kihachi-{candidate.candidate_id[:8]}.mid"
        path.write_bytes(candidate_to_midi_bytes(candidate))
        return {"ok": True, "path": str(path), "bytes": path.stat().st_size}

    def apply_preview(
        self, candidate_id: str, change_tempo: bool = False, skip_instruments: bool = False
    ) -> dict[str, Any]:
        """Show what would be applied without touching Live."""
        candidate = self._lookup(candidate_id)
        if candidate is None:
            return {"ok": False, "error": "指定した候補がありません"}
        snapshot, failure = self._snapshot()
        if snapshot is None:
            return failure or {"ok": False, "error": "Live状態を取得できません"}
        kit_parts, kit_notes = self._kit_parts(candidate, skip_instruments)
        plan = self._planner.create_plan(
            candidate,
            snapshot,
            change_tempo=change_tempo,
            skip_instruments=skip_instruments,
            external_kit_parts=kit_parts,
        )
        planned_notes = _notes_from_plan(plan)
        preview_notes = _notes_from_candidate(candidate)
        notes_match = planned_notes == preview_notes and bool(preview_notes)
        return {
            "ok": plan.status != "blocked",
            "candidate_id": candidate.candidate_id,
            "note_fingerprint": candidate.note_fingerprint,
            "notes_match_preview": notes_match,
            "status": plan.status,
            "plan": plan.to_dict(),
            "summary": {
                **_apply_summary(candidate, plan, snapshot, change_tempo),
                "skip_instruments": skip_instruments,
                "kits": sorted(kit_parts),
            },
            "conflicts": [item.to_dict() for item in plan.conflicts],
            "warnings": list(plan.warnings) + kit_notes,
        }

    def apply(
        self,
        candidate_id: str,
        confirmed: bool = False,
        change_tempo: bool = False,
        skip_instruments: bool = False,
    ) -> dict[str, Any]:
        """Apply one confirmed candidate once. Never auto-retries."""
        if not confirmed:
            return {"ok": False, "error": "適用内容を確認してから実行してください"}
        if candidate_id in self._applied_ids or self._applied_on_disk(candidate_id):
            return {
                "ok": False,
                "error": (
                    "この候補はすでに適用を試行済みです。"
                    "通信タイムアウト後も自動再実行しないため、重複配置を防げます。"
                    "必要なら別候補を生成してください"
                ),
            }
        if not self._apply_lock.acquire(blocking=False):
            return {"ok": False, "error": "別のLive適用が実行中です"}
        try:
            preview = self.apply_preview(
                candidate_id, change_tempo=change_tempo, skip_instruments=skip_instruments
            )
            if not preview.get("ok"):
                return preview
            if not preview.get("notes_match_preview"):
                return {
                    "ok": False,
                    "error": "プレビューと適用計画のノートが一致しないため中止しました",
                    "preview": preview,
                }
            candidate = self._lookup(candidate_id)
            assert candidate is not None  # apply_preview already found it
            from kihachi_mcp.models.live_mutation import LiveMutationPlan

            plan = LiveMutationPlan.from_dict(preview["plan"])
            try:
                token = self._gate.approve(plan)
            except ApprovalError as exc:
                return {"ok": False, "error": exc.message, "preview": preview}
            self._applied_ids.add(candidate_id)
            self._mark_applied(candidate_id)
            self._remember_expected(
                candidate_id, expected_from_plan(preview["plan"]), change_tempo
            )
            receipt = self._executor.execute(
                plan, approved=True, approval_token=token
            )
            kits = []
            if receipt.status == "verified":
                kits = self._load_kits(
                    candidate, plan, frozenset(preview["summary"].get("kits") or [])
                )
            coverage = self._coverage_after(candidate, receipt)
            result = {
                "ok": receipt.status == "verified" and all(item["ok"] for item in kits),
                "candidate_id": candidate.candidate_id,
                "note_fingerprint": candidate.note_fingerprint,
                "receipt": receipt.to_dict(),
                "kits": kits,
                "sound_coverage": coverage,
                "partial": receipt.status == "partially_applied",
                "musical_quality_claimed": False,
                "verified_means": (
                    "Liveが計画どおり応答したこと。音楽的な良し悪しは含みません"
                ),
            }
            self._last_apply = result
            return result
        finally:
            self._apply_lock.release()

    def _kit_parts(
        self, candidate: MidiCandidate, skip_instruments: bool
    ) -> tuple[frozenset[str], list[str]]:
        """Drum parts whose Core Library kit AbletonGPT will load after apply."""
        recipe = recipe_for(str(candidate.brief.genre.value))
        if skip_instruments or recipe is None or not recipe.kits:
            return frozenset(), []
        if self._kit_loader is None or not self._kit_loader.available():
            return frozenset(), [
                (
                    "AbletonGPT に接続できないため、付属キットの代わりに Drum Rack と同梱サンプルを使います"
                    "（Live の Control Surface で AbletonGPT_MCP を選ぶと付属キットを使えます）"
                )
            ]
        names = "、".join(f"{part}: {recipe.kits[part][0]}" for part in sorted(recipe.kits))
        return frozenset(recipe.kits), [
            f"適用後に AbletonGPT 経由で付属キットを読み込みます（{names}）"
        ]

    def _load_kits(
        self, candidate: MidiCandidate, plan: Any, parts: frozenset[str]
    ) -> list[dict[str, Any]]:
        """One load per empty drum track, after the Live plan verified. No retry."""
        recipe = recipe_for(str(candidate.brief.genre.value))
        if not parts or recipe is None or self._kit_loader is None:
            return []
        short = candidate.candidate_id[:8]
        indexes = {
            str(operation.arguments.get("name")): int(operation.target["track_index"])
            for operation in plan.operations
            if operation.op == "create_midi_track"
        }
        results = []
        for part in sorted(parts):
            name = managed_track_name(f"KIHACHI {part} {short}")
            index = indexes.get(name)
            if index is None:
                snapshot, _failure = self._coverage_snapshot()
                track = snapshot.track_by_name(name) if snapshot is not None else None
                index = track.index if track is not None else None
            if index is None:
                results.append({"part": part, "ok": False, "error": f"{name} が見つかりません"})
                continue
            try:
                kit = self._kit_loader.load(index, recipe.kits[part])
            except KitLoadError as exc:
                results.append({"part": part, "ok": False, "error": str(exc)})
                continue
            results.append({"part": part, "ok": True, "kit": kit, "track_index": index})
        return results

    def arrangement_preview(self, candidate_id: str) -> dict[str, Any]:
        """Show where the Session clips would go in the Arrangement. No changes."""
        candidate = self._lookup(candidate_id)
        if candidate is None:
            return {"ok": False, "error": "指定した候補がありません"}
        try:
            snapshot = self._arrangement_inspector.snapshot()
        except LiveTransportError as exc:
            return {"ok": False, "error": f"{exc.code}: {exc.message}"}
        except LiveVersionUnsupportedError as exc:
            return {"ok": False, "error": str(exc)}
        plan = self._planner.create_arrangement_plan(candidate, snapshot)
        placements = [
            operation for operation in plan.operations
            if operation.op == "place_arrangement_clip"
        ]
        beats = snapshot.time_signature.beats_per_bar
        last_bar = max(
            (
                (operation.arguments["start_beats"] + operation.arguments["length_beats"])
                / beats
                for operation in placements
            ),
            default=0,
        )
        return {
            "ok": plan.status != "blocked",
            "candidate_id": candidate.candidate_id,
            "status": plan.status,
            "plan": plan.to_dict(),
            "summary": {
                "set_name": snapshot.set_name,
                "clip_count": len(placements),
                "locators": [
                    f"{section.name} {section.start_bar}小節目"
                    for section in candidate.brief.sections
                ],
                "bars": round(last_bar),
                "already_expanded": self._arranged(candidate_id),
            },
            "conflicts": [item.to_dict() for item in plan.conflicts],
            "warnings": list(plan.warnings),
            "musical_quality_claimed": False,
        }

    def expand_arrangement(
        self, candidate_id: str, confirmed: bool = False
    ) -> dict[str, Any]:
        """Copy one applied candidate into the Arrangement once. Never retries."""
        if not confirmed:
            return {"ok": False, "error": "展開内容を確認してから実行してください"}
        if self._arranged(candidate_id):
            return {
                "ok": False,
                "error": (
                    "この候補はすでにアレンジメントへの展開を試行済みです。"
                    "重複配置を防ぐため再実行しません"
                ),
            }
        if not self._apply_lock.acquire(blocking=False):
            return {"ok": False, "error": "別のLive適用が実行中です"}
        try:
            preview = self.arrangement_preview(candidate_id)
            if not preview.get("ok"):
                return preview
            from kihachi_mcp.models.live_mutation import LiveMutationPlan

            plan = LiveMutationPlan.from_dict(preview["plan"])
            try:
                token = self._gate.approve(plan)
            except ApprovalError as exc:
                return {"ok": False, "error": exc.message, "preview": preview}
            self._arranged_ids.add(candidate_id)
            self._mark(candidate_id, ".arranged")
            receipt = self._arrangement_executor.execute(
                plan, approved=True, approval_token=token
            )
            return {
                "ok": receipt.status == "verified",
                "candidate_id": candidate_id,
                "receipt": receipt.to_dict(),
                "partial": receipt.status == "partially_applied",
                "musical_quality_claimed": False,
                "verified_means": (
                    "Liveが計画どおりクリップとロケーターを置いたこと。"
                    "音楽的な良し悪しは含みません"
                ),
            }
        finally:
            self._apply_lock.release()

    def _arranged(self, candidate_id: str) -> bool:
        if candidate_id in self._arranged_ids:
            return True
        path = self._candidate_path(candidate_id, ".arranged")
        return path is not None and path.is_file()

    def last_apply(self) -> dict[str, Any] | None:
        """Return the most recent apply result."""
        return self._last_apply

    # --- Project workspace, review, revision and approval -------------------

    def list_projects(self) -> list[dict[str, Any]]:
        """Saved candidates newest first; in-memory ones when nothing is saved."""
        if self._candidate_dir is not None:
            return list_saved_projects(self._candidate_dir)
        rows = []
        for candidate in reversed(list(self._candidates.values())):
            brief = candidate.brief
            rows.append(
                {
                    "candidate_id": candidate.candidate_id,
                    "parent_candidate_id": candidate.parent_candidate_id,
                    "title": (brief.original_text.strip().splitlines() or [""])[0][:60],
                    "tempo": brief.tempo.value,
                    "key": brief.key.value,
                    "genre": brief.genre.value,
                    "bars": brief.bars.value,
                    "notes": candidate.note_count(),
                    "approved": self.is_approved(candidate.candidate_id),
                    "applied": candidate.candidate_id in self._applied_ids,
                    "arranged": candidate.candidate_id in self._arranged_ids,
                }
            )
        return rows

    def project(self, candidate_id: str) -> dict[str, Any]:
        """Everything the workspace shows for one candidate. Live is not contacted."""
        candidate = self._lookup(candidate_id)
        if candidate is None:
            return {"ok": False, "error": "指定した候補がありません"}
        lineage = self._lineage(candidate)
        return {
            "ok": True,
            **project_view(candidate),
            "review": self.review(candidate_id),
            "status": {
                "approved": self.is_approved(candidate_id),
                "applied": candidate_id in self._applied_ids or self._applied_on_disk(candidate_id),
                "arranged": self._arranged(candidate_id),
                "verification": self._verifications.get(candidate_id),
            },
            "revisions": [
                item for item in self._revision_history
                if item.get("parent_candidate_id") in lineage
                or item.get("child_candidate_id") in lineage
            ],
            "pending_revisions": [
                _public_revision(item)
                for item in self._pending_revisions.values()
                if item["parent_candidate_id"] == candidate_id
            ],
            "musical_quality_claimed": False,
        }

    def review(self, candidate_id: str) -> dict[str, Any]:
        """Review the notes that would be sent. Ignored issues stay listed apart."""
        candidate = self._lookup(candidate_id)
        if candidate is None:
            return {"ok": False, "error": "指定した候補がありません"}
        report = review_candidate(candidate)
        ignored = self._ignored_issues.get(candidate_id, set())
        report["ignored"] = [item for item in report["issues"] if item["id"] in ignored]
        report["issues"] = [item for item in report["issues"] if item["id"] not in ignored]
        report["approved"] = self.is_approved(candidate_id)
        return report

    def ignore_issue(self, candidate_id: str, issue_id: str) -> dict[str, Any]:
        """Hide one review issue for this candidate. Notes are not changed."""
        if self._lookup(candidate_id) is None:
            return {"ok": False, "error": "指定した候補がありません"}
        self._ignored_issues.setdefault(candidate_id, set()).add(str(issue_id))
        return {"ok": True, "review": self.review(candidate_id)}

    def propose_revision(
        self,
        candidate_id: str,
        issue_ids: list[str] | None = None,
        scopes: list[str] | None = None,
        bars: tuple[int, int] | None = None,
    ) -> dict[str, Any]:
        """Build a local revision as a proposal. Nothing is adopted until accepted."""
        candidate = self._lookup(candidate_id)
        if candidate is None:
            return {"ok": False, "error": "指定した候補がありません"}
        ignored = self._ignored_issues.get(candidate_id, set())
        wanted = [item for item in issue_ids or [] if item not in ignored] or None
        if issue_ids and wanted is None:
            return {"ok": False, "error": "選んだ指摘はすべて無視されています"}
        result = revise_candidate(candidate, scopes=scopes, issue_ids=wanted, bars=bars)
        if not result.get("ok"):
            return {"ok": False, "error": result.get("error") or "修正案を作れませんでした"}
        record = revision_record(result)
        self._pending_revisions[record["revision_id"]] = record
        self._pending_children[record["child_candidate_id"]] = result["candidate"]
        child = result["candidate"]
        return {
            "ok": True,
            "revision": _public_revision(record),
            "note_count_before": candidate.note_count(),
            "note_count_after": child.note_count(),
            "requires_approval": True,
            "musical_quality_claimed": False,
        }

    def decide_revision(self, revision_id: str, accept: bool) -> dict[str, Any]:
        """The human adopts or rejects a proposed revision. Rejection keeps the parent."""
        record = self._pending_revisions.pop(str(revision_id), None)
        if record is None:
            return {"ok": False, "error": "この修正案は見つからないか、すでに判断済みです"}
        child = self._pending_children.pop(record["child_candidate_id"], None)
        record = {**record, "decision": "accepted" if accept else "rejected", "decided_at": time.time()}
        record.pop("review_after", None)
        if accept and child is not None:
            self._store(child)
        self._revision_history.append(record)
        if self._candidate_dir is not None:
            _write_json_atomic(
                self._candidate_dir / REVISION_HISTORY_FILE, self._revision_history[-200:]
            )
        return {
            "ok": True,
            "decision": record["decision"],
            "selected_candidate_id": self._selected_id,
            "revision": record,
        }

    def approve(self, candidate_id: str) -> dict[str, Any]:
        """Human approval of this take. Required before anything is sent to Live."""
        if self._lookup(candidate_id) is None:
            return {"ok": False, "error": "指定した候補がありません"}
        self._approved_ids.add(candidate_id)
        self._mark(candidate_id, ".approved")
        return {"ok": True, "candidate_id": candidate_id, "approved": True}

    def is_approved(self, candidate_id: str) -> bool:
        if candidate_id in self._approved_ids:
            return True
        path = self._candidate_path(candidate_id, ".approved")
        return path is not None and path.is_file()

    # --- Ableton: dry run, send, read-back verification -----------------------

    def ableton_plan(self, candidate_id: str) -> dict[str, Any]:
        """The plan from the candidate alone. Works without Live."""
        candidate = self._lookup(candidate_id)
        if candidate is None:
            return {"ok": False, "error": "指定した候補がありません"}
        return {**local_ableton_plan(candidate), "candidate_id": candidate_id}

    def dry_run(
        self, candidate_id: str, change_tempo: bool = False, skip_instruments: bool = False
    ) -> dict[str, Any]:
        """Show exactly what Send to Ableton would do. Nothing in Live changes."""
        plan = self.ableton_plan(candidate_id)
        if not plan.get("ok"):
            return plan
        live = self._live_health()
        preview: dict[str, Any] | None = None
        if live.get("state") == "ready":
            preview = self.apply_preview(
                candidate_id, change_tempo=change_tempo, skip_instruments=skip_instruments
            )
        operations: dict[str, int] = {}
        for operation in ((preview or {}).get("plan") or {}).get("operations") or []:
            operations[operation["op"]] = operations.get(operation["op"], 0) + 1
        approved = self.is_approved(candidate_id)
        already = candidate_id in self._applied_ids or self._applied_on_disk(candidate_id)
        blockers = []
        if not approved:
            blockers.append("まだ承認されていません。レビューを確認して「この候補を承認」を押してください")
        if already:
            blockers.append("この候補はすでに送信を試行済みです（重複配置を防ぐため再送しません）")
        if preview is None:
            blockers.append("Liveに接続していないため、Setとの照合ができません（EXTERNAL VERIFICATION REQUIRED）")
        elif not preview.get("ok"):
            blockers.append(preview.get("error") or "Liveの状態と計画が衝突しています")
        elif not preview.get("notes_match_preview"):
            blockers.append("プレビューと適用計画のノートが一致しません")
        return {
            "ok": True,
            "candidate_id": candidate_id,
            "plan": plan,
            "live": {"state": live.get("state"), "message": live.get("message")},
            "live_preview": preview,
            "live_operations": operations,
            "approved": approved,
            "can_send": not blockers,
            "blockers": blockers,
            "changes_live": False,
        }

    def send_to_ableton(
        self,
        candidate_id: str,
        confirmed: bool = False,
        change_tempo: bool = False,
        skip_instruments: bool = False,
    ) -> dict[str, Any]:
        """Approved candidates only: apply once, then read Live back and compare."""
        if not self.is_approved(candidate_id):
            return {
                "ok": False,
                "error": "承認されていない候補はLiveへ送りません。先に「この候補を承認」を押してください",
            }
        result = self.apply(
            candidate_id,
            confirmed=confirmed,
            change_tempo=change_tempo,
            skip_instruments=skip_instruments,
        )
        if (result.get("receipt") or {}).get("status") == "verified":
            result["verification"] = self.verify(candidate_id)
        return result

    def verify(self, candidate_id: str) -> dict[str, Any]:
        """Read tempo, tracks, clips, notes and arrangement bounds back from Live."""
        candidate = self._lookup(candidate_id)
        if candidate is None:
            return {"ok": False, "error": "指定した候補がありません"}
        try:
            snapshot = self._verify_inspector.snapshot()
        except LiveTransportError as exc:
            return {
                "ok": False,
                "status": "EXTERNAL VERIFICATION REQUIRED",
                "error": f"Liveから読み戻せません（{exc.code}）: {exc.message}",
                "lines": [],
            }
        except LiveVersionUnsupportedError as exc:
            return {"ok": False, "status": "EXTERNAL VERIFICATION REQUIRED", "error": str(exc), "lines": []}
        expected = self._load_expected(candidate_id)
        report = readback_verification(
            candidate,
            snapshot,
            (expected or {}).get("tracks"),
            tempo_changed=bool((expected or {}).get("change_tempo")),
            arranged=self._arranged(candidate_id),
        )
        report["candidate_id"] = candidate_id
        report["checked_at"] = time.time()
        self._verifications[candidate_id] = report
        return report

    def _remember_expected(
        self, candidate_id: str, tracks: dict[str, dict[str, int]], change_tempo: bool
    ) -> None:
        record = {"tracks": tracks, "change_tempo": change_tempo}
        self._expected[candidate_id] = record
        path = self._candidate_path(candidate_id, ".expected.json")
        if path is not None:
            _write_json_atomic(path, record)

    def _load_expected(self, candidate_id: str) -> dict[str, Any] | None:
        if candidate_id in self._expected:
            return self._expected[candidate_id]
        path = self._candidate_path(candidate_id, ".expected.json")
        if path is None or not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return data if isinstance(data, dict) else None

    def _lineage(self, candidate: MidiCandidate) -> set[str]:
        ids = {candidate.candidate_id}
        parent = candidate.parent_candidate_id
        while parent and parent not in ids:
            ids.add(parent)
            found = self._lookup(parent)
            parent = found.parent_candidate_id if found is not None else ""
        return ids

    def fill_drum_samples(self, candidate_id: str, confirmed: bool = False) -> dict[str, Any]:
        """Load bundled Kick/Hats samples onto empty Drum Rack pads only.

        ``candidate_id`` may also be the 8-character id in the names of tracks
        an earlier apply left, for when a restart lost that candidate.
        """
        if not confirmed:
            return {"ok": False, "error": "適用内容を確認してから実行してください"}
        candidate: MidiCandidate | AppliedTracks | None = self._lookup(candidate_id)
        if candidate is None:
            try:
                candidate = AppliedTracks(candidate_id)
            except ValueError:
                return {"ok": False, "error": "指定した候補がありません"}
        if not self._apply_lock.acquire(blocking=False):
            return {"ok": False, "error": "別のLive適用が実行中です"}
        try:
            snapshot, failure = self._coverage_snapshot()
            if snapshot is None:
                return failure or {"ok": False, "error": "Live状態を取得できません"}
            plan = self._planner.create_drum_sample_plan(candidate, snapshot)
            if plan.status == "blocked":
                return {
                    "ok": False,
                    "error": plan.conflicts[0].detail if plan.conflicts else "計画できません",
                    "conflicts": [item.to_dict() for item in plan.conflicts],
                }
            if not plan.operations:
                return {
                    "ok": False,
                    "error": "載せられる空の Drum Rack パッドはありません",
                    "warnings": list(plan.warnings),
                }

            try:
                token = self._gate.approve(plan)
            except ApprovalError as exc:
                return {"ok": False, "error": exc.message}
            receipt = self._executor.execute(
                plan, approved=True, approval_token=token
            )
            coverage = self._coverage_after(candidate, receipt)
            return {
                "ok": receipt.status == "verified",
                "candidate_id": candidate.candidate_id,
                "receipt": receipt.to_dict(),
                "sound_coverage": coverage,
                "partial": receipt.status == "partially_applied",
                "musical_quality_claimed": False,
            }
        finally:
            self._apply_lock.release()

    def inspect_coverage(self, candidate_id: str) -> dict[str, Any]:
        """Re-read instruments and leftover [KIHACHI] tracks. Does not apply."""
        candidate = self._lookup(candidate_id)
        if candidate is None:
            return {"ok": False, "error": "指定した候補がありません"}
        coverage = self._coverage_after(candidate, receipt=self._last_receipt())
        return {
            "ok": not coverage.get("issues")
            or all(
                issue.get("kind") != "readback_unavailable"
                for issue in coverage.get("issues") or []
            ),
            "candidate_id": candidate.candidate_id,
            "sound_coverage": coverage,
        }

    def default_seed(self, brief: str) -> int:
        """Return the seed a first generate would use."""
        return seed_from_brief(brief)

    def _store(self, candidate: MidiCandidate) -> None:
        self._candidates[candidate.candidate_id] = candidate
        self._selected_id = candidate.candidate_id
        path = self._candidate_path(candidate.candidate_id)
        if path is not None:
            _write_json_atomic(path, candidate.to_dict())

    def _lookup(self, candidate_id: str) -> MidiCandidate | None:
        """Return a candidate from memory, or the one saved before a restart."""
        candidate = self._candidates.get(candidate_id)
        if candidate is not None:
            return candidate
        path = self._candidate_path(candidate_id)
        if path is None or not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            candidate = MidiCandidate.from_dict(data)
        except (OSError, TypeError, ValueError, KeyError):
            return None
        # The saved fingerprint is what the user previewed. Different notes on
        # disk would break "preview notes equal applied notes", so refuse them.
        if (
            candidate.candidate_id != candidate_id
            or data.get("note_fingerprint") != candidate.note_fingerprint
        ):
            return None
        self._candidates[candidate_id] = candidate
        return candidate

    def _candidate_path(self, candidate_id: str, suffix: str = ".json") -> Path | None:
        if self._candidate_dir is None or not _CANDIDATE_ID_RE.fullmatch(candidate_id):
            return None
        return self._candidate_dir / f"{candidate_id}{suffix}"

    def _mark_applied(self, candidate_id: str) -> None:
        """Remember the attempt on disk so a restart cannot apply it twice."""
        self._mark(candidate_id, ".applied")

    def _mark(self, candidate_id: str, suffix: str) -> None:
        path = self._candidate_path(candidate_id, suffix)
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"{time.time():.0f}\n", encoding="utf-8")

    def _applied_on_disk(self, candidate_id: str) -> bool:
        path = self._candidate_path(candidate_id, ".applied")
        return path is not None and path.is_file()

    def _live_health(self) -> dict[str, Any]:
        if self._transport is None:
            return {
                "connected": False,
                "state": "not_configured",
                "message": "Liveブリッジが起動していません",
            }
        try:
            report = self._inspector.health()
        except Exception as exc:  # noqa: BLE001 - health must never raise to the UI
            return {
                "connected": False,
                "state": "error",
                "message": str(exc),
            }
        if not report.get("connected"):
            report["state"] = "disconnected"
            report["message"] = (
                report.get("error")
                or "Ableton Live の KIHACHI デバイスに接続できません"
            )
            return report
        if not report.get("protocol_supported", True):
            report["state"] = "protocol"
            report["message"] = report.get("error") or "デバイス版が一致しません"
            return report
        report["state"] = "ready"
        report["message"] = (
            f"Live {report.get('live_version') or '?'} / "
            f"{report.get('device_version') or '?'}"
        )
        return report

    def _snapshot(self):
        try:
            return self._inspector.snapshot(), None
        except LiveTransportError as exc:
            return None, {
                "ok": False,
                "error": f"{exc.code}: {exc.message}",
                "error_code": exc.code,
            }
        except LiveVersionUnsupportedError as exc:
            return None, {"ok": False, "error": str(exc)}

    def _last_receipt(self) -> Any:
        receipt = (self._last_apply or {}).get("receipt") or {}

        class _Receipt:
            status = str(receipt.get("status") or "")

        return _Receipt()

    def _coverage_after(
        self, candidate: MidiCandidate | AppliedTracks, receipt: Any
    ) -> dict[str, Any]:
        snapshot, failure = self._coverage_snapshot()
        if snapshot is None:
            return {
                "verified": False,
                "issues": [
                    {
                        "kind": "readback_unavailable",
                        "detail": (failure or {}).get("error", "適用後の状態を再取得できません"),
                        "verified": False,
                    }
                ],
                "automatic": [],
                "manual": ["Liveの状態を再確認してください"],
                "has_missing_sounds": False,
                "leftover_tracks": [],
                "current_tracks": [],
            }
        short = candidate.candidate_id[:8]
        track_names = {
            part: managed_track_name(f"KIHACHI {part} {short}")
            for part in candidate.parts
        }
        devices: dict[str, list[str]] = {}
        summaries: dict[str, dict[str, Any]] = {}
        for part, name in track_names.items():
            track = snapshot.track_by_name(name)
            devices[part] = list(track.device_names) if track is not None else []
            if track is None:
                continue
            try:
                summaries[part] = self._coverage_inspector.drum_rack_summary(track.index)
            except LiveTransportError:
                continue
        coverage = coverage_from_snapshot(candidate, track_names, devices, summaries)
        coverage["set_name"] = snapshot.set_name
        coverage["receipt_status"] = getattr(receipt, "status", "")
        coverage["current_tracks"] = [
            name for name in track_names.values() if snapshot.track_by_name(name)
        ]
        coverage["leftover_tracks"] = [
            track.name
            for track in snapshot.tracks
            if track.is_managed and short not in track.name
        ]
        return coverage

    def _coverage_snapshot(self):
        try:
            return self._coverage_inspector.snapshot(), None
        except LiveTransportError as exc:
            return None, {
                "ok": False,
                "error": f"{exc.code}: {exc.message}",
                "error_code": exc.code,
            }
        except LiveVersionUnsupportedError as exc:
            return None, {"ok": False, "error": str(exc)}


class _PlanningInspector(LiveStateInspector):
    """Inspect Live without walking every Arrangement clip or counting notes."""

    def snapshot(self) -> Any:
        return super().snapshot(include_arrangement=False, count_session_notes=False)


class _ArrangementInspector(LiveStateInspector):
    """Inspect Session and Arrangement clips, without counting notes."""

    def snapshot(self) -> Any:
        return super().snapshot(include_arrangement=True, count_session_notes=False)


class _VerifyInspector(LiveStateInspector):
    """Inspect Session and Arrangement clips with note counts, for read-back."""

    def snapshot(self) -> Any:
        return super().snapshot(include_arrangement=True, count_session_notes=True)


class _CoverageInspector(LiveStateInspector):
    """Inspect tracks and devices only. Session clips are not required."""

    def snapshot(self) -> Any:
        return super().snapshot(
            include_arrangement=False,
            count_session_notes=False,
            include_session_clips=False,
        )


def _latest_saved_id(directory: Path) -> str:
    """Return the most recently saved candidate id, or "" when there is none."""
    try:
        saved = [
            path
            for path in directory.glob("*.json")
            if path.is_file() and _CANDIDATE_ID_RE.fullmatch(path.stem)
        ]
    except OSError:
        return ""
    if not saved:
        return ""
    return max(saved, key=lambda path: path.stat().st_mtime).stem


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8"
    )
    os.replace(temporary, path)


def _read_json_list(path: Path) -> list[dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []


def _public_revision(record: dict[str, Any]) -> dict[str, Any]:
    review = record.get("review_after") or {}
    return {
        **{key: value for key, value in record.items() if key != "review_after"},
        "issues_after": review.get("issues") or [],
    }


def _unique_request_ids():
    """Return request ids that remain unique across studio restarts."""

    def next_id() -> str:
        return f"studio-{uuid.uuid4().hex}"

    return next_id


def _idle_job() -> dict[str, Any]:
    return {
        "state": JOB_IDLE,
        "started_at": 0.0,
        "elapsed_seconds": 0.0,
        "error": "",
        "candidate_id": "",
    }


def _notes_from_candidate(candidate: MidiCandidate) -> list[list[dict[str, Any]]]:
    return [[note.to_dict() for note in clip.notes] for clip in candidate.clips]


def _notes_from_plan(plan: Any) -> list[list[dict[str, Any]]]:
    from kihachi_mcp.models.live_contract import OP_REPLACE_CLIP_NOTES

    return [
        list(operation.arguments.get("notes") or [])
        for operation in plan.operations
        if operation.op == OP_REPLACE_CLIP_NOTES
    ]


def _apply_summary(
    candidate: MidiCandidate, plan: Any, snapshot: Any, change_tempo: bool
) -> dict[str, Any]:
    from kihachi_mcp.models.live_contract import (
        OP_CREATE_MIDI_TRACK,
        OP_REPLACE_CLIP_NOTES,
    )

    create_tracks = [
        operation.arguments.get("name")
        for operation in plan.operations
        if operation.op == OP_CREATE_MIDI_TRACK
    ]
    replace_ops = [
        operation
        for operation in plan.operations
        if operation.op == OP_REPLACE_CLIP_NOTES
    ]
    return {
        "set_name": snapshot.set_name,
        "live_version": snapshot.live_version,
        "device_version": "",
        "is_recording": snapshot.is_recording,
        "is_playing": snapshot.is_playing,
        "current_tempo": snapshot.tempo,
        "candidate_tempo": candidate.brief.tempo.value,
        "change_tempo": change_tempo,
        "new_tracks": create_tracks,
        "clip_count": len(replace_ops),
        "note_count": candidate.note_count(),
        "note_counts": {
            part: candidate.note_count(part)
            for part in candidate.parts
        },
        "used_pitches": {
            part: list(candidate.used_pitches(part))
            for part in candidate.parts
        },
        "replaces_existing_user_clips": False,
        "operation_count": len(plan.operations),
        "leftover_tracks": [
            track.name
            for track in snapshot.tracks
            if track.is_managed and candidate.candidate_id[:8] not in track.name
        ],
    }
