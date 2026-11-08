"""Local revisions of one candidate. A revision does not rebuild the whole song."""

from __future__ import annotations

import uuid
from dataclasses import replace
from itertools import pairwise
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

_SCOPES = {
    "bass",
    "syncopation",
    "drums",
    "section",
    "velocity",
    "arrangement",
    "thin",
    "clear",
    "thicken",
}
#: Scopes that act only on the parts a request names.
_PART_SCOPES = frozenset({"thin", "clear", "thicken"})
#: An added note sits on this grid, halfway between two notes at least two steps apart.
_THICKEN_STEP = 0.25
#: The drums a revision thins or softens; the kick carries the groove and stays.
_TOP_DRUMS = frozenset({"Hats", "Snare", "OpenHat", "Perc"})
_SCOPE_LABELS = {
    "bass": "ベース",
    "syncopation": "ベースのシンコペーション",
    "drums": "ドラム",
    "section": "セクション末尾",
    "velocity": "ベロシティ",
    "arrangement": "構成",
    "thin": "音数",
    "clear": "ノート",
    "thicken": "音数",
}
_REQUEST_VERBS = {"thin": "減らしてほしい", "clear": "消してほしい", "thicken": "増やしてほしい"}


def revise_candidate(
    candidate: MidiCandidate,
    scopes: list[str] | None = None,
    issue_ids: list[str] | None = None,
    bars: tuple[int, int] | None = None,
    parts: list[str] | None = None,
) -> dict[str, Any]:
    """Change only the requested notes and return a child candidate.

    With no matching review issue, explicit ``scopes`` still count as the
    user's own request. ``bars`` limits every change to that inclusive range.
    ``thin`` and ``clear`` act only on ``parts``.
    """
    review = review_candidate(candidate)
    if scopes and _PART_SCOPES & set(scopes) and not parts:
        return {"ok": False, "error": "減らす・消すパートを指定してください。", "review": review}
    selected = [item for item in review["issues"] if _selected(item, scopes, issue_ids)]
    if not selected and scopes and not issue_ids:
        selected = [_user_request(scope, bars, parts) for scope in scopes if scope in _SCOPES]
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
    named = frozenset(parts or ())
    clips = tuple(_rewrite_clip(clip, used, beats, bars, kicks, named) for clip in candidate.clips)
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
        "after": _after_lines(selected, bars, parts),
        "scopes": sorted(used),
        "bars": list(bars) if bars else None,
        "parent_candidate_id": candidate.candidate_id,
        "candidate": child,
        "review": after,
        "musical_quality_claimed": False,
    }


def _user_request(
    scope: str, bars: tuple[int, int] | None, parts: list[str] | None = None
) -> dict[str, Any]:
    where = f"{bars[0]}–{bars[1]} 小節" if bars else "曲全体"
    named = f"{'・'.join(parts)}の" if parts and scope in _PART_SCOPES else ""
    verb = _REQUEST_VERBS.get(scope, "変えてほしい")
    return {
        "id": f"request-{scope}",
        "category": scope,
        "severity": "request",
        "description": f"{where}の{named}{_SCOPE_LABELS[scope]}を{verb}（手動の依頼）",
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
    parts: frozenset[str] = frozenset(),
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
        if clip.part in parts and ("clear" in scopes or ("thin" in scopes and index % 2 == 1)):
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
        start_beats = note.start_beats
        if (
            "syncopation" in scopes
            and clip.part == "Bass"
            and index % 2 == 1
            and abs(start_beats - round(start_beats)) < 1e-6
        ):
            shifted = start_beats + 0.5
            if _can_shift_bass_note(clip, index, shifted, beats, kicks or {}):
                start_beats = shifted
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
            replace(note, pitch=pitch, velocity=velocity, start_beats=start_beats)
            if isinstance(note, MidiNote)
            else MidiNote(pitch, start_beats, note.duration_beats, velocity)
        )
    if clip.part in parts and "thicken" in scopes:
        notes = _thicken(notes, clip.start_bar, beats, bars)
    if notes == list(clip.notes):
        return clip
    return CandidateClip(
        part=clip.part,
        section_name=clip.section_name,
        start_bar=clip.start_bar,
        length_bars=clip.length_bars,
        notes=tuple(notes),
    )


def _thicken(
    notes: list[MidiNote], start_bar: int, beats: float, bars: tuple[int, int] | None
) -> list[MidiNote]:
    """Add a softer copy halfway between two notes, only where nothing would overlap."""
    ordered = sorted(notes, key=lambda note: note.start_beats)
    added: list[MidiNote] = []
    for left, right in pairwise(ordered):
        middle = (left.start_beats + right.start_beats) / 2
        if right.start_beats - left.start_beats < 2 * _THICKEN_STEP - 1e-9:
            continue
        if abs(middle / _THICKEN_STEP - round(middle / _THICKEN_STEP)) > 1e-6:
            continue
        if left.start_beats + left.duration_beats > middle + 1e-9:
            continue
        bar = start_bar + int(middle // beats)
        if bars is not None and not bars[0] <= bar <= bars[1]:
            continue
        added.append(
            MidiNote(
                left.pitch,
                middle,
                min(left.duration_beats, _THICKEN_STEP / 2),
                max(1, round(left.velocity * 0.75)),
            )
        )
    return sorted(ordered + added, key=lambda note: note.start_beats)


def _can_shift_bass_note(
    clip: CandidateClip,
    note_index: int,
    shifted_start: float,
    beats_per_bar: float,
    kicks: dict[int, set[float]],
) -> bool:
    """Move a note only when it stays in the clip and avoids kick/onset clashes."""
    if shifted_start >= clip.length_bars * beats_per_bar:
        return False
    bar = clip.start_bar + int(shifted_start // beats_per_bar)
    local_beat = shifted_start % beats_per_bar
    if on_kick(kicks, bar, local_beat):
        return False
    for index, other in enumerate(clip.notes):
        if index == note_index:
            continue
        if abs(other.start_beats - shifted_start) <= 0.02:
            return False
        if other.start_beats < shifted_start < other.start_beats + other.duration_beats:
            return False
    return True


def _after_lines(
    issues: list[dict[str, Any]],
    bars: tuple[int, int] | None = None,
    parts: list[str] | None = None,
) -> list[str]:
    where = f"{bars[0]}–{bars[1]} 小節の" if bars else ""
    named = "・".join(parts or ())
    lines = []
    for issue in issues:
        if issue["scope"] == "thin":
            lines.append(f"{where}{named}の音を1つおきに抜き、数を減らしました。")
        elif issue["scope"] == "clear":
            lines.append(f"{where}{named}のノートを消しました。")
        elif issue["scope"] == "thicken":
            lines.append(f"{where}{named}の音と音の間に弱めの音を足し、数を増やしました。")
        elif issue["scope"] == "bass":
            target = where or f"{issue['bars']} の後半"
            lines.append(f"{target}ベースにオクターブの変化を入れました。")
        elif issue["scope"] == "syncopation":
            lines.append(f"{where}ベースの一部を8分裏へ移し、キックと重なる位置は避けました。")
        elif issue["scope"] == "drums":
            lines.append(f"{where}ハットの一部のベロシティを下げ、同じ繰り返しを緩めました。")
        elif issue["scope"] == "velocity":
            lines.append(f"{where}キックと同じ拍の低いベースの強さを下げました。")
        elif issue["scope"] == "section":
            lines.append(f"{where}Build/Break 末尾のハットを抜き、次のセクションとの差を作りました。")
        else:
            lines.append(f"{where}指定した範囲だけを変更しました。曲全体は作り直していません。")
    return lines
