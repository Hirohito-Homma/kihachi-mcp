"""Read-only production orchestrator. Writes stop at APPROVAL_REQUIRED."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from kihachi_mcp.models.live_mutation import LiveMutationPlan
from kihachi_mcp.services.jarvis.brain import (
    plan_from_brain,
    review_songspec_for_jarvis,
    songspec_for_brain,
)
from kihachi_mcp.services.jarvis.clip_review import (
    ClipReader,
    evaluate_midi_clip,
    format_clip_evaluation_reply,
    preview_midi_clip_revision,
    read_session_midi_clip,
    resolve_clip_target,
)
from kihachi_mcp.services.jarvis.context import UNKNOWN, build_context
from kihachi_mcp.services.jarvis.gateway_read import read_gateway
from kihachi_mcp.services.jarvis.intent import (
    _direction,
    _range_outside_identifiers,
    _scope,
    _write_requested,
    parse_intent,
    validate_intent,
)
from kihachi_mcp.services.jarvis.live_agreement import agree_live_reads
from kihachi_mcp.services.jarvis.plan import build_plan
from kihachi_mcp.services.jarvis.revision import bars_for
from kihachi_mcp.services.jarvis.state import JarvisStateMachine, JarvisTransitionError
from kihachi_mcp.services.jarvis.verification import settle_readback
from kihachi_mcp.services.jarvis.write_gate import Tool, request_gateway_write
from kihachi_mcp.services.live_approval_gate import ApprovalGate

ContextReader = Callable[[], dict[str, Any]]
Reviewer = Callable[[dict[str, Any]], dict[str, Any]]
LiveClient = Mapping[str, Callable[..., dict[str, Any]]]


class JarvisOrchestrator:
    """Interpret one utterance, read context, and either report or propose."""

    def __init__(
        self,
        context_reader: ContextReader | None = None,
        reviewer: Reviewer | None = None,
        live_client: LiveClient | None = None,
        clip_reader: ClipReader | None = None,
    ) -> None:
        self._reader = context_reader or _empty_context
        self._reviewer = reviewer or review_songspec_for_jarvis
        self._live_client = live_client
        self._clip_reader = clip_reader
        self._machine = JarvisStateMachine()

    def status(self) -> dict[str, Any]:
        return {
            "ok": True,
            "state": self._machine.state,
            "live_changed": False,
            "executed": False,
        }

    def request_gateway_write(
        self,
        tool: Tool,
        plan: LiveMutationPlan,
        gate: ApprovalGate,
        *,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Refuse a gateway write unless ApprovalGate already holds this plan.

        ``confirm=True`` is not enough, and this method never sets execute.
        The turn state stays where ``ask`` left it.
        """
        result = request_gateway_write(tool, plan, gate, confirm=confirm, execute=False)
        result["state"] = self._machine.state
        result["states"] = list(self._machine.history)
        return result

    def settle_readback(self, comparison: str) -> dict[str, Any]:
        """Record readback order for an approval-waiting write. Live is not called."""
        if self._machine.state != "APPROVAL_REQUIRED":
            return self._verification_result(
                "承認待ちの書き込みだけを検証します。完了にはしていません。",
                False,
            )
        self._machine.transition("APPROVED")
        self._machine.transition("EXECUTING")
        try:
            message = settle_readback(self._machine, comparison)
        except JarvisTransitionError:
            return self._verification_result(
                "検証の順序を守れませんでした。完了にはしていません。",
                False,
            )
        matched = self._machine.state == "HUMAN_AUDITION_REQUIRED"
        return self._verification_result(message, matched)

    def inspect_midi_clip(
        self,
        *,
        track_index: int | None = None,
        track_name: str | None = None,
        clip_index: int | None = None,
        utterance: str = "",
        direction: str | None = None,
        scope: str | None = None,
        bar_range: dict[str, int] | tuple[int, int] | None = None,
    ) -> dict[str, Any]:
        """Read one Session MIDI clip via get_midi_clip_notes, evaluate facts, and preview revision."""
        self._machine.reset()
        self._machine.transition("UNDERSTANDING")
        return self._run_midi_clip_slice(
            utterance=utterance,
            parsed_intent=parse_intent(utterance) if utterance.strip() else None,
            track_index=track_index,
            track_name=track_name,
            clip_index=clip_index,
            explicit_direction=direction,
            explicit_scope=scope,
            explicit_range=bar_range,
        )

    def ask(
        self,
        utterance: str,
        *,
        track_index: int | None = None,
        track_name: str | None = None,
        clip_index: int | None = None,
    ) -> dict[str, Any]:
        self._machine.reset()
        self._machine.transition("UNDERSTANDING")
        intent = parse_intent(utterance)
        has_explicit_clip_args = (
            track_index is not None or track_name is not None or clip_index is not None
        )
        if (
            has_explicit_clip_args
            or intent["type"] in {"evaluate_midi_clip", "modify_midi_clip"}
            or intent.get("clip_target") is not None
        ):
            return self._run_midi_clip_slice(
                utterance=utterance,
                parsed_intent=intent,
                track_index=track_index,
                track_name=track_name,
                clip_index=clip_index,
            )
        if not intent["recognized"]:
            self._machine.transition("UNKNOWN")
            return self._result(
                intent,
                build_plan(intent),
                None,
                str(intent.get("error") or "依頼を構造化できませんでした。Liveは変更していません。"),
            )
        self._machine.transition("READING_STATE")
        try:
            raw = self._reader()
        except Exception:  # noqa: BLE001 - reader failures stay UNKNOWN
            self._machine.transition("UNKNOWN")
            return self._result(
                intent,
                build_plan(intent),
                None,
                "プロジェクト状態を読めませんでした。成功にはしていません。",
            )
        if not isinstance(raw, dict) or not raw.get("ok"):
            self._machine.transition("FAILED")
            return self._result(
                intent,
                build_plan(intent),
                None,
                "プロジェクト状態の取得に失敗しました。Liveは変更していません。",
            )
        context = build_context(
            project=raw.get("project") if isinstance(raw.get("project"), dict) else None,
            live=raw.get("live") if isinstance(raw.get("live"), dict) else None,
            ollama=raw.get("ollama") if isinstance(raw.get("ollama"), dict) else None,
            project_error=raw.get("project_error"),
        )
        live_payload = raw.get("live") if isinstance(raw.get("live"), dict) else None
        if isinstance(live_payload, dict) and ("tempo" in live_payload or "track_count" in live_payload):
            context["live_read"] = live_payload
        self._machine.transition("PLANNING")
        plan = build_plan(intent)
        if intent["type"] == "musical_judgment":
            return self._judge_music(intent, plan, context, raw)
        if intent["type"] == "inspect_project" and self._live_client is not None:
            read = read_gateway(self._live_client)
            if not read["ok"]:
                self._machine.transition("UNKNOWN")
                return self._result(intent, plan, context, read["error"])
            context = {**context, "gateway_read": read["results"]}
            agreement = _live_agreement(context, read["results"])
            if agreement is not None and not agreement["ok"]:
                self._machine.transition("UNKNOWN")
                return self._result(intent, plan, context, agreement["reply"])
        if intent["write_requested"]:
            self._machine.transition("APPROVAL_REQUIRED")
            return self._result(intent, plan, context, _proposal_reply(intent))
        self._machine.transition("PROPOSED")
        self._machine.transition("COMPLETED")
        return self._result(intent, plan, context, _report_reply(intent, context))

    def _judge_music(
        self,
        intent: dict[str, Any],
        plan: dict[str, Any],
        context: dict[str, Any],
        raw: dict[str, Any],
    ) -> dict[str, Any]:
        project = raw.get("project") if isinstance(raw.get("project"), dict) else None
        songspec = project.get("songspec") if isinstance(project, dict) else None
        if not isinstance(songspec, dict):
            self._machine.transition("PROPOSED")
            self._machine.transition("COMPLETED")
            return self._result(
                intent,
                plan,
                context,
                "渡す SongSpec がありません。Liveは変更していません。",
            )
        songspec = songspec_for_brain(
            songspec,
            project.get("arrangement") if isinstance(project, dict) else None,
        )
        try:
            review = self._reviewer(songspec)
        except Exception:  # noqa: BLE001 - a failed judgment is not success
            self._machine.transition("UNKNOWN")
            return self._result(
                intent,
                plan,
                context,
                "音楽判断を読めませんでした。成功にはしていません。",
            )
        if not isinstance(review, dict) or review.get("live_changed"):
            self._machine.transition("UNKNOWN")
            return self._result(
                intent,
                plan,
                context,
                "Liveを変えた応答は採用しません。成功にはしていません。",
            )
        try:
            plan = plan_from_brain(plan, review)
        except (TypeError, ValueError):
            self._machine.transition("UNKNOWN")
            return self._result(
                intent,
                plan,
                context,
                "Brain の案を計画にできませんでした。成功にはしていません。",
            )
        detail = " / ".join(plan["proposal"]) if plan["proposal"] else "指摘はありません。"
        self._machine.transition("PROPOSED")
        self._machine.transition("COMPLETED")
        return self._result(
            intent,
            plan,
            context,
            f"Brain の案を計画にしました。{detail} Liveには書いていません。音の完成ではありません。",
        )

    def _run_midi_clip_slice(
        self,
        *,
        utterance: str,
        parsed_intent: dict[str, Any] | None,
        track_index: int | None = None,
        track_name: str | None = None,
        clip_index: int | None = None,
        explicit_direction: str | None = None,
        explicit_scope: str | None = None,
        explicit_range: dict[str, int] | tuple[int, int] | None = None,
    ) -> dict[str, Any]:
        intent = dict(parsed_intent) if isinstance(parsed_intent, dict) else parse_intent(utterance)
        parsed_target = intent.get("clip_target") if isinstance(intent.get("clip_target"), dict) else {}

        if parsed_target.get("ambiguous"):
            self._machine.transition("UNKNOWN")
            return self._clip_result(
                intent,
                build_plan(intent),
                None,
                str(parsed_target.get("error") or intent.get("error") or "クリップ指定が曖昧です。Liveは変更していません。"),
                evaluation=None,
                preview=None,
                complete=False,
            )

        if (
            track_index is not None
            and parsed_target.get("track_index") is not None
            and track_index != parsed_target.get("track_index")
        ) or (
            track_name is not None
            and parsed_target.get("track_name") is not None
            and track_name != parsed_target.get("track_name")
        ) or (
            clip_index is not None
            and parsed_target.get("clip_index") is not None
            and clip_index != parsed_target.get("clip_index")
        ):
            self._machine.transition("UNKNOWN")
            return self._clip_result(
                intent,
                build_plan(intent),
                None,
                "発話内のトラック/クリップ指定と引数の指定が一致しません。1つの対象だけを指定してください。Liveは変更していません。",
                evaluation=None,
                preview=None,
                complete=False,
            )

        eff_track_index = track_index if track_index is not None else parsed_target.get("track_index")
        eff_track_name = track_name if track_name is not None else parsed_target.get("track_name")
        eff_clip_index = clip_index if clip_index is not None else parsed_target.get("clip_index")

        resolved = resolve_clip_target(
            track_index=eff_track_index,
            track_name=eff_track_name,
            clip_index=eff_clip_index,
            parse_error=parsed_target.get("error") if (track_index is None and track_name is None and clip_index is None) else None,
        )
        if not resolved["ok"]:
            self._machine.transition("UNKNOWN")
            return self._clip_result(
                intent,
                build_plan(intent),
                None,
                resolved["error"],
                evaluation=None,
                preview=None,
                complete=False,
            )

        text = (utterance or "").strip()
        span = intent.get("range")
        if explicit_range is not None:
            if isinstance(explicit_range, tuple) and len(explicit_range) == 2:
                span = {"start": int(explicit_range[0]), "end": int(explicit_range[1])}
            elif isinstance(explicit_range, dict) and "start" in explicit_range and "end" in explicit_range:
                span = {"start": int(explicit_range["start"]), "end": int(explicit_range["end"])}
        elif span is None and text:
            span = _range_outside_identifiers(text)
        if isinstance(span, dict) and span["start"] >= span["end"]:
            self._machine.transition("UNKNOWN")
            return self._clip_result(
                intent,
                build_plan(intent if intent.get("recognized") else {"recognized": False}),
                None,
                "小節範囲の開始は終了より前にしてください。Liveは変更していません。",
                evaluation=None,
                preview=None,
                complete=False,
            )

        write_req = bool(explicit_direction) or bool(intent.get("write_requested")) or (bool(text) and _write_requested(text))
        direction = explicit_direction or intent.get("direction") or (_direction(text) if write_req else None)
        scope = explicit_scope if explicit_scope is not None else (intent.get("scope") or (_scope(text) if text else None))
        target_label = (
            resolved["track_name"]
            if resolved["track_name"] is not None
            else f"track_{resolved['track_index']}"
        )
        intent = {
            "type": "modify_midi_clip" if write_req else "evaluate_midi_clip",
            "target": target_label,
            "scope": scope,
            "range": span,
            "direction": direction if write_req else None,
            "requested_action": "modify" if write_req else "evaluate",
            "write_requested": write_req,
            "recognized": True,
            "clip_target": {
                "track_index": resolved["track_index"],
                "track_name": resolved["track_name"],
                "clip_index": resolved["clip_index"],
            },
        }
        validate_intent(intent)

        self._machine.transition("READING_STATE")
        clip_read = read_session_midi_clip(
            track_index=resolved["track_index"],
            track_name=resolved["track_name"],
            clip_index=resolved["clip_index"],
            reader=self._resolve_clip_reader(),
        )
        if not clip_read["ok"] or not clip_read.get("complete"):
            self._machine.transition("UNKNOWN")
            return self._clip_result(
                intent,
                build_plan(intent),
                None,
                clip_read["error"],
                evaluation=None,
                preview=None,
                complete=False,
            )

        self._machine.transition("PLANNING")
        evaluation = evaluate_midi_clip(clip_read, scope=scope, bar_range=span)
        preview = preview_midi_clip_revision(
            clip_read,
            evaluation,
            direction=direction,
            scope=scope,
            bar_range=span,
        )
        plan = {
            **build_plan(intent),
            "clip_evaluation": evaluation,
            "clip_preview": preview,
        }
        context = {
            "project_present": False,
            "project_error": None,
            "candidate_id": UNKNOWN,
            "tempo": evaluation["tempo"],
            "key": UNKNOWN,
            "genre": UNKNOWN,
            "bars": evaluation["bars"],
            "sections": UNKNOWN,
            "tracks": UNKNOWN,
            "approved": UNKNOWN,
            "applied": UNKNOWN,
            "verification": UNKNOWN,
            "live_connected": True,
            "live_state": "ready",
            "ollama_state": UNKNOWN,
            "timbre": UNKNOWN,
            "perceived_audio_quality": UNKNOWN,
            "musical_quality_claimed": False,
            "clip": evaluation,
            "clip_preview": preview,
        }
        if preview.get("out_of_range"):
            self._machine.transition("UNKNOWN")
            requested = preview.get("requested_bars") or {}
            reply = (
                f"指定された{requested.get('start')}–{requested.get('end')}小節は"
                f"クリップの長さ（{evaluation['bar_count']}小節）を超えています。"
                "範囲を切り詰めて評価・改訂案を作ることはしません。ノートは変更しておらず、Liveも変更していません。"
            )
            return self._clip_result(
                intent,
                plan,
                context,
                reply,
                evaluation=evaluation,
                preview=preview,
                complete=True,
            )
        if write_req:
            self._machine.transition("APPROVAL_REQUIRED")
        else:
            self._machine.transition("PROPOSED")
            self._machine.transition("COMPLETED")
        reply = format_clip_evaluation_reply(evaluation, preview, write_requested=write_req)
        return self._clip_result(
            intent,
            plan,
            context,
            reply,
            evaluation=evaluation,
            preview=preview,
            complete=True,
        )

    def _resolve_clip_reader(self) -> ClipReader | None:
        if self._clip_reader is not None:
            return self._clip_reader
        if isinstance(self._live_client, Mapping):
            candidate = self._live_client.get("get_midi_clip_notes")
            if callable(candidate):
                return candidate
        return None

    def _clip_result(
        self,
        intent: dict[str, Any],
        plan: dict[str, Any],
        context: dict[str, Any] | None,
        reply: str,
        *,
        evaluation: dict[str, Any] | None,
        preview: dict[str, Any] | None,
        complete: bool,
    ) -> dict[str, Any]:
        base = self._result(intent, plan, context, reply)
        base.update(
            {
                "complete": complete,
                "clip_evaluation": evaluation,
                "evaluation": evaluation,
                "clip_preview": preview,
                "preview": preview,
            }
        )
        return base

    def _verification_result(self, message: str, matched: bool) -> dict[str, Any]:
        return {
            "ok": matched,
            "state": self._machine.state,
            "states": list(self._machine.history),
            "reply": message,
            "executed": False,
            "live_changed": False,
            "musical_quality_claimed": False,
        }

    def _result(
        self,
        intent: dict[str, Any],
        plan: dict[str, Any],
        context: dict[str, Any] | None,
        reply: str,
    ) -> dict[str, Any]:
        state = self._machine.state
        return {
            "ok": state in {"COMPLETED", "APPROVAL_REQUIRED"},
            "state": state,
            "states": list(self._machine.history),
            "intent": intent,
            "plan": plan,
            "context": context,
            "reply": reply,
            "live_changed": False,
            "executed": False,
            "musical_quality_claimed": False,
        }


def _live_agreement(context: dict[str, Any], results: dict[str, Any]) -> dict[str, Any] | None:
    status = results.get("ableton_status")
    live = context.get("live_read")
    if not isinstance(status, dict) or not isinstance(live, dict):
        return None
    return agree_live_reads(live, status)


def _empty_context() -> dict[str, Any]:
    return {"ok": True, "project": None, "project_error": "候補がありません", "live": None, "ollama": None}


def _report_reply(intent: dict[str, Any], context: dict[str, Any]) -> str:
    if intent["type"] == "inspect_project":
        return _project_sentence(context) + "Liveへの変更は行っていません。"
    target = _target_label(intent["target"])
    scope = _scope_label(intent.get("scope"))
    detail = _target_detail(intent, context)
    where = f"{scope}の" if scope else ""
    return f"{where}{target}を読み取りました。{detail}Liveは変更していません。"


def _proposal_reply(intent: dict[str, Any]) -> str:
    target = _target_label(intent["target"])
    span = intent.get("range")
    where = f"{span['start']}–{span['end']}の" if span else ""
    action = {
        "reduce_density": "密度を下げる",
        "increase_density": "密度を上げる",
        "delete": "削除する",
    }.get(intent["direction"], "変更する")
    return (
        f"{where}{target}について、{action}変更案を作成しました。"
        "Liveはまだ変更していません。実行には承認が必要です。"
    )


def _project_sentence(context: dict[str, Any]) -> str:
    if not context["project_present"]:
        reason = context.get("project_error") or "開いている候補はありません。"
        live = _live_sentence(context)
        return f"{reason}{live}"
    sections = context["sections"]
    names = "、".join(str(item.get("name")) for item in sections) if isinstance(sections, list) else UNKNOWN
    approved = "承認済み" if context["approved"] is True else "未承認" if context["approved"] is False else UNKNOWN
    return (
        f"現在の候補は{context['genre']}、{context['tempo']} BPM、{context['key']}、"
        f"{context['bars']}小節です。構成は{names}。承認は{approved}です。"
        f"{_live_sentence(context)}"
    )


def _target_detail(intent: dict[str, Any], context: dict[str, Any]) -> str:
    tracks = context["tracks"]
    if not isinstance(tracks, list):
        return "トラック情報は UNKNOWN です。"
    matched = [track for track in tracks if _same_target(track, intent["target"])]
    if not matched:
        return f"{_target_label(intent['target'])}のトラックは候補に見当たりません。"
    notes = sum(int(track.get("note_count") or 0) for track in matched)
    span = bars_for(intent, {"bars": context.get("bars"), "sections": context.get("sections")})
    per_bar = [track.get("bar_notes") for track in matched]
    if span is None or not all(isinstance(item, list) and item for item in per_bar):
        return f"ノート数は{notes}です。"
    first, last = span
    inside = sum(sum(item[first - 1 : last]) for item in per_bar)
    outside = sum(sum(item) for item in per_bar) - inside
    length = min(last, len(per_bar[0])) - first + 1
    rest = len(per_bar[0]) - length
    if length <= 0:
        return f"ノート数は{notes}です。{first}–{last}小節は曲の外です。"
    sentence = f"{first}–{last}小節は1小節あたり{inside / length:.1f}音"
    if rest > 0:
        sentence += f"、それ以外は{outside / rest:.1f}音"
    return f"{sentence}です。ノート数は全体で{notes}です。"


def _same_target(track: dict[str, Any], target: str) -> bool:
    part = str(track.get("part") or "").casefold()
    label = str(track.get("label") or "").casefold()
    if target == "bass":
        return part in {"bass", "sub"} or "bass" in label
    if target == "sub":
        return part == "sub" or "sub" in label
    if target == "slap":
        return "slap" in label or part == "bass"
    return part == target or target in label


def _live_sentence(context: dict[str, Any]) -> str:
    state = context["live_state"]
    if state == UNKNOWN:
        return "Liveの状態は UNKNOWN です。"
    return f"Liveの接続状態は{state}です。"


def _target_label(target: str | None) -> str:
    return {
        "bass": "ベース",
        "sub": "サブ",
        "slap": "スラップ",
        "kick": "キック",
        "hats": "ハット",
        "openhat": "オープンハット",
        "snare": "スネア",
        "perc": "パーカッション",
        "stab": "スタブ",
        "pad": "パッド",
        "arp": "アルペジオ",
        "lead": "リード",
        "guitar": "ギター",
        "horn": "ホーン",
        "vocal": "ボーカル",
        "chords": "コード",
    }.get(target or "", target or "対象")


def _scope_label(scope: str | None) -> str:
    return {
        "later_section": "後半",
        "earlier_section": "前半",
        "reentry": "Re-entry",
    }.get(scope or "", "")
