"""HTTP adapter for the local reference library, isolated from Live operations."""

import hashlib
import json
import random
import re
import queue
import sqlite3
import subprocess
import threading
import time
import uuid
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import parse_qs, urlparse

from kihachi_mcp.knowledge.genre_database import canonical_genre, load_database
from kihachi_mcp.services.ai_provider import GeminiAIProvider, OllamaProvider
from kihachi_mcp.services.groove_rule_composer import (
    generate_variants_from_card,
    promote_variant_to_candidate,
)
from kihachi_mcp.services.live_paths import candidate_store_dir
from kihachi_mcp.services.music_training_service import (
    TRAINING_THEMES,
    TrainingJournalEntry,
    TrainingJournalStore,
    generate_training_variants,
    promote_training_variant_to_candidate,
    reference_generation_profile,
    resolve_reference_key,
)
from kihachi_mcp.services.reference_analysis import EXTENSIONS, ReferenceError
from kihachi_mcp.services.reference_analysis_card import (
    AnalysisCard,
    AnalysisCardStore,
)
from kihachi_mcp.services.reference_groove_analysis import (
    analyze_reference_groove,
)
from kihachi_mcp.services.reference_groove_gemini import (
    enhance_groove_with_gemini,
)
from kihachi_mcp.services.reference_guidance import propose_tempo
from kihachi_mcp.services.reference_library import (
    MAX_BYTES,
    ReferenceLibrary,
    validate_metadata,
)
from kihachi_mcp.services.reference_sources import _autonomous_license_allowed


_ANALYSIS_JOBS: dict[str, dict] = {}
_ANALYSIS_JOBS_LOCK = threading.Lock()
_MAX_ANALYSIS_JOBS = 2
_MAX_RETAINED_ANALYSIS_JOBS = 50
_MAX_LEGACY_CARD_RECOVERY_SCAN = 50


def _analysis_request_fingerprint(body: dict) -> str:
    request_payload = {
        key: value for key, value in body.items()
        if key not in {"client_request_id", "recover_from_missing_job"}
    }
    return hashlib.sha256(
        json.dumps(request_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _saved_analysis_request_card(library, body: dict, request_id: str, request_fingerprint: str) -> dict | None:
    store = _get_card_store(library)
    explicit_card_id = str(body.get("card_id") or "")
    card_id = explicit_card_id or f"card_request_{request_id}"
    card = store.get(card_id)
    if card is None:
        if explicit_card_id or not body.get("recover_from_missing_job"):
            return None
        # Older cards used random IDs. Search them only for an explicit recovery
        # attempt so ordinary new requests stay on the direct-lookup path.
        legacy_paths = []
        for card_path in store.storage_dir.glob("*.json"):
            try:
                legacy_paths.append((card_path.stat().st_mtime, card_path))
            except OSError:
                continue
        for _modified_at, card_path in sorted(legacy_paths, key=lambda item: item[0], reverse=True)[:_MAX_LEGACY_CARD_RECOVERY_SCAN]:
            try:
                saved = json.loads(card_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            stats = saved.get("execution_stats") or {}
            if stats.get("analysis_request_id") != request_id:
                continue
            if stats.get("analysis_request_fingerprint") != request_fingerprint:
                raise ReferenceError("同じ解析要求IDが異なる条件で再利用されました。画面を再読み込みして再度お試しください")
            legacy_card = store.get(str(saved.get("card_id") or ""))
            if legacy_card:
                return legacy_card.to_dict()
        return None
    stats = card.execution_stats or {}
    if stats.get("analysis_request_id") != request_id:
        if not explicit_card_id:
            raise ReferenceError("解析要求IDから生成したカードIDが既存カードと衝突しました。既存カード保護のため解析を中止しました")
        return None
    if stats.get("analysis_request_fingerprint") != request_fingerprint:
        raise ReferenceError("同じ解析要求IDが異なる条件で再利用されました。画面を再読み込みして再度お試しください")
    return card.to_dict()


class AutonomousLearningDaemon:
    """Coordinate dedicated discovery and analysis workers inside Studio."""

    SEARCH_INTERVAL_SECONDS = 60
    RETRY_INTERVAL_SECONDS = 15
    MAX_AUTOMATIC_IMPORT_ATTEMPTS = 3
    LOCAL_SAMPLE_WORKERS = 2

    def __init__(self, library, sources, sample_catalog=None, runtime=None):
        self.library = library
        self.sources = sources
        self.sample_catalog = sample_catalog
        self.runtime = runtime
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread = None
        self._agent_threads = []
        self._analysis_lock = threading.BoundedSemaphore(2)
        self._started_at = 0.0
        self._sample_completed_at_start = 0
        self._training_theme_index = 0
        # A bad or temporarily unavailable registered asset must not monopolize
        # the head of the unfinished queue. Cooldowns are per reference and
        # retries remain visible in daemon status.
        self._registered_retry_state: dict[str, dict] = {}
        self._registered_failure_attempts = 0
        self._registered_recovered = 0
        # Coalesce backlog: the latest learned card is the useful one to compose,
        # and one local judge worker must not accumulate thousands of stale jobs.
        self._composition_queue = queue.Queue(maxsize=1)
        self._evaluation_queue = queue.Queue(maxsize=1)
        self._enabled = False
        self._agents = {
            "coordinator": {"name": "統括エージェント", "role": "キュー管理・重複防止", "status": "待機", "task": "", "completed": 0, "failed": 0},
            "provider_search": {"name": "音源探索エージェント", "role": "Freesound / Jamendo検索・取得条件確認", "status": "待機", "task": "", "completed": 0, "failed": 0},
            "registered_audio": {"name": "登録音源エージェント", "role": "登録済み音源のDSP解析", "status": "待機", "task": "", "completed": 0, "failed": 0},
            "local_samples": {"name": "ローカルサンプルエージェント", "role": "索引済みサンプルのDSP解析（最大2件並行）", "status": "待機", "task": "", "completed": 0, "failed": 0, "active_workers": 0},
            "composition": {"name": "作曲案エージェント", "role": "学習プロファイルを使ったA/B/C生成", "status": "待機", "task": "", "completed": 0, "failed": 0},
            "evaluation": {"name": "AI作曲判定エージェント", "role": "ローカルAIでA/B/Cを採点・自動選定", "status": "待機", "task": "", "completed": 0, "failed": 0},
        }
        self._state = {
            "phase": "stopped",
            "message": "自律学習は停止中です",
            "last_cycle_at": None,
            "last_result": None,
            "next_search_at": None,
            "error": None,
            "last_composition": None,
            "last_judged_composition": None,
            "last_composition_journal": None,
        }

    def status(self) -> dict:
        with self._lock:
            elapsed = max(1.0, time.monotonic() - self._started_at) if self._started_at else 1.0
            completed_since_start = max(0, self._agents["local_samples"]["completed"] - self._sample_completed_at_start)
            sample_rate = round(completed_since_start * 3600 / elapsed, 1) if elapsed >= 60 else None
            return {"ok": True, "enabled": self._enabled,
                    "running": bool(self._thread and self._thread.is_alive()),
                    "local_sample_workers": self.LOCAL_SAMPLE_WORKERS,
                    "local_samples_per_hour_estimate": sample_rate,
                    "agents": {key: dict(value) for key, value in self._agents.items()},
                    **self._state}

    def start(self) -> dict:
        with self._lock:
            if self._thread and self._thread.is_alive():
                self._enabled = True
                return {"ok": True, "enabled": True, "message": "バックグラウンド自律学習は実行中です"}
            self._stop_event.clear()
            self._enabled = True
            self._started_at = time.monotonic()
            self._sample_completed_at_start = self._agents["local_samples"]["completed"]
            self._state.update(phase="starting", message="バックグラウンド自律学習を開始します", error=None)
            self._thread = threading.Thread(
                target=self._run,
                name="kihachi-reference-learning",
                daemon=True,
            )
            self._thread.start()
        return {"ok": True, "enabled": True, "message": "バックグラウンド自律学習を開始しました"}

    def stop(self) -> dict:
        with self._lock:
            self._enabled = False
            self._stop_event.set()
            if self._thread and self._thread.is_alive():
                self._state.update(phase="stopping", message="現在の解析処理が終わり次第停止します")
            else:
                self._state.update(phase="stopped", message="自律学習は停止中です")
        return {"ok": True, "enabled": False, "message": "停止を受け付けました。実行中の処理後に停止します"}

    def close(self) -> None:
        self.stop()

    def _publish(self, **values) -> None:
        with self._lock:
            self._state.update(values)

    def _agent(self, key: str, **values) -> None:
        with self._lock:
            self._agents[key].update(values)
            self._agents[key]["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")

    def _increment_agent(self, key: str, field: str) -> int:
        with self._lock:
            value = int(self._agents[key].get(field, 0)) + 1
            self._agents[key][field] = value
            self._agents[key]["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
            return value

    def _change_sample_active(self, delta: int) -> int:
        with self._lock:
            active = max(0, int(self._agents["local_samples"].get("active_workers", 0)) + delta)
            self._agents["local_samples"]["active_workers"] = active
            self._agents["local_samples"]["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
            return active

    def _enqueue_composition(self, card_id: str, title: str) -> None:
        try:
            previous = self._composition_queue.get_nowait()
        except queue.Empty:
            previous = None
        if previous is not None:
            self._composition_queue.task_done()
        try:
            self._composition_queue.put_nowait({"card_id": card_id, "title": title})
            self._agent("composition", status="キュー待ち", task=f"生成待ち: {title}",
                        pending=self._composition_queue.qsize())
        except queue.Full:
            self._agent("composition", status="再試行待ち", task="生成キューが満杯です",
                        failed=self._agents["composition"]["failed"] + 1)

    @staticmethod
    def _composition_summary(package: dict) -> dict:
        return {
            key: value for key, value in package.items() if key != "variants"
        } | {
            "variants": [
                {key: value for key, value in variant.items() if key != "notes"}
                | {"note_counts": {part: len(notes) for part, notes in (variant.get("notes") or {}).items()}}
                for variant in package.get("variants", [])
            ]
        }

    def _composition_worker(self) -> None:
        journal = TrainingJournalStore()
        while not self._stop_event.is_set():
            try:
                job = self._composition_queue.get(timeout=1)
            except queue.Empty:
                self._agent("composition", status="待機", task="解析完了カードを待機")
                continue
            composition_id = uuid.uuid4().hex
            try:
                with self._lock:
                    theme = TRAINING_THEMES[self._training_theme_index % len(TRAINING_THEMES)]
                    self._training_theme_index += 1
                self._agent("composition", status="処理中", task=f"{theme.title}のA/B/C生成: {job['title']}", current_item=job["title"], current_theme=theme.theme_id)
                card = _get_card_store(self.library).get(job["card_id"])
                if card is None:
                    raise ReferenceError("作曲案の生成に必要な分析カードまたはテーマがありません")
                profile = journal.learning_profile(theme.theme_id, card.card_id, self.library.online_references())
                key = resolve_reference_key(card)
                result = generate_training_variants(theme=theme, card=card, key=key, bars=8, learning_profile=profile, reference_conditioned=True)
                package = {
                    "composition_id": composition_id,
                    "card_id": card.card_id, "source_title": job["title"],
                    "theme_id": theme.theme_id, "theme_title": theme.title,
                    "theme_category": theme.category,
                    "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                    "learning": result.get("learning", {}),
                    "learning_application": {
                        "profile_scope": profile.get("scope", {}),
                        "theme_choice_counts": profile.get("choice_counts", {}),
                        "theme_observations": profile.get("observations", 0),
                        "required_observations": result.get("learning", {}).get("required_observations", 5),
                        "preferred_variant": result.get("learning", {}).get("preferred_variant"),
                        "personal_preference_applied": bool(result.get("learning", {}).get("active")),
                        "sensory_feedback_applied": bool(result.get("learning", {}).get("sensory_adjustment")),
                        "sensory_adjustment": result.get("learning", {}).get("sensory_adjustment"),
                        "application_scope": "kick_bass_space" if result.get("learning", {}).get("active") else None,
                    },
                    "reference_features": result.get("source_reference", {}).get("features", {}),
                    "variants": result.get("variants", []),
                    "evaluation": None,
                    "evaluation_boundary": "ローカルAIがMIDIの構造と学習済み特徴から順位を付けます。実音の聴感評価ではありません。",
                }
                journal_entry = journal.record_composition_application(package)
                self._publish(last_composition_journal={
                    "saved": True,
                    "entry_id": journal_entry.entry_id,
                    "composition_id": package["composition_id"],
                    "created_at": journal_entry.created_at,
                })
                self._publish(last_composition=self._composition_summary(package))
                try:
                    old_job = self._evaluation_queue.get_nowait()
                except queue.Empty:
                    old_job = None
                if old_job is not None:
                    self._evaluation_queue.task_done()
                self._evaluation_queue.put_nowait({"package": package})
                self._agent("composition", status="完了", task=f"{theme.title}のA/B/Cを生成: {job['title']}",
                            completed=self._agents["composition"]["completed"] + 1,
                            last_item=job["title"], last_theme=theme.theme_id)
            except Exception as exc:
                self._publish(last_composition_journal={"saved": False, "composition_id": composition_id, "error": str(exc)})
                self._agent("composition", status="再試行待ち", task=str(exc),
                            failed=self._agents["composition"]["failed"] + 1)
            finally:
                self._composition_queue.task_done()

    def _evaluation_worker(self) -> None:
        while not self._stop_event.is_set():
            try:
                job = self._evaluation_queue.get(timeout=1)
            except queue.Empty:
                self._agent("evaluation", status="待機", task="生成案を待機")
                continue
            try:
                package = job["package"]
                self._agent("evaluation", status="処理中", task=f"ローカルAIでA/B/Cを採点: {package['source_title']}")
                variants = package.get("variants", [])
                ids = [item.get("variant_id") for item in variants]
                issues = []
                if len(variants) != 3 or set(ids) != {"A", "B", "C"} or len(set(ids)) != 3:
                    issues.append("A/B/Cの3案がそろっていません")
                signatures = set()
                rows = []
                for variant in variants:
                    notes = variant.get("notes") or {}
                    counts = {part: len(notes.get(part) or []) for part in ("Kick", "Bass")}
                    signatures.add(tuple(tuple((note.get("start_beats"), note.get("pitch")) for note in notes.get(part, [])) for part in ("Kick", "Bass")))
                    if not any(counts.values()):
                        issues.append(f"案{variant.get('variant_id')}にMIDIノートがありません")
                    rows.append({"variant_id": variant.get("variant_id"), "name": variant.get("name"), "note_counts": counts})
                if len(signatures) < min(2, len(variants)) and variants:
                    issues.append("A/B/C間でノート配置の違いを確認できません")
                if package.get("theme_category") == "sound_design" and len(signatures) <= 1:
                    evaluation = {
                        "status": "human_listening_required",
                        "judge": "none",
                        "best_variant_id": None,
                        "summary": "音色レシピが異なる候補ですが、MIDIは同一です。実音を聴かずに最良案は選びません。",
                        "checks": ["A/B/Cの有無", "同一MIDIの確認", "音色レシピの有無"],
                        "distinct_note_patterns": len(signatures),
                        "variants": rows,
                        "issues": ["音色差はAbleton Liveでの試聴が必要です"],
                        "human_listening_required": True,
                        "note": "自律評価はMIDI・設定レシピまでです。実音の聴感評価と採用は人の試聴後に記録してください。",
                    }
                    package["evaluation"] = evaluation
                    self._publish(last_judged_composition=self._composition_summary(package))
                    self._agent(
                        "evaluation", status="実音試聴待ち",
                        task=f"{package.get('theme_title', '音色テーマ')}: Live試聴後の評価を待機",
                        completed=self._agents["evaluation"]["completed"] + 1,
                        evaluation_status=evaluation["status"],
                        selected_variant=None,
                        last_item=package["source_title"],
                    )
                    continue
                profile = TrainingJournalStore().learning_profile(
                    package.get("theme_id") or "kick_bass_space",
                    package.get("card_id"), self.library.online_references(),
                )
                schema = {
                    "type": "object",
                    "properties": {
                        "best_variant_id": {"type": "string", "enum": ["A", "B", "C"]},
                        "summary": {"type": "string"},
                        "variants": {"type": "array", "items": {"type": "object", "properties": {
                            "variant_id": {"type": "string", "enum": ["A", "B", "C"]},
                            "score": {"type": "integer", "minimum": 0, "maximum": 100},
                            "reference_fit": {"type": "integer", "minimum": 0, "maximum": 100},
                            "groove": {"type": "integer", "minimum": 0, "maximum": 100},
                            "coherence": {"type": "integer", "minimum": 0, "maximum": 100},
                            "reasoning": {"type": "string"},
                        }, "required": ["variant_id", "score", "reference_fit", "groove", "coherence", "reasoning"], "additionalProperties": False}},
                    },
                    "required": ["best_variant_id", "summary", "variants"],
                    "additionalProperties": False,
                }
                score = None
                model_name = None
                fallback_reason = ""
                if not issues and len(variants) == 3:
                    try:
                        provider = self._local_judge_provider()
                        model_name = provider.model
                        health = provider.health()
                        if not health.get("ok"):
                            raise ReferenceError("Ollamaのモデルが利用できません")
                        score = provider.generate_structured(self._judge_prompt(package, profile), schema)
                        score_rows = score.get("variants") if isinstance(score.get("variants"), list) else []
                        score_by_id = {row.get("variant_id"): row for row in score_rows if isinstance(row, dict)}
                        if set(score_by_id) != {"A", "B", "C"} or score.get("best_variant_id") not in score_by_id:
                            raise ReferenceError("AI応答にA/B/Cの完全な採点がありません")
                        for row in score_by_id.values():
                            if any(not isinstance(row.get(key), int) or not 0 <= row[key] <= 100 for key in ("score", "reference_fit", "groove", "coherence")):
                                raise ReferenceError("AI応答の採点値が範囲外です")
                        for row in rows:
                            row.update(score_by_id[row["variant_id"]])
                            row["selected"] = row["variant_id"] == score["best_variant_id"]
                    except Exception as exc:
                        score = None
                        fallback_reason = str(exc) or type(exc).__name__
                if score is not None:
                    evaluation = {
                        "status": "pass", "judge": "ollama_local", "model": model_name,
                        "best_variant_id": score["best_variant_id"], "summary": score.get("summary") or "ローカルAIがMIDI特徴を比較しました。",
                        "checks": ["A/B/C案の有無", "Kick/Bassノート", "案ごとの配置差", "学習済み特徴に基づくローカルAI採点"],
                        "distinct_note_patterns": len(signatures), "variants": rows, "issues": [],
                        "human_listening_required": True,
                        "note": "AIはMIDIノート配置・リズムと学習済み特徴から順位を推定しました。実音を聴いた評価ではなく、Liveへは自動反映しません。",
                    }
                else:
                    evaluation = self._feature_fallback_evaluation(
                        package, rows, issues,
                        fallback_reason or ("・".join(issues) if issues else "ローカルAIを利用できません"),
                    )
                package["evaluation"] = evaluation
                judged_summary = self._composition_summary(package)
                self._publish(last_judged_composition=judged_summary)
                is_fallback = evaluation["judge"] != "ollama_local"
                best = evaluation.get("best_variant_id")
                self._agent("evaluation", status="特徴量で代替判定" if is_fallback else "完了",
                            task=(f"AI利用不可のため特徴量採点: 案{best} ({package['source_title']})" if is_fallback
                                  else f"AI選定: 案{best} ({package['source_title']})"),
                            completed=self._agents["evaluation"]["completed"] + 1,
                            fallback_count=self._agents["evaluation"].get("fallback_count", 0) + int(is_fallback),
                            last_fallback_reason=fallback_reason if is_fallback else "",
                            last_item=package["source_title"], evaluation_status=evaluation["status"],
                            selected_variant=best)
            except Exception as exc:
                self._agent("evaluation", status="再試行待ち", task=str(exc),
                            failed=self._agents["evaluation"]["failed"] + 1)
            finally:
                self._evaluation_queue.task_done()

    @staticmethod
    def _feature_fallback_evaluation(package: dict, rows: list[dict], issues: list[str], reason: str) -> dict:
        """Always finish judging with transparent MIDI feature scoring if Ollama fails."""
        variants = {item.get("variant_id"): item for item in package.get("variants", []) if isinstance(item, dict)}
        targets = package.get("reference_features") or {}
        bars = max(1, int(package.get("bars") or 8))

        def fit(actual, target) -> float | None:
            try:
                actual, target = float(actual), float(target)
            except (TypeError, ValueError):
                return None
            return max(0.0, 100.0 * (1.0 - abs(actual - target) / max(abs(target), 1.0)))

        def valid_midi(note: dict) -> bool:
            try:
                return 0 <= int(note.get("pitch", -1)) <= 127 and 1 <= int(note.get("velocity", 0)) <= 127
            except (TypeError, ValueError):
                return False

        for row in rows:
            variant = variants.get(row.get("variant_id"), {})
            notes = variant.get("notes") or {}
            kick = notes.get("Kick") or []
            bass = notes.get("Bass") or []
            params = variant.get("parameters") or {}
            kick_density = len(kick) / bars
            bass_density = len(bass) / bars
            target_kick = targets.get("kick_density_per_bar")
            target_bass = targets.get("bass_density_per_bar")
            target_sep = targets.get("bass_kick_separation_ratio")
            target_rest = targets.get("bass_rest_ratio")
            fits = [v for v in (
                fit(kick_density, target_kick),
                fit(bass_density, target_bass),
                fit(params.get("syncopation"), target_sep),
                fit(params.get("rest_ratio"), target_rest),
            ) if v is not None]
            reference_fit = round(sum(fits) / len(fits)) if fits else 60
            kick_times = [float(n.get("start_beats") or 0) for n in kick if isinstance(n, dict)]
            bass_times = [float(n.get("start_beats") or 0) for n in bass if isinstance(n, dict)]
            collisions = sum(any(abs(t - k) <= 0.125 for k in kick_times) for t in bass_times)
            collision_free = 100.0 * (1.0 - collisions / max(1, len(bass_times)))
            groove = round((collision_free * 0.7) + (reference_fit * 0.3))
            all_notes = [n for n in [*kick, *bass] if isinstance(n, dict)]
            coherence = 100 if kick and bass and all_notes and all(valid_midi(n) for n in all_notes) else 35
            row.update({
                "reference_fit": reference_fit,
                "groove": groove,
                "coherence": coherence,
                "score": round(reference_fit * 0.65 + groove * 0.20 + coherence * 0.15),
                "reasoning": f"MIDI特徴量による代替採点。キック密度 {kick_density:.2f}/小節、ベース密度 {bass_density:.2f}/小節、低域重複 {collisions}/{len(bass_times)}。",
            })
        best_row = max(rows, key=lambda item: item.get("score", 0), default=None)
        best_id = best_row.get("variant_id") if best_row else None
        for row in rows:
            row["selected"] = row.get("variant_id") == best_id
        status = "fallback" if len(rows) == 3 else "incomplete"
        signatures = {
            tuple(
                tuple((note.get("start_beats"), note.get("pitch")) for note in (variant.get("notes", {}).get(part) or []))
                for part in ("Kick", "Bass")
            )
            for variant in variants.values()
        }
        return {
            "status": status,
            "judge": "deterministic_midi_feature_fallback",
            "model": None,
            "best_variant_id": best_id,
            "summary": ("ローカルAIを利用できなかったため、参照特徴との密度・休符・低域重複を数値比較して選びました。" if best_id
                        else "有効な作曲案がなく、順位を決められませんでした。次の生成サイクルで再作成します。"),
            "fallback_reason": reason,
            "checks": ["MIDIノート数", "参照キック/ベース密度", "休符・シンコペーション", "キックとベースの重なり"],
            "distinct_note_patterns": len(signatures),
            "variants": rows,
            "issues": issues,
            "human_listening_required": True,
            "note": "これは生成MIDIの特徴量順位で、AIモデルの音楽的判断や実音の聴感評価ではありません。Liveには自動反映しません。",
        }

    def _local_judge_provider(self):
        settings = self.runtime.settings() if self.runtime is not None else {}
        # Always use the already-installed local model for autonomous judging;
        # never silently invoke a paid cloud provider or download a model.
        return OllamaProvider(
            url=str(settings.get("ollama_url") or "http://127.0.0.1:11434"),
            model=str(settings.get("ollama_model") or "gemma4:latest"),
            timeout=45.0,
        )

    @staticmethod
    def _judge_prompt(package: dict, profile: dict) -> str:
        compact = []
        for variant in package.get("variants", []):
            notes = variant.get("notes") or {}
            kick = notes.get("Kick") or []
            bass = notes.get("Bass") or []
            kick_times = [float(note.get("start_beats") or 0) for note in kick]
            bass_times = [float(note.get("start_beats") or 0) for note in bass]
            collision_count = sum(
                1 for start in bass_times
                if any(abs(start - kick_start) <= 0.125 for kick_start in kick_times)
            )
            offbeat = sum(1 for start in bass_times if abs((start % 1.0) - 0.5) <= 0.125 or abs((start % 1.0) - 0.75) <= 0.125)
            compact.append({
                "id": variant.get("variant_id"), "name": variant.get("name"),
                "concept": variant.get("concept"), "design_parameters": variant.get("parameters", {}),
                "midi_features": {
                    "kick_note_count": len(kick), "bass_note_count": len(bass),
                    "bass_onsets_overlapping_kick_within_0_125_beats": collision_count,
                    "bass_offbeat_ratio": round(offbeat / max(1, len(bass)), 3),
                    "bass_pitch_range": (max((int(note.get("pitch") or 0) for note in bass), default=0) - min((int(note.get("pitch") or 0) for note in bass), default=0)),
                    "mean_bass_duration_beats": round(sum(float(note.get("duration_beats") or 0) for note in bass) / max(1, len(bass)), 3),
                },
            })
        return json.dumps({
            "task": "日本語でMIDI作曲案A/B/Cの特徴を比較して最良案を1つ選ぶ。実際の音声を聴いたとは主張しない。",
            "theme": package.get("theme_title"),
            "reference_targets": package.get("reference_features", {}),
            "learned_preferences": {key: profile.get(key) for key in ("active", "observations", "preferred_variant", "preferred_tags", "message") if profile.get(key) is not None},
            "variants": compact,
            "criteria": ["参考音源のBPM・キック型・ベース密度・キックとの分離傾向への近さ", "テーマと明示的な学習済み選好", "グルーヴと休符のバランス", "フレーズの一貫性"],
            "rule": "参考特徴とMIDI統計だけを根拠に、参考音源の抽象的な演奏特徴に近い案を優先して3案に1から100の点数を付ける。推定確度が低い特徴は重みを下げる。説明は各案一文。音符列のコピーは提案しない。",
        }, ensure_ascii=False)

    def _provider_worker(self) -> None:
        providers = ("freesound", "jamendo")
        provider_index = 0
        next_search = 0.0
        while not self._stop_event.is_set():
            delay = next_search - time.monotonic()
            if delay > 0 and self._stop_event.wait(min(delay, 1.0)):
                break
            source = providers[provider_index % len(providers)]
            provider_index += 1
            self._agent("provider_search", status="実行中", task=f"{source}を検索・権利条件確認")
            try:
                result = _run_reference_learning_cycle(
                    self.library, self.sources,
                    {"autonomous": True, "search_only": True, "source": source, "offset": 0},
                    sample_catalog=self.sample_catalog,
                )
                acquired = (result.get("acquired_reference") or {}).get("title")
                self._agent(
                    "provider_search", status="完了", task=f"{source}: 候補{result.get('search_candidate_count', 0)}件確認",
                    completed=self._agents["provider_search"]["completed"] + 1,
                    last_result={"source": source, "acquired": acquired,
                                 "selection": result.get("auto_candidate_selection"),
                                 "errors": result.get("discovery_errors", [])[:3]},
                )
                self._publish(last_cycle_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                              next_search_at=time.time() + self.SEARCH_INTERVAL_SECONDS)
            except Exception as exc:
                self._agent("provider_search", status="再試行待ち", task=str(exc),
                            failed=self._agents["provider_search"]["failed"] + 1)
            next_search = time.monotonic() + self.SEARCH_INTERVAL_SECONDS

    def _registered_worker(self) -> None:
        while not self._stop_event.is_set():
            if not self._analysis_lock.acquire(timeout=0.5):
                continue
            try:
                self._agent("registered_audio", status="処理中", task="未学習の登録音源を確認")
                now = time.monotonic()
                skipped_ids = [
                    reference_id
                    for reference_id, retry in self._registered_retry_state.items()
                    if retry.get("retry_at", 0.0) > now
                ]
                result = _run_reference_learning_cycle(
                    self.library, self.sources,
                    {"source": "youtube", "genres": ["登録音源"],
                     "local_only": True, "registered_only": True, "offset": 0,
                     "exclude_reference_ids": skipped_ids},
                    sample_catalog=self.sample_catalog,
                )
                learned = result.get("analyzed") or []
                if learned:
                    title = learned[0].get("user_title", "登録音源")
                    learned_id = (learned[0].get("generation_rules") or {}).get("reference_entry_id")
                    if learned_id and self._registered_retry_state.pop(learned_id, None):
                        self._registered_recovered += 1
                    if learned[0].get("card_id"):
                        self._enqueue_composition(learned[0]["card_id"], title)
                    self._agent("registered_audio", status="完了", task=f"解析済み: {title}",
                                completed=self._agents["registered_audio"]["completed"] + 1,
                                failed=len(self._registered_retry_state),
                                failure_attempts=self._registered_failure_attempts,
                                recovered=self._registered_recovered,
                                retrying=len(self._registered_retry_state),
                                last_item=title)
                    self._publish(last_result={"source": "registered_library", "analyzed": [title],
                                               "pending": result.get("registered_learning_queue_pending", 0),
                                               "registered_references_total": result.get("registered_learning_queue_total", 0),
                                               "analyzed_this_cycle": 1, "errors": []})
                elif result.get("discovery_errors"):
                    failure = result["discovery_errors"][0]
                    reference_id = str(failure.get("reference_id") or "")
                    previous = self._registered_retry_state.get(reference_id, {})
                    attempts = int(previous.get("attempts", 0)) + 1
                    cooldown = min(900, 15 * (2 ** min(attempts - 1, 6)))
                    if reference_id:
                        self._registered_retry_state[reference_id] = {
                            "title": failure.get("title", "登録音源"),
                            "attempts": attempts,
                            "retry_at": time.monotonic() + cooldown,
                            "last_error": failure.get("error", "解析エラー"),
                        }
                    self._registered_failure_attempts += 1
                    retry_count = len(self._registered_retry_state)
                    self._agent("registered_audio", status="再試行待ち",
                                task=f"{failure.get('title', '登録音源')}: {failure.get('error', '解析エラー')}（{attempts}回目、{cooldown}秒後に再試行）",
                                failed=retry_count,
                                failure_attempts=self._registered_failure_attempts,
                                recovered=self._registered_recovered,
                                retrying=retry_count,
                                last_error=failure.get("error", "解析エラー"))
                    self._publish(last_result={"source": "registered_library", "analyzed": [],
                                               "pending": result.get("registered_learning_queue_pending", 0),
                                               "registered_references_total": result.get("registered_learning_queue_total", 0),
                                               "analyzed_this_cycle": 0, "errors": [failure]})
                else:
                    pending = int(result.get("registered_learning_queue_pending", 0))
                    eligible = int(result.get("registered_learning_queue_eligible", pending))
                    retry_in = max(1, int(min(
                        (item.get("retry_at", now) - now for item in self._registered_retry_state.values()),
                        default=0,
                    )))
                    if pending and eligible == 0:
                        self._agent("registered_audio", status="再試行待ち",
                                    task=f"失敗音源を一時保留中。{retry_in}秒後に再試行し、他の音源を先に処理します",
                                    pending=pending, failed=len(self._registered_retry_state),
                                    failure_attempts=self._registered_failure_attempts,
                                    recovered=self._registered_recovered,
                                    retrying=len(self._registered_retry_state), next_retry_in=retry_in)
                    else:
                        self._agent("registered_audio", status="待機", task="未学習の登録音源なし",
                                    pending=pending, failed=len(self._registered_retry_state),
                                    failure_attempts=self._registered_failure_attempts,
                                    recovered=self._registered_recovered,
                                    retrying=len(self._registered_retry_state))
            except Exception as exc:
                self._agent("registered_audio", status="再試行待ち", task=str(exc),
                            failed=self._agents["registered_audio"]["failed"] + 1)
            finally:
                self._analysis_lock.release()
            wait_for = 0.5 if self._agents["registered_audio"]["status"] == "完了" else 3
            if self._agents["registered_audio"]["status"] == "再試行待ち":
                wait_for = min(15, max(1, self._agents["registered_audio"].get("next_retry_in", 3)))
            self._stop_event.wait(wait_for)

    def _sample_worker(self) -> None:
        while not self._stop_event.is_set():
            if not self.sample_catalog:
                self._agent("local_samples", status="利用不可", task="サンプル索引が未設定")
                self._stop_event.wait(5)
                continue
            sample_status = self.sample_catalog.status()
            pending = max(0, int(sample_status.get("count") or 0)
                          - int(sample_status.get("learning_completed") or 0)
                          - int(sample_status.get("learning_failed") or 0))
            if not pending:
                self._agent("local_samples", status="待機", task="未学習の索引サンプルなし", pending=0)
                self._stop_event.wait(3)
                continue
            if not self._analysis_lock.acquire(timeout=0.5):
                self._agent("local_samples", status="キュー待ち", task=f"解析待ち {pending}件")
                continue
            sample = None
            try:
                sample = self.sample_catalog.next_learning_sample(claim=True)
                if not sample:
                    self._agent("local_samples", status="待機", task="解析可能なサンプルなし", pending=0)
                    continue
                self._change_sample_active(1)
                self._agent("local_samples", status="処理中", task=f"解析中: {sample['name']}", current_item=sample["name"], pending=pending)
                analysis = analyze_reference_groove(Path(sample["path"]), scope="full_track")
                kick = analysis.get("kick_pattern", {})
                bass = analysis.get("bass_groove", {})
                energy = analysis.get("energy_dynamics", {})
                features = {
                    "bpm": analysis.get("bpm", {}).get("value"),
                    "bpm_confidence": analysis.get("bpm", {}).get("confidence"),
                    "kick_hits_per_bar": kick.get("hits_per_bar"),
                    "kick_regularity": kick.get("regularity"),
                    "bass_density_notes_per_bar": bass.get("density_notes_per_bar"),
                    "bass_rest_ratio": bass.get("rest_ratio"),
                    "bass_syncopation_ratio": bass.get("syncopation_ratio"),
                    "energy_trend": energy.get("trend"),
                    "dynamic_range_db": energy.get("dynamic_range_db"),
                    "recommended_key": analysis.get("generation_rules", {}).get("recommended_key"),
                    "duration_seconds": analysis.get("segment", {}).get("duration_seconds"),
                }
                self.sample_catalog.record_learning(sample["sample_id"], features)
                card = AnalysisCard.from_analysis(analysis_result=analysis, user_title=sample["name"],
                                                  audio_filename=sample["name"], card_id=f"sample_{sample['sample_id']}")
                _get_card_store(self.library).save(card)
                self._enqueue_composition(card.card_id, sample["name"])
                completed = self._increment_agent("local_samples", "completed")
                completed_since_start = max(0, completed - self._sample_completed_at_start)
                elapsed = max(1.0, time.monotonic() - self._started_at)
                rate = round(completed_since_start * 3600 / elapsed, 1) if elapsed >= 60 else None
                rate_label = f"約{rate}件/時" if rate is not None else "速度計測中"
                self._agent("local_samples", status="完了", task=f"解析済み: {sample['name']} / {rate_label}",
                            completed=completed,
                            last_item=sample["name"])
                self._publish(last_result={"source": "sample_catalog", "analyzed": [sample["name"]],
                                           "sample_pending": max(0, pending - 1), "analyzed_this_cycle": 1,
                                           "registered_references_total": len(self.library.entries()), "errors": []})
            except Exception as exc:
                if sample:
                    try:
                        self.sample_catalog.record_learning(sample["sample_id"], {}, error=str(exc))
                    except Exception:
                        pass
                self._agent("local_samples", status="再試行待ち", task=str(exc),
                            failed=self._increment_agent("local_samples", "failed"))
            finally:
                if sample:
                    self._change_sample_active(-1)
                self._analysis_lock.release()
            self._stop_event.wait(0.5)

    def _run(self) -> None:
        if self.sample_catalog:
            self.sample_catalog.release_abandoned_learning_claims()
        workers = [
            ("provider_search", self._provider_worker),
            ("registered_audio", self._registered_worker),
            *(("local_samples", self._sample_worker) for _ in range(self.LOCAL_SAMPLE_WORKERS)),
            ("composition", self._composition_worker),
            ("evaluation", self._evaluation_worker),
        ]
        self._agent("coordinator", status="実行中", task="担当エージェントを起動し、キューを監視")
        self._agent_threads = [threading.Thread(target=worker, name=f"kihachi-learning-{key}", daemon=True)
                              for key, worker in workers]
        for thread in self._agent_threads:
            thread.start()
        while not self._stop_event.wait(1):
            registered_total = len(self.library.entries())
            registered_status = self._agents["registered_audio"]
            sample_status = self.sample_catalog.status() if self.sample_catalog else {}
            sample_pending = max(0, int(sample_status.get("count") or 0)
                                 - int(sample_status.get("learning_completed") or 0)
                                 - int(sample_status.get("learning_failed") or 0))
            active = [self._agents[key]["name"] for key in ("provider_search", "registered_audio", "local_samples", "composition", "evaluation")
                      if self._agents[key].get("status") in {"実行中", "処理中"}]
            phase = "processing_pending" if active else "waiting"
            message = (f"担当稼働中: {'・'.join(active)} / 登録音源解析 {registered_status.get('completed', 0)}件 / "
                       f"索引サンプル未学習 {sample_pending}件 / 提供元検索は約1分間隔")
            self._agent("coordinator", status="実行中", task=f"登録音源{registered_total}件・サンプル残り{sample_pending}件を監視")
            self._publish(phase=phase, message=message, error=None)
        for thread in self._agent_threads:
            thread.join(timeout=2)
        for thread in self._agent_threads:
            thread.join()
        for key, _worker in workers:
            self._agent(key, status="停止", task="自律学習停止")
        self._agent("coordinator", status="停止", task="自律学習停止")
        self._publish(phase="stopped", message="バックグラウンド自律学習は停止中です")
        with self._lock:
            self._enabled = False


def _analysis_job_snapshot(job_id: str) -> dict | None:
    with _ANALYSIS_JOBS_LOCK:
        job = _ANALYSIS_JOBS.get(job_id)
        return dict(job) if job else None


def _prune_finished_analysis_jobs_locked() -> None:
    finished = [key for key, job in _ANALYSIS_JOBS.items() if job["status"] != "running"]
    for key in finished[:-_MAX_RETAINED_ANALYSIS_JOBS]:
        _ANALYSIS_JOBS.pop(key, None)


def _start_analysis_job(library, body: dict) -> dict:
    job_id = uuid.uuid4().hex
    client_request_id = str(body.get("client_request_id") or "")
    if client_request_id and not re.fullmatch(r"[a-f0-9]{32}", client_request_id):
        raise ReferenceError("解析要求IDの形式が不正です")
    request_fingerprint = _analysis_request_fingerprint(body)
    saved_card = (
        _saved_analysis_request_card(library, body, client_request_id, request_fingerprint)
        if client_request_id else None
    )
    with _ANALYSIS_JOBS_LOCK:
        if client_request_id:
            existing = next((
                job for job in _ANALYSIS_JOBS.values()
                if job.get("client_request_id") == client_request_id
            ), None)
            if existing:
                if existing.get("request_fingerprint") != request_fingerprint:
                    raise ReferenceError("同じ解析要求IDが異なる条件で再利用されました。画面を再読み込みして再度お試しください")
                return {"ok": True, "job_id": existing["job_id"], "status": existing["status"]}
        if saved_card:
            _ANALYSIS_JOBS[job_id] = {
                "ok": True, "job_id": job_id, "status": "completed", "progress": 1.0,
                "stage": "completed", "message": "保存済みの分析カードを復元しました",
                "card": saved_card, "error": None, "client_request_id": client_request_id,
                "request_fingerprint": request_fingerprint,
            }
            _prune_finished_analysis_jobs_locked()
            return {"ok": True, "job_id": job_id, "status": "completed"}
        active = sum(job["status"] == "running" for job in _ANALYSIS_JOBS.values())
        if active >= _MAX_ANALYSIS_JOBS:
            raise ReferenceError("同時に実行できる全曲解析は2件までです。完了後に再度お試しください")
        _ANALYSIS_JOBS[job_id] = {
            "ok": True, "job_id": job_id, "status": "running", "progress": 0.0,
            "stage": "queued", "message": "解析を開始しました", "card": None, "error": None,
            "client_request_id": client_request_id,
            "request_fingerprint": request_fingerprint,
        }
        _prune_finished_analysis_jobs_locked()

    def update(progress: float, stage: str, message: str | None = None) -> None:
        with _ANALYSIS_JOBS_LOCK:
            job = _ANALYSIS_JOBS.get(job_id)
            if job and job["status"] == "running":
                job["progress"] = max(job["progress"], min(0.99, float(progress)))
                job["stage"] = stage
                if message:
                    job["message"] = message

    def run() -> None:
        try:
            card = _handle_create_analysis_card(library, body, progress_callback=update)
            with _ANALYSIS_JOBS_LOCK:
                job = _ANALYSIS_JOBS.get(job_id)
                if job:
                    job.update(status="completed", progress=1.0, stage="completed", message="分析カードを保存しました", card=card.get("card"))
        except Exception as exc:
            with _ANALYSIS_JOBS_LOCK:
                job = _ANALYSIS_JOBS.get(job_id)
                if job:
                    job.update(status="failed", stage="failed", message="解析に失敗しました", error=str(exc))

    threading.Thread(target=run, name=f"reference-analysis-{job_id[:8]}", daemon=True).start()
    return {"ok": True, "job_id": job_id, "status": "running"}


def _get_card_store(library) -> AnalysisCardStore:
    return AnalysisCardStore(library.directory)


def handle_get(handler, library, parsed, sources, sample_catalog=None, learning_daemon=None) -> bool:
    req_path = parsed.path
    if req_path.startswith("/api/training/"):
        req_path = "/api/references" + req_path
    elif not req_path.startswith("/api/references/"):
        return False
    if not _local_host(handler):
        handler._send_json({"ok": False, "error": "localhostから開いてください"}, 403)
        return True
    if req_path.startswith("/api/references/sample-catalog/audio/"):
        _serve_sample_audio(handler, sample_catalog, parsed)
        return True
    if req_path.startswith("/api/references/audio/"):
        _serve_audio(handler, library, parsed)
        return True
    query = parse_qs(parsed.query)
    try:
        if req_path == "/api/references/status":
            result = library.status()
            result["providers"] = sources.status()
        elif req_path == "/api/references/learning-daemon":
            result = learning_daemon.status() if learning_daemon else {
                "ok": False, "enabled": False, "running": False,
                "phase": "unavailable", "message": "バックグラウンド学習機能を利用できません",
            }
        elif req_path == "/api/references/sample-catalog/status" and sample_catalog:
            result = sample_catalog.status()
        elif req_path == "/api/references/sample-catalog/search" and sample_catalog:
            tempo_text = query.get("tempo", [""])[0]
            result = sample_catalog.search(
                query.get("role", ["all"])[0],
                query.get("query", [""])[0],
                tempo=float(tempo_text) if tempo_text else None,
                key=query.get("key", [""])[0],
            )
        elif req_path == "/api/references/genres":
            result = {
                "ok": True,
                "genres": [
                    *[{"slug": g.slug, "name": g.name} for g in load_database()],
                    *[{"slug": name, "name": name} for name in library.custom_genres()],
                ],
            }
        elif req_path == "/api/references/list":
            result = {"ok": True, "entries": library.entries()}
        elif req_path == "/api/references/online/list":
            result = {"ok": True, "references": library.online_references()}
        elif req_path == "/api/references/analysis-cards":
            store = _get_card_store(library)
            result = {"ok": True, "cards": store.list_cards()}
        elif req_path == "/api/references/training/themes":
            from dataclasses import asdict
            result = {"ok": True, "themes": [asdict(t) for t in TRAINING_THEMES]}
        elif req_path == "/api/references/training/journal":
            from dataclasses import asdict
            store = TrainingJournalStore()
            result = {"ok": True, "entries": [asdict(e) for e in store.list_entries(strict=True)]}
        elif req_path == "/api/references/training/tips":
            store = TrainingJournalStore()
            result = {
                "ok": True,
                "tips": store.get_recent_tips_for_production(),
                "listening_references": library.online_references()[:12],
            }
        elif req_path == "/api/references/training/dashboard":
            references = library.online_references()
            result = TrainingJournalStore().learning_dashboard(references)
            genre_counts: dict[str, int] = {}
            for reference in references:
                genre = str(reference.get("genre") or "未分類").strip() or "未分類"
                genre_counts[genre] = genre_counts.get(genre, 0) + 1
            result["reference_library"] = {
                "registered_references": len(references),
                "genres": sorted(set(genre_counts) | set(result.get("genre_learning", {}))),
                "genre_counts": genre_counts,
                "with_listening_notes": sum(bool(str(reference.get("musical_notes") or "").strip()) for reference in references),
            }
            result["reference_library"]["genre_learning"] = result.get("genre_learning", {})
        elif re.fullmatch(r"/api/references/analysis-jobs/[a-f0-9]{32}", req_path):
            job_id = req_path.rsplit("/", 1)[-1]
            result = _analysis_job_snapshot(job_id)
            if result is None:
                handler._send_json({"ok": False, "error": "解析ジョブが見つかりません"}, 404)
                return True
        elif req_path == "/api/references/gemini/status":
            gemini = GeminiAIProvider()
            result = gemini.health()
        elif req_path.startswith("/api/references/analysis-cards/"):
            card_id = req_path.removeprefix("/api/references/analysis-cards/")
            store = _get_card_store(library)
            card = store.get(card_id)
            if card is None:
                handler._send_json({"ok": False, "error": "分析カードが見つかりません"}, 404)
                return True
            result = {"ok": True, "card": card.to_dict()}
        elif req_path == "/api/references/compare":
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
        _serve_audio_path(handler, library.audio_path(entry_id))
    except ReferenceError:
        handler._send_json({"ok": False, "error": "音源が見つかりません"}, 404)
    except (OSError, sqlite3.Error):
        handler._send_json({"ok": False, "error": "音源を読み取れません"}, 500)


def _serve_sample_audio(handler, sample_catalog, parsed) -> None:
    if not sample_catalog or not _same_origin_audio_request(handler, parsed):
        handler._send_json(
            {"ok": False, "error": "Studioの画面から再生してください"}, 403
        )
        return
    sample_id = parsed.path.removeprefix("/api/references/sample-catalog/audio/")
    try:
        _serve_audio_path(handler, sample_catalog.audio_path(sample_id))
    except ReferenceError:
        handler._send_json({"ok": False, "error": "サンプルが見つかりません"}, 404)
    except (OSError, sqlite3.Error):
        handler._send_json({"ok": False, "error": "サンプルを読み取れません"}, 500)


def _same_origin_audio_request(handler, parsed) -> bool:
    return not parsed.query and handler.headers.get(
        "Sec-Fetch-Site", "same-origin"
    ) in {"same-origin", "none"} and (
        not handler.headers.get("Origin")
        or handler.headers["Origin"] == "http://" + handler.headers.get("Host", "")
    )


def _serve_audio_path(handler, path: Path) -> None:
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


def _local_host(handler) -> bool:
    return urlparse("http://" + handler.headers.get("Host", "")).hostname in {
        "127.0.0.1",
        "localhost",
        "::1",
    }


def handle_post(handler, library, parsed, sources, sample_catalog=None, runtime=None, learning_daemon=None) -> bool:
    req_path = parsed.path
    if req_path.startswith("/api/training/"):
        req_path = "/api/references" + req_path
    elif not req_path.startswith("/api/references/"):
        return False
    origin = handler.headers.get("Origin")
    if not _local_host(handler) or (
        origin and origin != "http://" + handler.headers.get("Host", "")
    ):
        handler._send_json(
            {"ok": False, "error": "Studioの同じ画面から操作してください"}, 403
        )
        return True
    upload = req_path == "/api/references/upload"
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
            if req_path == "/api/references/tempo-proposal":
                result = propose_tempo(library, body)
            elif (
                req_path == "/api/references/sample-catalog/index" and sample_catalog
            ):
                result = sample_catalog.index(body.get("path", ""))
            elif req_path == "/api/references/import":
                path = body.get("path")
                if (
                    not isinstance(path, str)
                    or not Path(path).expanduser().is_absolute()
                ):
                    raise ReferenceError("音源の絶対パスを指定してください")
                result = library.import_file(Path(path), body)
            elif req_path == "/api/references/update":
                result = library.update(str(body.get("id", "")), body)
            elif req_path == "/api/references/search":
                result = sources.search(
                    str(body.get("source", "")), body.get("query", "")
                )
            elif req_path == "/api/references/learning-daemon":
                if not learning_daemon:
                    raise ReferenceError("バックグラウンド学習機能を利用できません")
                result = learning_daemon.start() if body.get("enabled") is True else learning_daemon.stop()
            elif req_path == "/api/references/learning-cycle":
                if learning_daemon and learning_daemon.status().get("enabled"):
                    raise ReferenceError("バックグラウンド学習中です。重複実行を防ぐため手動サイクルを停止しました")
                result = _run_reference_learning_cycle(
                    library, sources, body, sample_catalog=sample_catalog
                )
            elif req_path == "/api/references/online/save":
                result = library.save_online_reference(body)
            elif req_path == "/api/references/download":
                result = sources.import_remote(library, body)
            elif req_path == "/api/references/analysis-card/create":
                result = _handle_create_analysis_card(library, body)
            elif req_path == "/api/references/analysis-jobs":
                result = _start_analysis_job(library, body)
            elif req_path == "/api/references/analysis-card/enhance-gemini":
                result = _handle_enhance_gemini(library, body)
            elif req_path == "/api/references/analysis-card/generate-variants":
                result = _handle_generate_card_variants(library, body)
            elif req_path == "/api/references/analysis-card/promote-candidate":
                result = _handle_promote_candidate(library, body, runtime=runtime)
            elif req_path == "/api/references/analysis-card/evaluate":
                result = _handle_evaluate_analysis_card(library, body)
            elif req_path == "/api/references/training/session/start":
                result = _handle_training_session_start(library, body)
            elif req_path == "/api/references/training/session/generate-variants":
                result = _handle_training_generate_variants(library, body)
            elif req_path == "/api/references/training/session/promote-variant":
                result = _handle_training_promote_variant(library, body, runtime=runtime)
            elif req_path == "/api/references/training/journal/save":
                result = _handle_training_journal_save(library, body)
            else:
                handler._send_json({"ok": False, "error": "not found"}, 404)
                return True
        handler._send_json(result)
    except (ReferenceError, ValueError) as exc:
        message = (
            str(exc)
            if isinstance(exc, ReferenceError)
            or str(exc).startswith("学習履歴が読み取れないため、学習関連の処理を中止しました:")
            else "入力内容を確認してください"
        )
        handler._send_json({"ok": False, "error": message}, 400)
    except (OSError, sqlite3.Error):
        handler._send_json(
            {"ok": False, "error": "音源やDBの読み書きに失敗しました"}, 500
        )
    return True


def _learning_candidate_key(value: object) -> str | None:
    """Normalize provider URLs so a learned work is filtered from later results."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = urlparse(value.strip())
        host = (parsed.hostname or "").lower()
        if host in {"youtube.com", "www.youtube.com", "music.youtube.com", "m.youtube.com", "youtu.be"}:
            video_id, _ = ReferenceLibrary.parse_youtube_url(value)
            return f"youtube:{video_id}"
        if parsed.scheme not in {"http", "https"} or not host:
            return None
        return f"{parsed.scheme.lower()}://{host}{parsed.path.rstrip('/')}".casefold()
    except (ReferenceError, ValueError):
        return None


def _run_reference_learning_cycle(library, sources, body: dict, sample_catalog=None) -> dict:
    """Run one bounded discover -> analyze -> remember -> generate cycle.

    Online search metadata is returned to the browser only. YouTube audio is
    never downloaded or analyzed; DSP only runs on assets already imported
    into the rights-confirmed local library or user-indexed sample catalog.
    """
    autonomous = body.get("autonomous") is True
    search_only = body.get("search_only") is True
    local_only = bool(body.get("local_only", False)) and not autonomous
    registered_only = body.get("registered_only") is True
    try:
        offset = max(0, int(body.get("offset", 0)))
    except (TypeError, ValueError):
        raise ReferenceError("学習キュー位置が不正です") from None
    source = str(body.get("source", "youtube"))
    if source not in {"youtube", "freesound", "jamendo"}:
        raise ReferenceError("検索元を選択してください")
    raw_genres = body.get("genres")
    if autonomous and offset == 0:
        genres = [random.choice(load_database()).name]
    else:
        if not isinstance(raw_genres, list) or not 1 <= len(raw_genres) <= 3:
            raise ReferenceError("1サイクルのジャンルは1〜3個にしてください")
        genres = []
        for value in raw_genres:
            if not isinstance(value, str) or not 1 <= len(value.strip()) <= 64:
                raise ReferenceError("ジャンル名は1〜64文字で入力してください")
            genre = value.strip()
            if genre not in genres:
                genres.append(genre)
    discovery = []
    errors = []
    if not registered_only and not local_only and (not autonomous or offset == 0):
        for genre in genres:
            try:
                query_suffix = " electronic music" if source == "jamendo" else " electronic music loop"
                response = sources.search(source, genre + query_suffix)
                discovery.append({"genre": genre, "results": response["results"][:10]})
            except ReferenceError as exc:
                errors.append({"genre": genre, "error": str(exc)})

    # Prefer registered, rights-confirmed reference assets. Autonomous mode
    # can fall back to the user's indexed local sample catalog; search results
    # are never downloaded in this cycle.
    requested = {canonical_genre(g).casefold() for g in genres}
    # Keep imported assets with a missing/stale analysis in the retry queue too.
    # The learning handler can analyze the stored audio file again; filtering on
    # cached analysis here would leave these entries pending forever.
    all_local_entries = library.entries()
    local_entries = [
        entry for entry in all_local_entries
        if requested.intersection(
            canonical_genre(str(g)).casefold() for g in entry.get("genres", [])
        )
    ]
    store = _get_card_store(library)
    already_learned_references = set()
    stale_confirmed_tempo_references = set()
    local_entries_by_id = {entry["id"]: entry for entry in all_local_entries}
    cards_by_reference = {}
    for item in store.list_cards(limit=1000):
        card = store.get(item["card_id"])
        if card is None:
            continue
        reference_entry_id = card.generation_rules.get("reference_entry_id")
        if reference_entry_id in local_entries_by_id:
            cards_by_reference.setdefault(reference_entry_id, card)
    for reference_entry_id, card in cards_by_reference.items():
        entry = local_entries_by_id[reference_entry_id]
        confirmed_bpm = entry.get("confirmed_bpm")
        bpm_data = card.bpm if isinstance(card.bpm, dict) else {}
        if confirmed_bpm is not None and (
            bpm_data.get("method") != "user_specified_hint"
            or abs(float(bpm_data.get("value") or 0) - float(confirmed_bpm)) > 0.05
        ):
            stale_confirmed_tempo_references.add(reference_entry_id)
            continue
        already_learned_references.add(reference_entry_id)
    learned_candidate_keys = {
        key
        for entry in all_local_entries
        if entry["id"] in already_learned_references
        for key in [_learning_candidate_key(entry.get("source_url"))]
        if key
    }
    # A manually listened and annotated YouTube reference is already a learned
    # item for discovery purposes, even though its audio is never downloaded.
    if hasattr(library, "online_references"):
        learned_candidate_keys.update(
            key
            for reference in library.online_references()
            if reference.get("listened") and str(reference.get("musical_notes") or "").strip()
            for key in [_learning_candidate_key(reference.get("source_url"))]
            if key
        )
    excluded_learned_candidates = 0
    seen_candidate_keys = set()
    for group in discovery:
        filtered = []
        for candidate in group["results"]:
            key = _learning_candidate_key(candidate.get("source_url"))
            if key and (key in learned_candidate_keys or key in seen_candidate_keys):
                excluded_learned_candidates += 1
                continue
            if key:
                seen_candidate_keys.add(key)
            filtered.append(candidate)
        group["results"] = filtered
    acquired_reference = None
    auto_import_status = None
    auto_candidate_selection = {
        "considered": sum(len(group["results"]) for group in discovery),
        "download_allowed": 0,
        "license_eligible": 0,
        "rejected_download": 0,
        "rejected_license": 0,
        "attempted": [],
        "selected": None,
    }
    if autonomous and source in {"freesound", "jamendo"}:
        auto_import_status = "no_eligible_candidate"
        for group in discovery:
            for candidate in group["results"]:
                if not candidate.get("download_allowed"):
                    auto_candidate_selection["rejected_download"] += 1
                    continue
                auto_candidate_selection["download_allowed"] += 1
                if not _autonomous_license_allowed(source, candidate.get("license", "")):
                    auto_candidate_selection["rejected_license"] += 1
                    continue
                auto_candidate_selection["license_eligible"] += 1
                if (
                    len(auto_candidate_selection["attempted"])
                    >= AutonomousLearningDaemon.MAX_AUTOMATIC_IMPORT_ATTEMPTS
                ):
                    auto_import_status = "attempt_limit_reached"
                    break
                attempt = {
                    "title": candidate.get("title", "音源"),
                    "license": candidate.get("license", ""),
                    "genre": group["genre"],
                    "result": "attempting",
                }
                auto_candidate_selection["attempted"].append(attempt)
                try:
                    imported = sources.import_remote(
                        library,
                        {
                            "source": source,
                            "remote_id": candidate.get("remote_id"),
                            "expected_license": candidate.get("license"),
                            "kind": "track" if source == "jamendo" else "loop",
                            "genres": group["genre"],
                            "rights_confirmed": True,
                            "autonomous": True,
                            "notes": "自動学習: 提供元の取得許可とCC0/CC BYライセンスを確認して取得。",
                        },
                    )
                    imported_entry = imported.get("entry", {})
                    acquired_reference = {
                        "entry_id": imported_entry.get("id"),
                        "title": imported_entry.get("title", candidate.get("title", "音源")),
                        "source": source,
                        "source_url": candidate.get("source_url"),
                        "license": candidate.get("license"),
                        "duplicate": imported.get("duplicate", False),
                    }
                    attempt["result"] = "selected_and_imported"
                    auto_candidate_selection["selected"] = {
                        "title": acquired_reference["title"],
                        "license": acquired_reference["license"],
                        "genre": group["genre"],
                    }
                    auto_import_status = "acquired"
                    break
                except ReferenceError as exc:
                    attempt["result"] = "failed"
                    attempt["error"] = str(exc)
                    errors.append({
                        "genre": group["genre"],
                        "title": candidate.get("title", "音源"),
                        "error": str(exc),
                    })
                    auto_import_status = "failed"
            if acquired_reference or auto_import_status == "attempt_limit_reached":
                break
        if auto_import_status == "no_eligible_candidate":
            errors.append({
                "genre": genres[0],
                "error": "取得可能かつCC0/CC BY（NC/ND/SAなし）の候補がありません。認証設定、提供元のダウンロード許可、ライセンスを確認してください。",
            })
    if search_only:
        pending_registered = sum(
            1 for entry in all_local_entries if entry["id"] not in already_learned_references
        )
        if acquired_reference and acquired_reference.get("entry_id") not in local_entries_by_id:
            pending_registered += 1
        return {
            "ok": True, "source": source, "autonomous": True,
            "genres": genres, "discovery": discovery,
            "search_candidate_count": sum(len(group["results"]) for group in discovery),
            "acquired_reference": acquired_reference,
            "auto_candidate_selection": auto_candidate_selection,
            "discovery_errors": errors,
            "registered_learning_queue_total": len(all_local_entries) + int(bool(acquired_reference and acquired_reference.get("entry_id") not in local_entries_by_id)),
            "registered_learning_queue_pending": pending_registered,
            "analyzed": [],
        }
    if registered_only:
        # Select the next eligible unfinished item. Per-item cooldowns are
        # supplied by the daemon so a repeatedly failing file cannot starve
        # the rest of the registered library.
        excluded_ids = {
            str(value) for value in body.get("exclude_reference_ids", [])
            if isinstance(value, str)
        }
        local_entries = [
            entry for entry in all_local_entries
            if entry["id"] not in already_learned_references
            and entry["id"] not in excluded_ids
        ]
    analysis_fallback = False
    analysis_fallback_reason = None
    if (local_only or autonomous) and not registered_only:
        if offset == 0:
            local_entries = [
                entry for entry in local_entries
                if entry["id"] not in already_learned_references
            ]
        if autonomous and offset == 0 and stale_confirmed_tempo_references:
            local_entries = [
                entry for entry in all_local_entries
                if entry["id"] in stale_confirmed_tempo_references
            ][:2]
            analysis_fallback = bool(local_entries)
            if analysis_fallback:
                analysis_fallback_reason = "confirmed_tempo_refresh"
        # A random discovery genre must not make a cycle silently skip all
        # learning when the rights-confirmed local library has other genres.
        if autonomous and offset == 0 and not local_entries:
            local_entries = [
                entry for entry in all_local_entries
                if entry["id"] not in already_learned_references
            ][:2]
            analysis_fallback = bool(local_entries)
            if analysis_fallback:
                analysis_fallback_reason = "other_registered_genre"
    queue_entries = []
    if autonomous and not registered_only:
        # Each search cycle also processes the next pending registered item,
        # even when the random genre differs from the item's stored genre.
        queue_entries = [
            entry for entry in all_local_entries
            if entry["id"] not in already_learned_references
        ]
        if queue_entries:
            sample_status = sample_catalog.status() if sample_catalog else {}
            sample_pending = max(0, int(sample_status.get("count") or 0) - int(sample_status.get("learning_completed") or 0) - int(sample_status.get("learning_failed") or 0))
            local_entries = queue_entries[:2]
            analysis_fallback = True
            analysis_fallback_reason = "registered_learning_queue"
        else:
            sample_status = sample_catalog.status() if sample_catalog else {}
            sample_pending = max(0, int(sample_status.get("count") or 0) - int(sample_status.get("learning_completed") or 0) - int(sample_status.get("learning_failed") or 0))
            if sample_pending:
                analysis_fallback = True
                analysis_fallback_reason = "sample_catalog_queue"
    batch_size = 1 if registered_only else 2
    batch = local_entries[offset : offset + batch_size]
    analyzed = []
    successful_reference_ids = set()
    for entry in batch:
        try:
            previous_card_id = next(
                (
                    item["card_id"]
                    for item in store.list_cards(limit=1000)
                    if (store.get(item["card_id"]) is not None)
                    and store.get(item["card_id"]).generation_rules.get(
                        "reference_entry_id"
                    ) == entry["id"]
                ),
                None,
            )
            card_result = _handle_create_analysis_card(
                library,
                {
                    "reference_id": entry["id"],
                    "title": entry["title"],
                    "card_id": previous_card_id,
                    "scope": "segment",
                    "start_sec": 0,
                    "duration_sec": 90,
                    "confirmed_bpm": entry.get("confirmed_bpm"),
                },
            )
            card = store.get(card_result["card"]["card_id"])
            if card:
                card.generation_rules["reference_genres"] = list(entry.get("genres", []))
                card.generation_rules["reference_entry_id"] = entry["id"]
                card.generation_rules["reference_source"] = entry.get("source", "local")
                store.save(card)
                analyzed.append(card.to_dict())
                successful_reference_ids.add(entry["id"])
        except Exception as exc:
            errors.append({
                "reference_id": entry["id"],
                "title": entry.get("title", "音源"),
                "genre": ", ".join(entry.get("genres", [])),
                "error": str(exc) or type(exc).__name__,
            })

    sample_learning = None
    sample_learning_card = None
    if autonomous and not registered_only and analysis_fallback_reason == "sample_catalog_queue" and not analyzed and sample_catalog:
        sample = sample_catalog.next_learning_sample(claim=True)
        if sample:
            try:
                sample_analysis = analyze_reference_groove(
                    Path(sample["path"]), scope="full_track"
                )
                kick = sample_analysis.get("kick_pattern", {})
                bass = sample_analysis.get("bass_groove", {})
                energy = sample_analysis.get("energy_dynamics", {})
                features = {
                    "bpm": sample_analysis.get("bpm", {}).get("value"),
                    "bpm_confidence": sample_analysis.get("bpm", {}).get("confidence"),
                    "kick_hits_per_bar": kick.get("hits_per_bar"),
                    "kick_regularity": kick.get("regularity"),
                    "bass_density_notes_per_bar": bass.get("density_notes_per_bar"),
                    "bass_rest_ratio": bass.get("rest_ratio"),
                    "bass_syncopation_ratio": bass.get("syncopation_ratio"),
                    "energy_trend": energy.get("trend"),
                    "dynamic_range_db": energy.get("dynamic_range_db"),
                    "recommended_key": sample_analysis.get("generation_rules", {}).get(
                        "recommended_key"
                    ),
                    "duration_seconds": sample_analysis.get("segment", {}).get(
                        "duration_seconds"
                    ),
                }
                sample_catalog.record_learning(sample["sample_id"], features)
                sample_learning = {
                    "sample_id": sample["sample_id"],
                    "name": sample["name"],
                    "role": sample["role"],
                    "features": features,
                }
                sample_learning_card = AnalysisCard.from_analysis(
                    analysis_result=sample_analysis,
                    user_title=sample["name"],
                    audio_filename=sample["name"],
                    card_id=f"sample_{sample['sample_id']}",
                )
                sample_learning_card.generation_rules["reference_genres"] = list(genres)
                _get_card_store(library).save(sample_learning_card)
                analyzed.append({
                    "user_title": sample["name"],
                    "card_id": f"sample_{sample['sample_id']}",
                    "bpm": sample_analysis.get("bpm", {}),
                    "learning_source": "sample_catalog",
                    "sample_features": features,
                })
                analysis_fallback = True
                analysis_fallback_reason = "sample_catalog"
            except Exception as exc:
                sample_catalog.record_learning(
                    sample["sample_id"], {}, error=str(exc)
                )
                errors.append({"genre": genres[0], "error": f"ローカルサンプル解析: {exc}"})

    card = None
    if analyzed:
        card = store.get(analyzed[-1]["card_id"])
        if card is None and sample_learning_card is not None:
            card = sample_learning_card
    elif not local_only and not autonomous:
        cards = store.list_cards(limit=1)
        if cards:
            card = store.get(cards[0]["card_id"])
    training = None
    if card:
        theme = next(t for t in TRAINING_THEMES if t.theme_id == "kick_bass_space")
        profile = TrainingJournalStore().learning_profile(
            theme.theme_id, card.card_id, library.online_references()
        )
        training = generate_training_variants(
            theme=theme,
            card=card,
            key=card.generation_rules.get("key") or "D#m",
            bars=8,
            learning_profile=profile,
        )
    pending_before = sum(
        1 for entry in all_local_entries
        if entry["id"] not in already_learned_references
    )
    if autonomous and acquired_reference:
        acquired_id = acquired_reference.get("entry_id")
        if acquired_id and acquired_id not in local_entries_by_id:
            pending_before += 1
    # A successful retry may create a new card or refresh an existing card.
    # Count successful items that were pending before this batch, so the UI can
    # keep selecting the head of the shrinking pending queue without skipping
    # entries as the queue is reordered.
    pending_after = max(
        0,
        pending_before - sum(
            entry_id in successful_reference_ids
            and entry_id not in already_learned_references
            for entry_id in (entry["id"] for entry in batch)
        ),
    )
    pending_after += sum(
        1 for item in analyzed
        if item.get("learning_source") == "sample_catalog"
    )

    if registered_only:
        # Report all unfinished references, including items temporarily delayed
        # for retry, separately from the number currently eligible to process.
        pending_after = max(0, len(all_local_entries) - len(already_learned_references)
                            - len(successful_reference_ids))
    pending_total_after = max(0, pending_after)

    return {
        "ok": True,
        "source": source,
        "local_only": local_only,
        "autonomous": autonomous,
        "registered_only": registered_only,
        "processed_reference_ids": [entry["id"] for entry in batch]
        + ([f"sample:{sample_learning['sample_id']}"] if sample_learning else []),
        "local_matches_total": len(local_entries) + int(sample_learning is not None),
        "registered_references_total": len(all_local_entries),
        "registered_references_pending": pending_total_after,
        "next_offset": offset + len(batch) + int(sample_learning is not None),
        "remaining_local_references": max(
            0,
            len(local_entries)
            + int(sample_learning is not None)
            - offset
            - len(batch)
            - int(sample_learning is not None),
        ),
        "analysis_fallback": analysis_fallback,
        "analysis_fallback_reason": analysis_fallback_reason,
        "learning_status": "learned" if analyzed else "no_new_local_audio",
        "registered_learning_queue_total": len(all_local_entries),
        "registered_learning_queue_pending": pending_total_after,
        "registered_learning_queue_eligible": len(local_entries),
        "analyzed_this_cycle": len(analyzed),
        "search_candidate_count": sum(len(group["results"]) for group in discovery),
        "sample_learning": sample_learning,
        "genres": genres,
        "discovery": discovery,
        "excluded_learned_candidates": excluded_learned_candidates,
        "acquired_reference": acquired_reference,
        "auto_import_status": auto_import_status,
        "auto_candidate_selection": auto_candidate_selection,
        "discovery_errors": errors,
        "analyzed": analyzed,
        "learning_profile": profile if card else None,
        "training": training,
        "limitations": [
            "オンライン検索結果はこの画面に一時表示し、サーバーへ検索結果を保存しません。",
            "自動学習では権利確認済み登録音源を優先し、対象がない場合はユーザーが索引化したローカルサンプルを解析します。",
            "YouTubeは公式プレイヤーでの視聴候補のみです。音声取得・自動聴取・DSP解析はしません。",
            "YouTube検索候補は表示だけです。実際の解析は登録音源または索引化済みローカルサンプルを対象とし、音源をコピー・送信しません。",
            "作曲案の個人化はA/B/Cの人による評価記録から学びます。音源解析の特徴量は別に保存します。",
        ],
    }


def _handle_create_analysis_card(library, body: dict, progress_callback=None) -> dict:
    reference_id = body.get("reference_id")
    raw_path = body.get("path")
    if reference_id:
        audio_path = library.audio_path(str(reference_id))
    elif raw_path:
        p = Path(raw_path).expanduser()
        if not p.is_absolute() or not p.is_file():
            raise ReferenceError("有効な音源ファイルの絶対パスを指定してください")
        audio_path = p
    else:
        raise ReferenceError("reference_id または音源ファイルの絶対パスを指定してください")

    raw_bpm_hint = body.get("confirmed_bpm")
    try:
        user_hint_bpm = float(raw_bpm_hint) if raw_bpm_hint not in (None, "") else None
    except (TypeError, ValueError):
        raise ReferenceError("確認済みBPMは数値で指定してください") from None
    if user_hint_bpm is not None and not 60 <= user_hint_bpm <= 180:
        raise ReferenceError("確認済みBPMは60〜180の範囲で指定してください")

    raw_scope = str(body.get("scope") or body.get("analysis_scope") or "").strip().lower()
    if raw_scope in ("segment", "part"):
        scope = "segment"
        start_sec = float(body.get("start_sec") or body.get("start_seconds") or 0.0)
        duration_sec = float(body.get("duration_sec") or body.get("duration_seconds") or 16.0)
        if duration_sec <= 0:
            raise ReferenceError("分析区間の長さは正の秒数を指定してください")
        analysis_path = audio_path
        converted_path = None
        try:
            # soundfile cannot decode some supported library formats (notably
            # M4A). Decode only the requested excerpt to a temporary WAV; keep
            # the original asset and its provenance untouched.
            if audio_path.suffix.lower() == ".m4a":
                if progress_callback:
                    progress_callback(0.03, "audio_conversion", "M4Aの解析区間を一時変換しています")
                with NamedTemporaryFile(suffix=".wav", delete=False) as temp_audio:
                    converted_path = Path(temp_audio.name)
                try:
                    decoded = subprocess.run(
                        ["ffmpeg", "-nostdin", "-v", "error", "-ss", str(start_sec),
                         "-t", str(duration_sec), "-i", str(audio_path), "-vn",
                         "-ac", "1", "-ar", "22050", "-c:a", "pcm_s16le", "-y",
                         str(converted_path)],
                        capture_output=True, timeout=120, check=False,
                    )
                except FileNotFoundError:
                    raise ReferenceError("M4A解析にはFFmpegが必要です") from None
                except subprocess.TimeoutExpired:
                    raise ReferenceError("M4Aの一時変換が時間上限に達しました") from None
                if decoded.returncode != 0 or not converted_path.is_file() or converted_path.stat().st_size == 0:
                    raise ReferenceError("M4Aを読み取れません。形式・破損・保護の有無を確認してください")
                if progress_callback:
                    progress_callback(0.10, "audio_loading", "一時音声を解析しています")
                analysis_path = converted_path
            analysis = analyze_reference_groove(
                analysis_path,
                start_seconds=0 if converted_path else start_sec,
                duration_seconds=duration_sec,
                scope="segment",
                user_hint_bpm=user_hint_bpm,
                progress_callback=progress_callback,
            )
            if converted_path and isinstance(analysis.get("segment"), dict):
                analysis["segment"]["start_seconds"] = start_sec
        finally:
            if converted_path is not None:
                converted_path.unlink(missing_ok=True)
    else:
        # Default is full-track analysis from 0 to end of file
        scope = "full_track"
        analysis = analyze_reference_groove(
            audio_path,
            scope="full_track",
            progress_callback=progress_callback,
            user_hint_bpm=user_hint_bpm,
        )

    title = str(body.get("title") or body.get("user_title") or audio_path.stem)
    client_request_id = str(body.get("client_request_id") or "")
    card = AnalysisCard.from_analysis(
        analysis_result=analysis,
        user_title=title,
        audio_filename=audio_path.name,
        card_id=(body.get("card_id") or (f"card_request_{client_request_id}" if re.fullmatch(r"[a-f0-9]{32}", client_request_id) else None)),
    )
    if re.fullmatch(r"[a-f0-9]{32}", client_request_id):
        card.execution_stats["analysis_request_id"] = client_request_id
        card.execution_stats["analysis_request_fingerprint"] = _analysis_request_fingerprint(body)

    gemini_mode = str(body.get("gemini_mode") or "")
    if not gemini_mode and body.get("use_gemini"):
        gemini_mode = "gemini"

    genre_str = str(body.get("genre") or "")
    # MVP: Interpret extracted mechanical metrics via Gemini. Audio files are never sent directly.
    if gemini_mode in ("gemini", "mode_a", "auto", "true", "1"):
        if progress_callback:
            progress_callback(0.94, "gemini_interpretation", "測定結果をもとに理論解釈を生成しています")
        gemini_res = enhance_groove_with_gemini(
            mechanical_analysis=card.to_dict(),
            genre=genre_str,
            title=title,
        )
        card.apply_gemini_enhancement(gemini_res)
    else:
        card.gemini_status = "skipped_not_requested"
        card.analysis_mode = "local_only"

    store = _get_card_store(library)
    if progress_callback:
        progress_callback(0.98, "saving", "分析カードを保存しています")
    store.save(card)
    return {"ok": True, "card": card.to_dict()}


def _handle_enhance_gemini(library, body: dict) -> dict:
    card_id = str(body.get("card_id") or "")
    if not card_id:
        raise ReferenceError("card_id を指定してください")
    store = _get_card_store(library)
    card = store.get(card_id)
    if card is None:
        raise ReferenceError("指定された分析カードが見つかりません")

    genre = str(body.get("genre") or "")

    # MVP: Enhance using extracted mechanical metrics only.
    gemini_res = enhance_groove_with_gemini(
        mechanical_analysis=card.to_dict(),
        genre=genre,
        title=card.user_title,
    )

    card.apply_gemini_enhancement(gemini_res)
    store.save(card)
    return {
        "ok": gemini_res.get("ok", False),
        "card": card.to_dict(),
        "enhanced": gemini_res.get("ok", False),
        "analysis_mode": card.analysis_mode,
        "gemini_status": card.gemini_status,
        "fallback_used": gemini_res.get("fallback_used", False),
        "error": gemini_res.get("error"),
        "model": gemini_res.get("model"),
    }


def _handle_generate_card_variants(library, body: dict) -> dict:
    card_id = str(body.get("card_id") or "")
    if not card_id:
        raise ReferenceError("card_id を指定してください")
    store = _get_card_store(library)
    card = store.get(card_id)
    if card is None:
        raise ReferenceError("指定された分析カードが見つかりません")

    key = str(body.get("key") or "D#m")
    bars = int(body.get("bars") or body.get("bar_count") or 8)

    result = generate_variants_from_card(
        card,
        bars=bars,
        key=key,
    )
    return result


def _handle_promote_candidate(library, body: dict, runtime=None) -> dict:
    card_id = str(body.get("card_id") or "")
    if not card_id:
        raise ReferenceError("card_id を指定してください")
    store = _get_card_store(library)
    card = store.get(card_id)
    if card is None:
        raise ReferenceError("指定された分析カードが見つかりません")

    variant_id = str(body.get("variant_id") or "A").upper()
    key = str(body.get("key") or "") or None
    bars = int(body.get("bars") or body.get("bar_count") or 64)
    seed = int(body.get("seed") or 42)

    candidate = promote_variant_to_candidate(
        card=card,
        variant_id=variant_id,
        bars=bars,
        key=key,
        seed=seed,
    )

    # Persist candidate to storage
    stored = False
    c_dir = Path(candidate_store_dir())
    c_dir.mkdir(parents=True, exist_ok=True)
    c_path = c_dir / f"{candidate.candidate_id}.json"
    c_path.write_text(json.dumps(candidate.to_dict(), indent=2), encoding="utf-8")
    stored = True

    if runtime is not None:
        try:
            store_fn = getattr(runtime, "store_candidate", None) or getattr(runtime, "_store", None)
            if store_fn is not None:
                store_fn(candidate)
        except Exception:
            pass
        try:
            select_fn = getattr(runtime, "select", None)
            if select_fn is not None:
                select_fn(candidate.candidate_id)
        except Exception:
            pass

    return {
        "ok": True,
        "candidate_id": candidate.candidate_id,
        "card_id": card.card_id,
        "variant_id": variant_id,
        "stored": stored,
        "tempo": candidate.brief.tempo.value,
        "tempo_confidence": card.bpm.get("confidence", 0.0) if isinstance(card.bpm, dict) else getattr(card.bpm, "confidence", 0.0),
        "key": candidate.brief.key.value,
        "key_confidence": card.harmonic_analysis.get("confidence", 0.0) if isinstance(card.harmonic_analysis, dict) else getattr(card.harmonic_analysis, "confidence", 0.0),
        "bars": candidate.brief.bars.value,
        "clip_count": len(candidate.clips),
        "note_fingerprint": candidate.note_fingerprint,
        "candidate": candidate.to_dict(),
        "live_mutation_warning": "この候補はStudioの候補リストに登録されました。Liveへの変更は一切行われていません。変更にはDry Runと明示的な人間の承認が必要です。",
    }


def _handle_evaluate_analysis_card(library, body: dict) -> dict:
    card_id = str(body.get("card_id") or "")
    if not card_id:
        raise ReferenceError("card_id を指定してください")
    store = _get_card_store(library)
    card = store.get(card_id)
    if card is None:
        raise ReferenceError("指定された分析カードが見つかりません")

    user_notes = body.get("user_notes") or body.get("notes")
    user_rating = body.get("user_rating") or body.get("rating")
    rating_str = str(user_rating) if user_rating is not None else None
    adopted = body.get("adopted")
    if adopted is None and "adopted_variant" in body:
        adopted = bool(body["adopted_variant"] and body["adopted_variant"] != "None")
    rejection_reason = body.get("rejection_reason")
    manual_overrides = body.get("manual_adjustments") or body.get("manual_overrides")

    card.apply_user_feedback(
        rating=rating_str,
        notes=user_notes,
        adopted=adopted,
        rejection_reason=rejection_reason,
        manual_overrides=manual_overrides,
    )
    store.save(card)
    return {"ok": True, "card": card.to_dict()}


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


def _handle_training_session_start(library, body: dict) -> dict:
    from dataclasses import asdict
    card_id = body.get("card_id")
    theme_id = body.get("theme_id")
    if not theme_id:
        raise ReferenceError("theme_id を指定してください")
    theme = next((t for t in TRAINING_THEMES if t.theme_id == theme_id), None)
    if not theme:
        raise ReferenceError(f"未知の学習テーマ: {theme_id}")
    store = _get_card_store(library)
    card = store.get(card_id) if card_id else None
    if not card:
        cards = store.list_cards()
        if cards:
            card = store.get(cards[0]["card_id"])
    if not card:
        raise ReferenceError("トレーニング対象となる分析カードを選択または作成してください")

    rules = card.generation_rules
    reference_features = reference_generation_profile(card, resolve_reference_key(card))
    return {
        "ok": True,
        "theme": asdict(theme),
        "source": {
            "card_id": card.card_id,
            "title": card.user_title,
            "tempo": reference_features["tempo_bpm"],
            "key": resolve_reference_key(card),
            "reference_features": reference_features,
            "bass_syncopation": rules.get("bass_syncopation", 0.5),
            "bass_rest_ratio": rules.get("bass_rest_ratio", 0.35),
            "kick_style": rules.get("kick_style", "four_on_the_floor"),
        },
        "session_id": uuid.uuid4().hex[:12],
    }


def _handle_training_generate_variants(library, body: dict) -> dict:
    card_id = body.get("card_id")
    theme_id = body.get("theme_id")
    key = body.get("key")
    bars = int(body.get("bars", 8))
    if not theme_id:
        raise ReferenceError("theme_id を指定してください")
    theme = next((t for t in TRAINING_THEMES if t.theme_id == theme_id), None)
    if not theme:
        raise ReferenceError(f"未知の学習テーマ: {theme_id}")
    store = _get_card_store(library)
    card = store.get(card_id) if card_id else None
    if not card:
        raise ReferenceError("分析カードが見つかりません")
    journal = TrainingJournalStore()
    profile = journal.learning_profile(theme.theme_id, card.card_id, library.online_references())
    return generate_training_variants(
        theme=theme, card=card, key=key, bars=bars, learning_profile=profile,
        reference_conditioned=True,
    )


def _handle_training_promote_variant(library, body: dict, runtime=None) -> dict:
    card_id = body.get("card_id")
    theme_id = body.get("theme_id")
    variant_id = body.get("variant_id", "A")
    bars = int(body.get("bars", 64))
    key = body.get("key")
    theme = next((t for t in TRAINING_THEMES if t.theme_id == theme_id), None)
    if not theme:
        raise ReferenceError(f"未知の学習テーマ: {theme_id}")
    store = _get_card_store(library)
    card = store.get(card_id) if card_id else None
    if not card:
        raise ReferenceError("分析カードが見つかりません")
    candidate = promote_training_variant_to_candidate(
        card=card,
        theme=theme,
        variant_id=variant_id,
        bars=bars,
        key=key,
        learning_profile=TrainingJournalStore().learning_profile(
            theme.theme_id, card.card_id, library.online_references()
        ),
    )
    # Saving A/B/C for audition is preparation, not evidence of preference.
    # In the sound-design exercise all three candidates may be saved for a
    # controlled comparison; the human's chosen variant is recorded later by
    # the explicit training-journal endpoint.
    learned_entry = (
        None if theme_id == "bass_timbre"
        else TrainingJournalStore().record_promoted_candidate(candidate)
    )
    return {
        "ok": True,
        "candidate_id": candidate.candidate_id,
        "candidate_name": f"【学習候補】{card.user_title} ({theme.title} 案{variant_id})",
        "bars": bars,
        "clips_count": len(candidate.clips),
        "learning_observation_recorded": learned_entry is not None,
        "learning_observation": (
            {
                "theme_id": learned_entry.theme_id,
                "variant_id": learned_entry.chosen_variant_id,
                "source": "explicit_candidate_promotion",
            }
            if learned_entry else None
        ),
        "live_mutation_warning": "Studio候補として保存されました。Liveへの変更は一切行われていません。変更計画の確認（Dry Run）と明示的承認が必要です。",
    }


def _handle_training_journal_save(library, body: dict) -> dict:
    from datetime import UTC, datetime
    theme_id = body.get("theme_id", "")
    theme = next((t for t in TRAINING_THEMES if t.theme_id == theme_id), None)
    theme_title = theme.title if theme else body.get("theme_title", "不明なテーマ")
    theme_category = theme.category if theme else "general"

    entry = TrainingJournalEntry(
        entry_id=uuid.uuid4().hex[:10],
        theme_id=theme_id,
        theme_title=theme_title,
        theme_category=theme_category,
        source_type=body.get("source_type", "reference_card"),
        source_id=body.get("source_id", ""),
        source_title=body.get("source_title", "無題の音源"),
        chosen_variant_id=body.get("chosen_variant_id", "A"),
        chosen_variant_name=body.get("chosen_variant_name", "案A"),
        user_feedback_tags=body.get("user_feedback_tags", []),
        user_notes=body.get("user_notes", "").strip(),
        key_takeaway=body.get("key_takeaway", "").strip(),
        next_action_tip=body.get("next_action_tip", "").strip(),
        created_at=datetime.now(UTC).isoformat(),
        promoted_candidate_id=body.get("promoted_candidate_id"),
        genre=str(body.get("genre") or (theme.target_genre if theme else "")).strip(),
        genre_basis=str(body.get("genre_basis") or ("user_selected" if body.get("genre") else "theme_target")),
    )
    store = TrainingJournalStore()
    sound_design_recipe = {}
    if theme_id == "bass_timbre" and entry.chosen_variant_id in {"A", "B", "C"}:
        card = _get_card_store(library).get(entry.source_id)
        if not card:
            raise ReferenceError("音色設定を保存する題材カードが見つかりません")
        package = generate_training_variants(
            theme=theme,
            card=card,
            key=None,
            bars=8,
            seed=42,
            reference_conditioned=True,
        )
        selected = next(
            item for item in package["variants"]
            if item["variant_id"] == entry.chosen_variant_id
        )
        sound_design_recipe = selected["live_sound_design"]
        entry.sound_design_recipe = sound_design_recipe
        entry.user_notes = (
            f"[音色レシピ: Ableton Operator / 案{entry.chosen_variant_id}]\n"
            f"{json.dumps(sound_design_recipe, ensure_ascii=False)}\n"
            f"{entry.user_notes}"
        )[:4000]
    reference_id = body.get("youtube_reference_id")
    if reference_id:
        reference = library.get_online_reference(str(reference_id))
        entry.user_notes = (
            f"[YouTube参照: {reference['title']} / {reference['source_url']}]\n"
            f"[聴取メモ: {reference['musical_notes']}]\n{entry.user_notes}"
        )[:4000]
    store.save_entry(entry)
    return {
        "ok": True,
        "entry_id": entry.entry_id,
        "sound_design_recipe": sound_design_recipe,
        "message": "選択結果と参照メモを保存しました。5回以上の記録が蓄積すると、同じ題材・テーマの次回A/B/C生成に反映します。",
    }
