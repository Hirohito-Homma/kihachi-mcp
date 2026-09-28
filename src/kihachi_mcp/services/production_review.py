"""Concrete musical review of a MIDI candidate. Issues name bars and a fix."""

from __future__ import annotations

from typing import Any

from kihachi_mcp.knowledge.genre_profiles import profile_for
from kihachi_mcp.models.midi_candidate import MidiCandidate
from kihachi_mcp.services.brief_parser import explicit_swing

_DIMENSIONS = (
    "structure",
    "groove",
    "bass",
    "drums",
    "harmony",
    "energy",
    "repetition",
    "variation",
    "playability",
    "production_readiness",
)


def review_candidate(candidate: MidiCandidate) -> dict[str, Any]:
    """Review the notes that would be sent, not only the SongSpec metadata."""
    beats = candidate.brief.beats_per_bar
    issues: list[dict[str, Any]] = []
    bass_run = _repeated_run(candidate, "Bass", beats)
    if bass_run is not None and bass_run[1] - bass_run[0] + 1 >= 16:
        start, end = bass_run
        issues.append(
            _issue(
                "bass-repeat",
                "bass",
                "high",
                f"ベースのパターンが {start}–{end} 小節で変わっていません。",
                f"{start}–{end}",
                "後半の小節だけオクターブかリズムを変えてください。",
                "bass",
            )
        )
    drop = _drop_contrast(candidate)
    if drop is not None:
        issues.append(drop)
    collision = _kick_bass_collision(candidate, beats)
    if collision is not None:
        issues.append(collision)
    if candidate.note_count() < 8:
        issues.append(
            _issue(
                "thin-arrangement",
                "production_readiness",
                "high",
                "ノートが少なすぎて、Liveで確認できる曲になっていません。",
                "1",
                "小節数とジャンルを確認して作り直してください。",
                "arrangement",
            )
        )
    dimensions = {name: "pass" for name in _DIMENSIONS}
    for issue in issues:
        dimensions[issue["category"]] = issue["severity"]
    if any(item["category"] == "bass" for item in issues):
        dimensions["repetition"] = "high"
        dimensions["variation"] = "high"
    return {
        "ok": True,
        "candidate_id": candidate.candidate_id,
        "approved": False,
        "issues": issues,
        "dimensions": [
            {"name": name, "status": dimensions[name]} for name in _DIMENSIONS
        ],
        "swing": _swing_of(candidate),
        "musical_quality_claimed": False,
    }


def _issue(
    issue_id: str,
    category: str,
    severity: str,
    description: str,
    bars: str,
    recommendation: str,
    scope: str,
) -> dict[str, Any]:
    return {
        "id": issue_id,
        "category": category,
        "severity": severity,
        "description": description,
        "bars": bars,
        "recommendation": recommendation,
        "scope": scope,
    }


def _bar_patterns(candidate: MidiCandidate, part: str, beats: float) -> dict[int, tuple]:
    patterns: dict[int, tuple] = {}
    for clip in candidate.clips_for_part(part):
        for note in clip.notes:
            bar = clip.start_bar + int(note.start_beats // beats)
            local = round(note.start_beats % beats, 3)
            patterns.setdefault(bar, []).append((note.pitch, local))
    return {bar: tuple(sorted(notes)) for bar, notes in patterns.items()}


def _repeated_run(
    candidate: MidiCandidate, part: str, beats: float
) -> tuple[int, int] | None:
    patterns = _bar_patterns(candidate, part, beats)
    if len(patterns) < 16:
        return None
    bars = sorted(patterns)
    best: tuple[int, int] | None = None
    start = bars[0]
    previous = patterns[bars[0]]
    run_start = start
    for bar in bars[1:]:
        if bar == start + 1 and patterns[bar] == previous and previous:
            start = bar
            continue
        if start - run_start + 1 >= 16 and (best is None or start - run_start > best[1] - best[0]):
            best = (run_start, start)
        run_start = bar
        start = bar
        previous = patterns[bar]
    if start - run_start + 1 >= 16 and (best is None or start - run_start > best[1] - best[0]):
        best = (run_start, start)
    return best


def _drop_contrast(candidate: MidiCandidate) -> dict[str, Any] | None:
    sections = list(candidate.brief.sections)
    names = [section.name for section in sections]
    drop_name = next(
        (name for name in ("Drop", "ChorusA", "ChorusB") if name in names), None
    )
    if drop_name is None:
        return None
    drop = next(section for section in sections if section.name == drop_name)
    index = names.index(drop_name)
    if index == 0:
        return None
    previous = sections[index - 1]
    drop_density = _notes_per_bar(candidate, drop.name)
    previous_density = _notes_per_bar(candidate, previous.name)
    if previous_density <= 0 or drop_density >= previous_density * 1.05:
        return None
    return _issue(
        "drop-contrast",
        "energy",
        "medium",
        f"{drop.name} の音数が {previous.name} より少なく、対比が足りません。",
        f"{drop.start_bar}–{drop.end_bar}",
        "ドロップ直前を薄くし、ドロップのベースとコードを残してください。",
        "section",
    )


def _notes_per_bar(candidate: MidiCandidate, section_name: str) -> float:
    clips = [clip for clip in candidate.clips if clip.section_name == section_name]
    if not clips:
        return 0.0
    notes = sum(len(clip.notes) for clip in clips)
    bars = max(clip.length_bars for clip in clips)
    return notes / max(1, bars)


#: Bass at or below this pitch fights the kick for the same low end.
LOW_BASS_MAX_PITCH = 40
#: A low bass note this soft under a kick is the recommended fix, not a problem.
DUCKED_BASS_VELOCITY = 80


def kick_positions(candidate: MidiCandidate, beats: float) -> dict[int, set[float]]:
    """Beat positions of kick hits, keyed by absolute bar."""
    kicks: dict[int, set[float]] = {}
    for clip in candidate.clips_for_part("Kick"):
        for note in clip.notes:
            if note.pitch not in {35, 36}:
                continue
            bar = clip.start_bar + int(note.start_beats // beats)
            kicks.setdefault(bar, set()).add(round(note.start_beats % beats, 3))
    return kicks


def on_kick(kicks: dict[int, set[float]], bar: int, local_beat: float) -> bool:
    return any(abs(round(local_beat, 3) - kick) <= 0.02 for kick in kicks.get(bar, ()))


def _kick_bass_collision(candidate: MidiCandidate, beats: float) -> dict[str, Any] | None:
    kicks = kick_positions(candidate, beats)
    hit_bars = []
    for clip in candidate.clips_for_part("Bass"):
        for note in clip.notes:
            if note.pitch > LOW_BASS_MAX_PITCH or note.velocity <= DUCKED_BASS_VELOCITY:
                continue
            bar = clip.start_bar + int(note.start_beats // beats)
            if on_kick(kicks, bar, note.start_beats % beats):
                hit_bars.append(bar)
    if len(hit_bars) < 8:
        return None
    start, end = min(hit_bars), max(hit_bars)
    return _issue(
        "kick-bass",
        "groove",
        "medium",
        f"キックと低いベースが {start}–{end} 小節付近で同じ拍に重なっています。",
        f"{start}–{end}",
        "重なるベースのベロシティだけ下げてください。",
        "velocity",
    )


def _swing_of(candidate: MidiCandidate) -> float:
    stated = explicit_swing(candidate.brief.original_text)
    if stated is not None:
        return stated
    return profile_for(str(candidate.brief.genre.value)).swing or 0.5
