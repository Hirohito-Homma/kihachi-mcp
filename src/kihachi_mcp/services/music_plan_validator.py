"""Check a MusicPlan against its scope and instrument limits.

This module does not talk to Ableton Live.
"""

from __future__ import annotations

from dataclasses import dataclass

from kihachi_mcp.models.instrument_spec import InstrumentSpec
from kihachi_mcp.models.music_plan import MUSIC_PLAN_SCHEMA, MusicNote, MusicPlan
from kihachi_mcp.services.musical_time import beats_per_bar

SCHEMA_INVALID = "SCHEMA_INVALID"
MUSIC_VALIDATION_FAILED = "MUSIC_VALIDATION_FAILED"
SCOPE_VIOLATION = "SCOPE_VIOLATION"


@dataclass(frozen=True)
class PlanIssue:
    code: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    issues: tuple[PlanIssue, ...]

    def to_dict(self) -> dict[str, object]:
        return {"ok": self.ok, "issues": [issue.to_dict() for issue in self.issues]}


def validate_music_plan(
    plan: MusicPlan,
    instruments: dict[str, InstrumentSpec] | None = None,
) -> ValidationResult:
    """Return every problem. An empty issue list is the only success."""
    issues: list[PlanIssue] = []
    if plan.schema != MUSIC_PLAN_SCHEMA:
        issues.append(PlanIssue(SCHEMA_INVALID, "MusicPlan の schema が違います。"))
    if plan.tempo <= 0 or plan.bars < 1 or plan.numerator < 1 or plan.denominator < 1:
        issues.append(PlanIssue(SCHEMA_INVALID, "テンポ、小節数、拍子が不正です。"))
    scope = plan.scope
    if scope.start_bar < 1 or scope.length_bars < 1:
        issues.append(PlanIssue(SCHEMA_INVALID, "生成範囲の小節が不正です。"))
    allowed = set(scope.tracks)
    protected = set(scope.preserve)
    if allowed & protected:
        issues.append(PlanIssue(SCOPE_VIOLATION, "残すパートと作るパートが重なっています。"))
    try:
        meter = beats_per_bar(plan.numerator, plan.denominator)
    except ValueError:
        meter = 0
        issues.append(PlanIssue(SCHEMA_INVALID, "拍子から拍数を計算できません。"))
    scope_end = scope.start_bar + scope.length_bars - 1
    for track in plan.tracks:
        if track.id in protected:
            issues.append(PlanIssue(SCOPE_VIOLATION, f"{track.id} は残す指定なので変更できません。"))
        if track.id not in allowed:
            issues.append(PlanIssue(SCOPE_VIOLATION, f"{track.id} は今回の生成範囲にありません。"))
        if track.type != "midi":
            issues.append(PlanIssue(SCHEMA_INVALID, f"{track.id} は MIDI ではありません。"))
        instrument = (instruments or {}).get(track.id)
        for clip in track.clips:
            clip_end = clip.start_bar + clip.length_bars - 1
            if clip.start_bar < scope.start_bar or clip_end > scope_end:
                issues.append(PlanIssue(SCOPE_VIOLATION, f"{track.id} のクリップが指定小節の外です。"))
            if meter:
                _validate_notes(issues, track.id, clip.notes, clip.length_bars * meter, instrument)
    return ValidationResult(ok=not issues, issues=tuple(issues))


def _validate_notes(
    issues: list[PlanIssue],
    track_id: str,
    notes: tuple[MusicNote, ...],
    clip_beats: float,
    instrument: InstrumentSpec | None,
) -> None:
    ordered = tuple(sorted(notes, key=lambda note: (note.start, note.pitch)))
    previous = None
    for note in ordered:
        if note.duration <= 0 or note.start < 0 or note.start + note.duration > clip_beats + 1e-6:
            issues.append(PlanIssue(MUSIC_VALIDATION_FAILED, f"{track_id} のノートがクリップの外です。"))
        if not 0 <= note.pitch <= 127 or not 1 <= note.velocity <= 127:
            issues.append(PlanIssue(MUSIC_VALIDATION_FAILED, f"{track_id} の音程か強さが MIDI の範囲外です。"))
        if instrument is not None and not instrument.pitch_range.contains(note.pitch):
            issues.append(PlanIssue(MUSIC_VALIDATION_FAILED, f"{track_id} の音程 {note.pitch} は楽器の音域外です。"))
        if (
            instrument is not None
            and previous is not None
            and note.start > previous.start + 1e-6
            and abs(note.pitch - previous.pitch) > instrument.max_leap
        ):
            issues.append(PlanIssue(MUSIC_VALIDATION_FAILED, f"{track_id} の音程の飛びが大きすぎます。"))
        previous = note
    if instrument is None:
        return
    active_notes: list[MusicNote] = []
    for note in ordered:
        # A note finishes before note.start if note.start >= end.
        # Boundary tolerance (1e-6) allows abutting notes with float inaccuracies to pass,
        # but is restricted to cases where note.start > prior.start and the overlap is strictly
        # less than half the prior note's duration, preventing micro-notes from premature zeroing.
        active_notes = [
            prior for prior in active_notes
            if not (
                note.start >= prior.start + prior.duration
                or (
                    note.start > prior.start
                    and (prior.start + prior.duration - note.start) <= 1e-6
                    and (prior.start + prior.duration - note.start) < prior.duration * 0.5
                )
            )
        ]
        active_notes.append(note)
        if len(active_notes) > instrument.max_polyphony:
            issues.append(PlanIssue(MUSIC_VALIDATION_FAILED, f"{track_id} の同時発音が上限を超えています。"))
            return
