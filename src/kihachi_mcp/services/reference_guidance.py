"""Preview-only BPM guidance from curated reference data."""

import math
import re
import unicodedata

from kihachi_mcp.services.reference_analysis import ReferenceError


def propose_tempo(library, body: dict) -> dict:
    original = body.get("brief")
    if not isinstance(original, str) or not original.strip() or len(original) > 4000:
        raise ReferenceError("制作指示は1〜4000文字で入力してください")
    genre, kind = body.get("genre", ""), body.get("kind", "")
    comparison = library.compare(genre, kind)
    confirmed = comparison["confirmed_bpm"]
    result = {
        "ok": True,
        "original_brief": original,
        "proposed_brief": original,
        "genre": genre,
        "kind": kind,
        "confirmed_bpm": confirmed,
        "suggested_bpm": None,
        "changed": False,
        "generation_applied": False,
        "warning": comparison["warning"],
    }
    normalized = unicodedata.normalize("NFKC", original)
    # Preserve even ambiguous, decimal, out-of-range and verbal tempo requests.
    if re.search(
        r"bpm|テンポ|速度|速さ|速く|遅く|ゆっくり|スロー|高速", normalized, re.IGNORECASE
    ):
        reason = "テンポに関する指定があるため、制作指示を変更しません"
    elif not confirmed["n"]:
        reason = (
            "この分類には確認済みBPMがありません。参考音源DBで確認値を登録してください"
        )
    elif not 60 <= confirmed["median"] <= 180:
        reason = "参考BPMがMIDI生成の対応範囲（60〜180 BPM）外です。自動補正しません"
    else:
        bpm = math.floor(confirmed["median"] + 0.5)
        proposal = original + f"\n{bpm} BPM。"
        if len(proposal) > 4000:
            reason = "BPMを追記すると制作指示の4000文字上限を超えます"
        else:
            result.update(proposed_brief=proposal, suggested_bpm=bpm, changed=True)
            reason = (
                f"確認済みBPM {confirmed['n']}件の中央値 {confirmed['median']:g} を参考に、"
                f"整数の {bpm} BPMを提案します。採用するまで制作指示は変わりません"
            )
    result["reason"] = reason
    return result
