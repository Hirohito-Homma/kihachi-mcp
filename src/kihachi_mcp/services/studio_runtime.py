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

from kihachi_mcp.models.live_contract import managed_track_name
from kihachi_mcp.models.midi_candidate import MidiCandidate
from kihachi_mcp.services.candidate_live_planner import (
    AppliedTracks,
    CandidateLivePlanner,
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
from kihachi_mcp.services.sound_coverage import coverage_from_snapshot
from kihachi_mcp.services.studio_interpreter import (
    InferenceCancelled,
    InterpretationError,
    OllamaClient,
    interpret_brief,
)

JOB_IDLE = "idle"
JOB_RUNNING = "running"
JOB_DONE = "done"
JOB_ERROR = "error"
JOB_CANCELLED = "cancelled"

#: Candidate ids become file names, so only these characters are accepted.
_CANDIDATE_ID_RE = re.compile(r"[0-9A-Za-z_-]{1,64}")


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
        self._export_dir = Path(
            export_dir or Path.home() / "Music" / "KIHACHI" / "exports"
        )
        self._last_apply: dict[str, Any] | None = None
        self._applied_ids: set[str] = set()
        # None keeps candidates in memory only, as tests and the MCP server do.
        self._candidate_dir = Path(candidate_dir) if candidate_dir else None
        if self._candidate_dir is not None:
            self._selected_id = _latest_saved_id(self._candidate_dir)

    def close(self) -> None:
        """Release the Live bridge if this runtime created it."""
        if self._bridge_session is not None and isinstance(
            self._transport, LocalhostBridgeTransport
        ):
            self._transport.close()

    def health(self) -> dict[str, Any]:
        """Return Ollama and Live connection states without mutating anything."""
        ollama = probe_ollama()
        live = self._live_health()
        return {
            "ollama": ollama,
            "live": live,
            "inference": self.job_status(),
            "selected_candidate_id": self._selected_id,
            "candidate_ids": list(self._candidates),
            "paid_api": False,
            "model_download": False,
        }

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
        client = self._ollama_factory()
        started = time.monotonic()
        with self._lock:
            self._client = client
            self._job = {
                "state": JOB_RUNNING,
                "started_at": time.time(),
                "elapsed_seconds": 0.0,
                "error": "",
                "candidate_id": "",
            }
        try:
            ollama = probe_ollama()
            if not ollama["ok"]:
                raise InterpretationError(str(ollama["message"]))
            production = interpret_brief(brief, client=client)
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
        self, candidate_id: str, change_tempo: bool = False
    ) -> dict[str, Any]:
        """Show what would be applied without touching Live."""
        candidate = self._lookup(candidate_id)
        if candidate is None:
            return {"ok": False, "error": "指定した候補がありません"}
        snapshot, failure = self._snapshot()
        if snapshot is None:
            return failure or {"ok": False, "error": "Live状態を取得できません"}
        plan = self._planner.create_plan(candidate, snapshot, change_tempo=change_tempo)
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
            "summary": _apply_summary(candidate, plan, snapshot, change_tempo),
            "conflicts": [item.to_dict() for item in plan.conflicts],
            "warnings": list(plan.warnings),
        }

    def apply(
        self, candidate_id: str, confirmed: bool = False, change_tempo: bool = False
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
            preview = self.apply_preview(candidate_id, change_tempo=change_tempo)
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
            receipt = self._executor.execute(
                plan, approved=True, approval_token=token
            )
            coverage = self._coverage_after(candidate, receipt)
            result = {
                "ok": receipt.status == "verified",
                "candidate_id": candidate.candidate_id,
                "note_fingerprint": candidate.note_fingerprint,
                "receipt": receipt.to_dict(),
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

    def last_apply(self) -> dict[str, Any] | None:
        """Return the most recent apply result."""
        return self._last_apply

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
            from kihachi_mcp.models.live_mutation import LiveMutationPlan

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
        path = self._candidate_path(candidate_id, ".applied")
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
            for part in ("Kick", "Hats", "Bass", "Stab")
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
        saved = [path for path in directory.glob("*.json") if path.is_file()]
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
            for part in ("Kick", "Hats", "Bass", "Stab")
        },
        "used_pitches": {
            part: list(candidate.used_pitches(part))
            for part in ("Kick", "Hats", "Bass", "Stab")
        },
        "replaces_existing_user_clips": False,
        "operation_count": len(plan.operations),
        "leftover_tracks": [
            track.name
            for track in snapshot.tracks
            if track.is_managed and candidate.candidate_id[:8] not in track.name
        ],
    }
