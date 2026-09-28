"""Local revisions of one candidate. A revision does not rebuild the whole song."""

from __future__ import annotations

import uuid
from dataclasses import replace
from typing import Any

from kihachi_mcp.models.midi_candidate import CandidateClip, MidiCandidate
from kihachi_mcp.services.production_review import (
    DUCKED_BASS_VELOCITY,
    LOW_BASS_MAX_PITCH,
    kick_positions,
    on_kick,
    review_candidate,
)
from kihachi_mcp.services.session_pattern_builder import MidiNote

_SCOPES = {"bass", "drums", "section", "velocity", "arrangement"}
#: The drums a revision thins or softens; the kick carries the groove and stays.
_TOP_DRUMS = frozenset({"Hats", "Snare", "OpenHat", "Perc"})
_SCOPE_LABELS = {
    "bass": "ベース",
    "drums": "ドラム",
    "section": "セクション末尾",
    "velocity": "ベロシティ",
    "arrangement": "構成",
}


def revise_candidate(
    candidate: MidiCandidate,
    scopes: list[str] | None = None,
    issue_ids: list[str] | None = None,
    bars: tuple[int, int] | None = None,
) -> dict[str, Any]:
    """Change only the requested notes and return a child candidate.

    With no matching review issue, explicit ``scopes`` still count as the
    user's own request. ``bars`` limits every change to that inclusive range.
    """
    review = review_candidate(candidate)
    selected = [item for item in review["issues"] if _selected(item, scopes, issue_ids)]
    if not selected and scopes and not issue_ids:
        selected = [_user_request(scope, bars) for scope in scopes if scope in _SCOPES]
    if not selected:
        return {
            "ok": False,
            "error": "修正対象がありません。指摘を選ぶか、範囲を指定してください。",
            "review": review,
        }
    if bars is not None and (bars[0] < 1 or bars[1] < bars[0]):
        return {"ok": False, "error": "小節の範囲が正しくありません", "review": review}
    used = {item["scope"] for item in selected}
    beats = candidate.brief.beats_per_bar
    kicks = kick_positions(candidate, beats)
    clips = tuple(_rewrite_clip(clip, used, beats, bars, kicks) for clip in candidate.clips)
    if clips == candidate.clips:
        return {
            "ok": False,
            "error": "指定した範囲では変更できるノートがありませんでした。範囲を広げてください。",
            "review": review,
        }
    child = MidiCandidate(
        candidate_id=uuid.uuid4().hex,
        seed=candidate.seed,
        brief=candidate.brief,
        clips=clips,
        parent_candidate_id=candidate.candidate_id,
    )
    after = review_candidate(child)
    descriptions = [item["description"] for item in selected]
    return {
        "ok": True,
        "before": descriptions,
        "after": _after_lines(selected, bars),
        "scopes": sorted(used),
        "bars": list(bars) if bars else None,
        "parent_candidate_id": candidate.candidate_id,
        "candidate": child,
        "review": after,
        "musical_quality_claimed": False,
    }


def _user_request(scope: str, bars: tuple[int, int] | None) -> dict[str, Any]:
    where = f"{bars[0]}–{bars[1]} 小節" if bars else "曲全体"
    return {
        "id": f"request-{scope}",
        "category": scope,
        "severity": "request",
        "description": f"{where}の{_SCOPE_LABELS[scope]}を変えてほしい（手動の依頼）",
        "bars": f"{bars[0]}–{bars[1]}" if bars else "",
        "recommendation": "",
        "scope": scope,
    }


def _selected(issue: dict[str, Any], scopes: list[str] | None, issue_ids: list[str] | None) -> bool:
    if issue_ids:
        return issue["id"] in set(issue_ids)
    if scopes:
        allowed = {scope for scope in scopes if scope in _SCOPES}
        return issue["scope"] in allowed
    return True


def _rewrite_clip(
    clip: CandidateClip,
    scopes: set[str],
    beats: float,
    bars: tuple[int, int] | None = None,
    kicks: dict[int, set[float]] | None = None,
) -> CandidateClip:
    notes = []
    for index, note in enumerate(clip.notes):
        pitch = note.pitch
        velocity = note.velocity
        bar_in_clip = int(note.start_beats // beats)
        absolute_bar = clip.start_bar + bar_in_clip
        if bars is not None and not bars[0] <= absolute_bar <= bars[1]:
            notes.append(note)
            continue
        # A bar range already says where; without one, vary the clip's second half.
        late = bars is not None or bar_in_clip >= clip.length_bars // 2
        if clip.part == "Bass" and "bass" in scopes and late and index % 2 == 1 and pitch + 12 <= 72:
            pitch += 12
        if clip.part in _TOP_DRUMS and "drums" in scopes and index % 4 == 0:
            velocity = max(1, velocity - 12)
        if (
            "velocity" in scopes
            and clip.part == "Bass"
            and pitch <= LOW_BASS_MAX_PITCH
            and on_kick(kicks or {}, absolute_bar, note.start_beats % beats)
        ):
            velocity = min(velocity, DUCKED_BASS_VELOCITY)
        if (
            "section" in scopes
            and clip.section_name in {"Build", "Break"}
            and clip.part in _TOP_DRUMS
            and bar_in_clip >= clip.length_bars - 2
        ):
            continue
        if "arrangement" in scopes and clip.part == "Lead" and clip.section_name == "Intro":
            continue
        notes.append(
            replace(note, pitch=pitch, velocity=velocity)
            if isinstance(note, MidiNote)
            else MidiNote(pitch, note.start_beats, note.duration_beats, velocity)
        )
    if notes == list(clip.notes):
        return clip
    return CandidateClip(
        part=clip.part,
        section_name=clip.section_name,
        start_bar=clip.start_bar,
        length_bars=clip.length_bars,
        notes=tuple(notes),
    )


def _after_lines(issues: list[dict[str, Any]], bars: tuple[int, int] | None = None) -> list[str]:
    where = f"{bars[0]}–{bars[1]} 小節の" if bars else ""
    lines = []
    for issue in issues:
        if issue["scope"] == "bass":
            target = where or f"{issue['bars']} の後半"
            lines.append(f"{target}ベースにオクターブの変化を入れました。")
        elif issue["scope"] == "drums":
            lines.append(f"{where}ハットの一部のベロシティを下げ、同じ繰り返しを緩めました。")
        elif issue["scope"] == "velocity":
            lines.append(f"{where}キックと同じ拍の低いベースの強さを下げました。")
        elif issue["scope"] == "section":
            lines.append(f"{where}Build/Break 末尾のハットを抜き、次のセクションとの差を作りました。")
        else:
            lines.append(f"{where}指定した範囲だけを変更しました。曲全体は作り直していません。")
    return lines
