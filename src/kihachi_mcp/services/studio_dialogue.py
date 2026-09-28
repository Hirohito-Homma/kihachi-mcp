"""Bounded, deterministic conversation drafts; never executes Studio or Live actions."""

from __future__ import annotations

import re
import sqlite3
import threading
import time
import unicodedata
import uuid
from dataclasses import dataclass, field

from kihachi_mcp.services.reference_analysis import ReferenceError

_SEARCH_ROLES = {
    "キック": "kick",
    "ハイハット": "hats",
    "スネア": "snare",
    "クラップ": "clap",
}


@dataclass
class _Conversation:
    last_used: float = 0.0
    target: str = ""
    proposal_id: str = ""
    original_brief: str = ""
    proposed_brief: str = ""
    samples: list[dict] = field(default_factory=list)
    awaiting_sample_role: bool = False
    pending_search: str = ""


class StudioDialogue:
    """Keep a short-lived turn context and make reviewable text proposals."""

    def __init__(self, sample_catalog=None) -> None:
        self._lock = threading.Lock()
        self._sessions: dict[str, _Conversation] = {}
        self._sample_catalog = sample_catalog

    def turn(self, session_id: str, utterance: str, brief: str) -> dict:
        if (
            not isinstance(session_id, str)
            or not re.fullmatch(r"[0-9a-f]{32}", session_id)
            or not isinstance(utterance, str)
            or not isinstance(brief, str)
            or not 1 <= len(utterance.strip()) <= 500
            or len(brief) > 4000
        ):
            return {"ok": False, "error": "対話ID、発話、制作指示を確認してください"}
        said = utterance.strip()
        with self._lock:
            now = time.monotonic()
            for key in [
                key
                for key, value in self._sessions.items()
                if now - value.last_used > 3600
            ]:
                del self._sessions[key]
            if session_id not in self._sessions and len(self._sessions) >= 32:
                oldest = min(
                    self._sessions, key=lambda key: self._sessions[key].last_used
                )
                del self._sessions[oldest]
            state = self._sessions.setdefault(session_id, _Conversation())
            state.last_used = now
            state.proposal_id = ""
            state.original_brief = ""
            state.proposed_brief = ""
            target = _named_target(said)
            if target:
                state.target = target
            if re.search(r"Live|Ableton|適用|反映|入れて|展開", said, re.IGNORECASE):
                reply = (
                    "Liveの変更はこの対話から実行しません。候補の適用内容を画面で"
                    "プレビューし、対象Setと変更点を確認してください。"
                )
                return _reply(reply, state)
            if re.search(r"生成して|再生成|作り直して|削除|消して|保存して", said):
                return _reply(
                    "その操作は対話からまだ実行できません。制作画面で対象と操作内容を確認してください。",
                    state,
                )
            selection = re.fullmatch(
                r"([1-5一二三四五])番(?:を)?(?:詳しく|見せて|教えて|の詳細)?[。？?]?",
                unicodedata.normalize("NFKC", said),
            )
            if selection:
                digit = selection[1]
                number = (
                    int(digit) if digit.isdigit() else "一二三四五".index(digit) + 1
                )
                if number > len(state.samples):
                    return _reply(
                        "その番号の候補はありません。先にサンプルを検索してください。",
                        state,
                    )
                sample = state.samples[number - 1]
                return {
                    **_reply(
                        f"{number}番のファイル情報を表示しました。音色・利用条件はまだ確認していません。",
                        state,
                    ),
                    "sample_detail": sample,
                }
            if re.search(r"探して|探す|検索", said) or (
                state.awaiting_sample_role and target
            ):
                search_text = (
                    state.pending_search
                    if state.awaiting_sample_role
                    and target
                    and not re.search(r"探して|探す|検索", said)
                    else said
                )
                state.samples = []
                if state.target not in _SEARCH_ROLES:
                    state.awaiting_sample_role = True
                    state.pending_search = said
                    return _reply(
                        "キック、ハイハット、スネア、クラップのどれを探しますか？",
                        state,
                    )
                state.awaiting_sample_role = False
                state.pending_search = ""
                if self._sample_catalog is None:
                    return _reply(
                        "サンプル索引が接続されていません。参考音源画面で確認してください。",
                        state,
                    )
                quoted = re.search(r"[「\"]([^」\"]{1,80})[」\"]", search_text)
                query = quoted[1] if quoted else ""
                if not quoted:
                    query = re.sub(
                        r"(?:を)?(?:探して|探す|検索して|検索)(?:ください)?[。？?]?$",
                        "",
                        search_text,
                    )
                    query = re.sub(
                        r"ハイハット|キック|ハット|スネア|クラップ|サンプル|音源|kick|hi.?hat|snare|clap",
                        "",
                        query,
                        flags=re.IGNORECASE,
                    ).strip(" のを、。")
                if re.search(r"太い|太く|明る|暗い|暗く|柔らか|硬い", query):
                    return _reply(
                        "今はファイル名で検索できます。音色の特徴ではまだ選べません。例: 909のキックを探して。",
                        state,
                    )
                try:
                    result = self._sample_catalog.search(
                        _SEARCH_ROLES[state.target], query, limit=5
                    )
                except (ReferenceError, OSError, sqlite3.Error):
                    return _reply(
                        "サンプル索引を検索できませんでした。参考音源画面で保存先を確認してください。",
                        state,
                    )
                state.samples = result["results"]
                return {
                    **_reply(
                        f"ファイル名の検索で{result['count']}件見つかりました。先頭{len(state.samples)}件を表示します。番号で詳しく確認できます。"
                        if state.samples
                        else "該当するファイル名がありません。別の検索語を指定してください。",
                        state,
                    ),
                    "search_results": state.samples,
                    "search_query": query,
                    "search_count": result["count"],
                }
            if re.search(r"それ|その音|もう少し|さっき", said) and not state.target:
                return _reply(
                    "どの音・候補を指していますか？ 例: キックをもう少し太く。", state
                )
            if re.search(r"太く|明るく|暗く|強く|弱く|広く|狭く", said):
                return _reply(
                    f"{state.target or '対象'}のどの点を変えますか？ "
                    "音色・音量・配置から選んで具体的に教えてください。",
                    state,
                )
            if re.search(r"再生|聴かせ|聞かせ|検索|比較", said):
                return _reply(
                    "この操作の音声対話はまだ接続されていません。"
                    "参考音源画面または候補画面で操作してください。",
                    state,
                )
            # Keep both texts verbatim; no guessed tempo, genre or interpretation.
            proposed = brief.rstrip() + ("\n" if brief.strip() else "") + said
            if len(proposed) > 4000:
                return _reply("制作指示が4000文字を超えるため追記できません。", state)
            state.proposal_id = uuid.uuid4().hex
            state.original_brief = brief
            state.proposed_brief = proposed
            return {
                **_reply(
                    "発話をそのまま制作指示へ追記する案です。内容を確認してください。",
                    state,
                ),
                "proposal": {"id": state.proposal_id, "brief": proposed},
            }

    def accept(self, session_id: str, proposal_id: str, current_brief: str) -> dict:
        if not isinstance(session_id, str) or not isinstance(current_brief, str):
            return {
                "ok": False,
                "error": "提案が古くなりました。もう一度話してください",
            }
        with self._lock:
            state = self._sessions.get(session_id)
            if (
                state is None
                or not isinstance(proposal_id, str)
                or not proposal_id
                or proposal_id != state.proposal_id
                or current_brief != state.original_brief
            ):
                return {
                    "ok": False,
                    "error": "提案が古くなりました。もう一度話してください",
                }
            brief = state.proposed_brief
            state.proposal_id = ""
            state.original_brief = ""
            state.proposed_brief = ""
            return {"ok": True, "brief": brief, "generation_started": False}


def _named_target(said: str) -> str:
    for name, pattern in (
        ("キック", r"キック|kick"),
        ("ハイハット", r"ハイハット|ハット|hi.?hat"),
        ("スネア", r"スネア|snare"),
        ("クラップ", r"クラップ|clap"),
        ("ベース", r"ベース|bass"),
    ):
        if re.search(pattern, said, re.IGNORECASE):
            return name
    return ""


def _reply(text: str, state: _Conversation) -> dict:
    return {
        "ok": True,
        "reply": text,
        "target": state.target,
        "action_executed": False,
        "live_changed": False,
    }
