"""Read one Session MIDI clip, compute objective facts, and build a non-destructive preview.

Only ``get_midi_clip_notes`` is used for reading. No Live writes, playback, saving,
or exports are performed. Timbre and perceived audio quality are never claimed from MIDI.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any

from kihachi_mcp.gateway.live_tools import get_midi_clip_notes
from kihachi_mcp.gateway.song_time import BEATS_PER_BAR, MAX_NOTES_PER_CLIP
from kihachi_mcp.services.jarvis.context import UNKNOWN

ClipReader = Callable[..., dict[str, Any]]


def resolve_clip_target(
    *,
    track_index: int | None = None,
    track_name: str | None = None,
    clip_index: int | None = None,
    ambiguous: bool = False,
    parse_error: str | None = None,
) -> dict[str, Any]:
    """Validate a user-specified track and Session clip target without guessing."""
    if parse_error:
        return {"ok": False, "error": parse_error}
    if ambiguous:
        return {
            "ok": False,
            "error": "トラックまたはクリップの指定が曖昧・重複しています。1つのトラックと1つのクリップを指定してください。Liveは変更していません。",
        }
    if track_index is not None and track_name is not None:
        return {
            "ok": False,
            "error": "track_index と track_name は片方だけ指定してください。Liveは変更していません。",
        }
    if track_index is None and track_name is None:
        return {
            "ok": False,
            "error": "読み取るトラック（トラック番号またはトラック名）を指定してください。推測では選びません。Liveは変更していません。",
        }
    cleaned_name: str | None = None
    if track_index is not None:
        if isinstance(track_index, bool) or not isinstance(track_index, int) or track_index < 0:
            return {
                "ok": False,
                "error": "track_index は 0 以上の整数で指定してください。Liveは変更していません。",
            }
    else:
        if (
            not isinstance(track_name, str)
            or not track_name.strip()
            or len(track_name.strip()) > 80
            or "/" in track_name
            or "\\" in track_name
        ):
            return {
                "ok": False,
                "error": "track_name が不正です。Liveは変更していません。",
            }
        cleaned_name = track_name.strip()
    if clip_index is None or isinstance(clip_index, bool) or not isinstance(clip_index, int) or clip_index < 0:
        return {
            "ok": False,
            "error": "読み取る Session クリップ番号（clip_index、0以上の整数）を指定してください。推測では選びません。Liveは変更していません。",
        }
    return {
        "ok": True,
        "track_index": track_index,
        "track_name": cleaned_name,
        "clip_index": clip_index,
    }


def read_session_midi_clip(
    *,
    track_index: int | None = None,
    track_name: str | None = None,
    clip_index: int | None = None,
    reader: ClipReader | None = None,
) -> dict[str, Any]:
    """Read one Session MIDI clip via ``get_midi_clip_notes`` and enforce completeness."""
    target = resolve_clip_target(
        track_index=track_index,
        track_name=track_name,
        clip_index=clip_index,
    )
    if not target["ok"]:
        return {
            "ok": False,
            "complete": False,
            "status": "UNKNOWN",
            "error": target["error"],
            "live_changed": False,
        }
    call_reader = reader or get_midi_clip_notes
    try:
        raw = call_reader(
            track_index=target["track_index"],
            track_name=target["track_name"],
            clip_index=target["clip_index"],
        )
    except Exception:  # noqa: BLE001 - reader failures never become success
        return {
            "ok": False,
            "complete": False,
            "status": "FAILED",
            "error": "MIDI クリップの読み取り中に失敗しました。評価と改訂案の作成を中止しました。Liveは変更していません。",
            "live_changed": False,
        }
    if not isinstance(raw, dict):
        return {
            "ok": False,
            "complete": False,
            "status": "FAILED",
            "error": "MIDI クリップの読み取り結果が不正です。評価と改訂案の作成を中止しました。Liveは変更していません。",
            "live_changed": False,
        }
    if raw.get("live_changed") is True:
        return {
            "ok": False,
            "complete": False,
            "status": "FAILED",
            "error": "Live を変更した応答は採用しません。成功にはしていません。",
            "live_changed": False,
        }
    notes = raw.get("notes")
    note_count = raw.get("note_count")
    length_beats = raw.get("length_beats")
    truncated = raw.get("truncated")
    complete_flag = raw.get("complete")
    status = str(raw.get("status") or ("SUCCESS" if raw.get("ok") else "FAILED"))

    if truncated is True or status == "PARTIAL" or (complete_flag is False and raw.get("error") is None and isinstance(notes, list)):
        return {
            "ok": False,
            "complete": False,
            "status": "PARTIAL",
            "error": "MIDI クリップのノートが切り詰められているか不完全（complete=false）なため、評価と改訂案の作成を中止しました。Liveは変更していません。",
            "live_changed": False,
        }
    if raw.get("ok") is not True or complete_flag is not True or status != "SUCCESS":
        return {
            "ok": False,
            "complete": False,
            "status": status if status in {"PARTIAL", "FAILED", "rejected"} else "FAILED",
            "error": _explain_read_failure(raw),
            "raw_error": raw.get("error") or raw.get("message"),
            "live_changed": False,
        }
    if (
        not isinstance(notes, list)
        or len(notes) > MAX_NOTES_PER_CLIP
        or isinstance(note_count, bool)
        or not isinstance(note_count, int)
        or note_count < 0
        or note_count != len(notes)
        or isinstance(length_beats, bool)
        or not isinstance(length_beats, (int, float))
        or not math.isfinite(length_beats)
        or length_beats <= 0
        or truncated is not False
    ):
        return {
            "ok": False,
            "complete": False,
            "status": "FAILED",
            "error": "MIDI クリップの読み取り結果が不完全または形式不正です。評価と改訂案の作成を中止しました。Liveは変更していません。",
            "live_changed": False,
        }
    clip_length = float(length_beats)
    validated_notes: list[dict[str, Any]] = []
    for note in notes:
        checked = _validate_clip_note(note, clip_length)
        if checked is None:
            return {
                "ok": False,
                "complete": False,
                "status": "FAILED",
                "error": "MIDI クリップに形式不正のノートが含まれているため、評価と改訂案の作成を中止しました。Liveは変更していません。",
                "live_changed": False,
            }
        validated_notes.append(checked)
    raw_track_index = raw.get("track_index")
    raw_clip_index = raw.get("clip_index")
    raw_clip_name = raw.get("clip")
    raw_track_name = raw.get("track") if raw.get("track") is not None else raw.get("track_name")
    if (
        isinstance(raw_track_index, bool)
        or not isinstance(raw_track_index, int)
        or raw_track_index < 0
        or isinstance(raw_clip_index, bool)
        or not isinstance(raw_clip_index, int)
        or raw_clip_index < 0
        or not isinstance(raw_clip_name, str)
        or (raw_track_name is not None and (not isinstance(raw_track_name, str) or not raw_track_name.strip()))
        or (target["track_name"] is not None and raw_track_name is None)
    ):
        return _identity_failure(
            "MIDI クリップの識別情報（track_index / clip_index / clip / トラック名）が欠落または不正なため、"
            "別クリップのデータとして扱わず中止しました。Liveは変更していません。"
        )
    if (
        raw_clip_index != target["clip_index"]
        or (target["track_index"] is not None and raw_track_index != target["track_index"])
        or (target["track_name"] is not None and raw_track_name.strip() != target["track_name"])
    ):
        return _identity_failure(
            "読み取ったクリップの識別情報が要求したトラック/クリップと一致しないため、"
            "別クリップのデータとして扱わず中止しました。Liveは変更していません。"
        )
    resolved_track_index = raw_track_index
    resolved_track_name = raw_track_name.strip() if isinstance(raw_track_name, str) else UNKNOWN
    resolved_clip_name = raw_clip_name if raw_clip_name.strip() else UNKNOWN
    tempo = raw.get("tempo")
    if isinstance(tempo, bool) or not isinstance(tempo, (int, float)) or not math.isfinite(tempo) or tempo <= 0:
        tempo = UNKNOWN
    else:
        tempo = float(tempo)
    signature = raw.get("time_signature")
    if (
        isinstance(signature, (list, tuple))
        and len(signature) == 2
        and all(not isinstance(x, bool) and isinstance(x, int) and x > 0 for x in signature)
    ):
        time_signature: list[int] | str = [int(signature[0]), int(signature[1])]
    else:
        time_signature = UNKNOWN
    return {
        "ok": True,
        "complete": True,
        "status": "SUCCESS",
        "track_index": resolved_track_index,
        "track_name": resolved_track_name,
        "clip_index": target["clip_index"],
        "clip_name": resolved_clip_name,
        "length_beats": clip_length,
        "tempo": tempo,
        "time_signature": time_signature,
        "note_count": len(validated_notes),
        "truncated": False,
        "notes": validated_notes,
        "live_changed": False,
    }


def evaluate_midi_clip(
    clip: dict[str, Any],
    *,
    scope: str | None = None,
    bar_range: dict[str, int] | tuple[int, int] | None = None,
) -> dict[str, Any]:
    """Compute objective facts from a verified Session MIDI clip. Never claims timbre or audio quality."""
    notes: list[dict[str, Any]] = [dict(n) for n in clip.get("notes") or []]
    length_beats = float(clip["length_beats"])
    beats_per_bar = _beats_per_bar(clip.get("time_signature"))
    bar_count = max(1, math.ceil(length_beats / beats_per_bar - 1e-9))
    exact_bars = length_beats / beats_per_bar
    bars: int | float = bar_count if abs(exact_bars - bar_count) < 1e-6 else round(exact_bars, 2)
    bar_notes = _bar_note_counts(notes, bar_count, beats_per_bar)
    note_count = len(notes)
    notes_per_bar = round(note_count / bar_count, 2)
    empty_bar_count = sum(1 for count in bar_notes if count == 0)

    if note_count == 0:
        lowest_pitch = None
        highest_pitch = None
        pitch_span = 0
        pitch_range = None
        unique_pitches: list[int] = []
        average_duration = None
        min_duration = None
        max_duration = None
        average_velocity = None
        velocity_range = None
        offbeat_count = 0
        offbeat_ratio = 0.0
        ghost_count = 0
    else:
        pitches = [int(n["pitch"]) for n in notes]
        durations = [float(n["duration"]) for n in notes]
        velocities = [int(n["velocity"]) for n in notes]
        starts = [float(n["start_time"]) for n in notes]
        lowest_pitch = min(pitches)
        highest_pitch = max(pitches)
        pitch_span = highest_pitch - lowest_pitch
        pitch_range = {"min": lowest_pitch, "max": highest_pitch, "span": pitch_span}
        unique_pitches = sorted(set(pitches))
        average_duration = round(sum(durations) / note_count, 4)
        min_duration = round(min(durations), 4)
        max_duration = round(max(durations), 4)
        average_velocity = round(sum(velocities) / note_count, 2)
        velocity_range = {
            "min": min(velocities),
            "max": max(velocities),
            "average": average_velocity,
        }
        offbeat_count = sum(1 for s in starts if abs(s - round(s)) > 1e-4)
        offbeat_ratio = round(offbeat_count / note_count, 4)
        ghost_count = sum(1 for v in velocities if v <= 55)

    selected_bars = _selected_bars_stats(bar_notes, bar_count, scope=scope, bar_range=bar_range)
    observations = _build_observations(
        note_count=note_count,
        bar_count=bar_count,
        notes_per_bar=notes_per_bar,
        empty_bar_count=empty_bar_count,
        pitch_range=pitch_range,
        average_duration=average_duration,
        velocity_range=velocity_range,
        selected_bars=selected_bars,
    )
    return {
        "track_index": clip.get("track_index", UNKNOWN),
        "track_name": clip.get("track_name", UNKNOWN),
        "clip_index": clip["clip_index"],
        "clip_name": clip.get("clip_name", UNKNOWN),
        "tempo": clip.get("tempo", UNKNOWN),
        "time_signature": clip.get("time_signature", UNKNOWN),
        "key": UNKNOWN,
        "timbre": UNKNOWN,
        "perceived_audio_quality": UNKNOWN,
        "musical_quality_claimed": False,
        "complete": True,
        "length_beats": length_beats,
        "bars": bars,
        "bar_count": bar_count,
        "note_count": note_count,
        "bar_notes": bar_notes,
        "notes_per_bar": notes_per_bar,
        "empty_bar_count": empty_bar_count,
        "pitch_range": pitch_range,
        "lowest_pitch": lowest_pitch,
        "highest_pitch": highest_pitch,
        "pitch_span": pitch_span,
        "unique_pitches": unique_pitches,
        "unique_pitch_count": len(unique_pitches),
        "average_duration_beats": average_duration,
        "min_duration_beats": min_duration,
        "max_duration_beats": max_duration,
        "velocity_range": velocity_range,
        "average_velocity": average_velocity,
        "offbeat_note_count": offbeat_count,
        "offbeat_ratio": offbeat_ratio,
        "ghost_note_count": ghost_count,
        "selected_bars": selected_bars,
        "observations": observations,
        "live_changed": False,
    }


def preview_midi_clip_revision(
    clip: dict[str, Any],
    evaluation: dict[str, Any],
    *,
    direction: str | None = None,
    scope: str | None = None,
    bar_range: dict[str, int] | tuple[int, int] | None = None,
) -> dict[str, Any]:
    """Build a non-destructive revision preview preserving ``original_notes`` intact."""
    original_notes = [_copy_note(n) for n in clip.get("notes") or []]
    length_beats = float(clip["length_beats"])
    beats_per_bar = _beats_per_bar(clip.get("time_signature"))
    bar_count = int(evaluation["bar_count"])
    bar_notes_before = list(evaluation["bar_notes"])

    resolved_span = _resolve_bar_span(bar_count, scope=scope, bar_range=bar_range)
    action = direction or _default_preview_action(evaluation)

    requested_bars = _requested_bars(bar_range)
    if resolved_span is None:
        preview_notes = [_copy_note(n) for n in original_notes]
        where = (
            f"{requested_bars['start']}–{requested_bars['end']}小節"
            if requested_bars is not None
            else "指定された小節範囲"
        )
        summary = (
            f"{where}はクリップ（{bar_count}小節）の範囲外です。範囲を切り詰めず、"
            "ノートを変更しないプレビューを返しました。Liveは変更していません。"
        )
        target_bars = None
    else:
        first_bar, last_bar = resolved_span
        target_bars = {"start": first_bar, "end": last_bar}
        start_beat = (first_bar - 1) * beats_per_bar
        end_beat = min(length_beats, last_bar * beats_per_bar)
        preview_notes, summary = _transform_notes_for_preview(
            original_notes,
            action=action,
            start_beat=start_beat,
            end_beat=end_beat,
            length_beats=length_beats,
            first_bar=first_bar,
            last_bar=last_bar,
        )

    bar_notes_after = _bar_note_counts(preview_notes, bar_count, beats_per_bar)
    return {
        "preview_only": True,
        "action": action,
        "requested_bars": requested_bars,
        "out_of_range": resolved_span is None,
        "target_bars": target_bars,
        "summary": summary,
        "original_notes": original_notes,
        "preview_notes": preview_notes,
        "note_count_before": len(original_notes),
        "note_count_after": len(preview_notes),
        "bar_notes_before": bar_notes_before,
        "bar_notes_after": bar_notes_after,
        "changed": preview_notes != original_notes,
        "applied_to_live": False,
        "live_changed": False,
        "saved": False,
        "played": False,
        "exported": False,
        "musical_quality_claimed": False,
    }


def format_clip_evaluation_reply(
    evaluation: dict[str, Any],
    preview: dict[str, Any] | None = None,
    *,
    write_requested: bool = False,
) -> str:
    """Build a concise Japanese summary of objective MIDI clip facts and preview."""
    track_desc = (
        f"トラック「{evaluation['track_name']}」"
        if evaluation.get("track_name") not in (None, UNKNOWN)
        else f"トラック{evaluation.get('track_index', UNKNOWN)}"
    )
    clip_idx = evaluation["clip_index"]
    clip_name = evaluation.get("clip_name")
    clip_desc = f"クリップ{clip_idx}" + (f"（{clip_name}）" if clip_name not in (None, UNKNOWN) else "")
    note_count = evaluation["note_count"]
    bars = evaluation["bars"]
    length_beats = evaluation["length_beats"]
    parts = [
        f"{track_desc}の{clip_desc}を読み取りました（{bars}小節 / {length_beats:g}拍、ノート数{note_count}、1小節あたり{evaluation['notes_per_bar']:.1f}音）。"
    ]
    if evaluation["pitch_range"] is not None:
        pr = evaluation["pitch_range"]
        vr = evaluation["velocity_range"]
        parts.append(
            f"音域はMIDI {pr['min']}–{pr['max']}（幅{pr['span']}）、平均ノート長は{evaluation['average_duration_beats']:.2f}拍、"
            f"ベロシティは{vr['min']}–{vr['max']}（平均{vr['average']:.1f}）、小節別ノート数は{evaluation['bar_notes']}です。"
        )
    selected = evaluation.get("selected_bars")
    if isinstance(selected, dict):
        if selected.get("out_of_range"):
            parts.append(f"{selected['start_bar']}–{selected['end_bar']}小節はクリップ（{ evaluation['bar_count']}小節）の範囲外です。")
        else:
            line = f"{selected['start_bar']}–{selected['end_bar']}小節は1小節あたり{selected['notes_per_bar']:.1f}音"
            if selected.get("outside_notes_per_bar") is not None:
                line += f"、それ以外は{selected['outside_notes_per_bar']:.1f}音"
            parts.append(line + "です。")
    if preview is not None:
        parts.append(
            f"改訂プレビュー（元データ保持）: ノート数 {preview['note_count_before']} → {preview['note_count_after']}。"
        )
    parts.append("音色や聴感品質はMIDIからは断定しません（UNKNOWN）。Liveは変更していません。")
    if write_requested:
        parts.append("Liveへの書き戻しは行いません（プレビューのみ・要承認）。")
    return "".join(parts)


def _requested_bars(bar_range: dict[str, int] | tuple[int, int] | None) -> dict[str, int] | None:
    if isinstance(bar_range, dict) and "start" in bar_range and "end" in bar_range:
        return {"start": int(bar_range["start"]), "end": int(bar_range["end"])}
    if isinstance(bar_range, tuple) and len(bar_range) == 2:
        return {"start": int(bar_range[0]), "end": int(bar_range[1])}
    return None


def _identity_failure(message: str) -> dict[str, Any]:
    return {
        "ok": False,
        "complete": False,
        "status": "FAILED",
        "error": message,
        "live_changed": False,
    }


def _explain_read_failure(raw: dict[str, Any]) -> str:
    detail = str(raw.get("error") or raw.get("message") or "").strip()
    lowered = detail.casefold()
    if "not a midi track" in lowered or "does not contain a midi clip" in lowered or "audio" in lowered:
        return "指定されたスロットに Session MIDI クリップがありません（空スロットまたは非MIDI）。推測せず中止しました。Liveは変更していません。"
    if "一意に見つかりません" in detail or "unique" in lowered:
        return "指定されたトラック名が一意に見つかりません（未存在または同名重複）。Liveは変更していません。"
    if "片方だけ" in detail or "必要です" in detail or "不正です" in detail or "整数です" in detail:
        return f"{detail}。Liveは変更していません。"
    if "out of range" in lowered:
        return "指定されたトラック番号またはクリップ番号が範囲外です。Liveは変更していません。"
    if "不完全" in detail or "不正なノート" in detail:
        return f"{detail}。評価と改訂案の作成を中止しました。Liveは変更していません。"
    return "MIDI クリップを完全に読み取れなかったため、評価と改訂案の作成を中止しました。Liveは変更していません。"


def _validate_clip_note(note: Any, clip_length: float) -> dict[str, Any] | None:
    if not isinstance(note, dict):
        return None
    pitch = note.get("pitch")
    start = note.get("start_time")
    duration = note.get("duration")
    velocity = note.get("velocity")
    prob = note.get("probability")
    if (
        isinstance(pitch, bool)
        or not isinstance(pitch, int)
        or not 0 <= pitch <= 127
        or isinstance(start, bool)
        or not isinstance(start, (int, float))
        or not math.isfinite(start)
        or start < 0
        or start >= clip_length
        or isinstance(duration, bool)
        or not isinstance(duration, (int, float))
        or not math.isfinite(duration)
        or duration <= 0
        or isinstance(velocity, bool)
        or not isinstance(velocity, int)
        or not 1 <= velocity <= 127
    ):
        return None
    cleaned: dict[str, Any] = {
        "pitch": int(pitch),
        "start_time": float(start),
        "duration": float(duration),
        "velocity": int(velocity),
    }
    if prob is not None:
        if isinstance(prob, bool) or not isinstance(prob, (int, float)) or not math.isfinite(prob) or not 0.0 <= float(prob) <= 1.0:
            return None
        cleaned["probability"] = float(prob)
    return cleaned


def _beats_per_bar(time_signature: Any) -> float:
    if (
        isinstance(time_signature, (list, tuple))
        and len(time_signature) == 2
        and all(not isinstance(x, bool) and isinstance(x, int) and x > 0 for x in time_signature)
    ):
        return float(time_signature[0]) * (4.0 / float(time_signature[1]))
    return float(BEATS_PER_BAR)


def _bar_note_counts(notes: list[dict[str, Any]], bar_count: int, beats_per_bar: float) -> list[int]:
    counts = [0] * bar_count
    for note in notes:
        bar_idx = int(float(note["start_time"]) // beats_per_bar)
        if 0 <= bar_idx < bar_count:
            counts[bar_idx] += 1
        elif bar_idx >= bar_count > 0:
            counts[-1] += 1
    return counts


def _resolve_bar_span(
    bar_count: int,
    *,
    scope: str | None = None,
    bar_range: dict[str, int] | tuple[int, int] | None = None,
) -> tuple[int, int] | None:
    if isinstance(bar_range, dict) and "start" in bar_range and "end" in bar_range:
        first, last = int(bar_range["start"]), int(bar_range["end"])
        if first < 1 or first > last or last > bar_count:
            return None
        return first, last
    if isinstance(bar_range, tuple) and len(bar_range) == 2:
        first, last = int(bar_range[0]), int(bar_range[1])
        if first < 1 or first > last or last > bar_count:
            return None
        return first, last
    if scope == "later_section":
        if bar_count >= 2:
            return bar_count // 2 + 1, bar_count
        return 1, 1
    if scope == "earlier_section":
        if bar_count >= 2:
            return 1, bar_count // 2
        return 1, 1
    return 1, bar_count


def _selected_bars_stats(
    bar_notes: list[int],
    bar_count: int,
    *,
    scope: str | None = None,
    bar_range: dict[str, int] | tuple[int, int] | None = None,
) -> dict[str, Any] | None:
    if scope is None and bar_range is None:
        return None
    if isinstance(bar_range, dict):
        raw_first, raw_last = int(bar_range["start"]), int(bar_range["end"])
    elif isinstance(bar_range, tuple) and len(bar_range) == 2:
        raw_first, raw_last = int(bar_range[0]), int(bar_range[1])
    else:
        resolved = _resolve_bar_span(bar_count, scope=scope, bar_range=None)
        if resolved is None:
            return None
        raw_first, raw_last = resolved
    if raw_first < 1 or raw_first > raw_last or raw_last > bar_count:
        return {
            "start_bar": raw_first,
            "end_bar": raw_last,
            "out_of_range": True,
            "note_count": 0,
            "notes_per_bar": 0.0,
            "outside_notes_per_bar": None,
        }
    first = raw_first
    last = raw_last
    inside = sum(bar_notes[first - 1 : last])
    span_len = last - first + 1
    rest_len = bar_count - span_len
    outside = sum(bar_notes) - inside
    return {
        "start_bar": first,
        "end_bar": last,
        "out_of_range": False,
        "note_count": inside,
        "notes_per_bar": round(inside / span_len, 2) if span_len > 0 else 0.0,
        "outside_notes_per_bar": round(outside / rest_len, 2) if rest_len > 0 else None,
    }


def _build_observations(
    *,
    note_count: int,
    bar_count: int,
    notes_per_bar: float,
    empty_bar_count: int,
    pitch_range: dict[str, int] | None,
    average_duration: float | None,
    velocity_range: dict[str, Any] | None,
    selected_bars: dict[str, Any] | None,
) -> list[str]:
    items: list[str] = []
    if note_count == 0:
        items.append("ノート数が 0 件の空クリップです。")
    else:
        items.append(f"全{bar_count}小節に計{note_count}音（1小節あたり平均{notes_per_bar:.2f}音）あります。")
        if pitch_range is not None:
            items.append(
                f"ピッチ範囲は MIDI {pitch_range['min']}〜{pitch_range['max']}（音域幅 {pitch_range['span']} 半音）、"
                f"平均ノート長は {average_duration:.3f} 拍です。"
            )
        if velocity_range is not None:
            span = int(velocity_range["max"]) - int(velocity_range["min"])
            if span <= 5 and note_count >= 4:
                items.append(
                    f"ベロシティは {velocity_range['min']}〜{velocity_range['max']}（平均 {velocity_range['average']:.1f}）で強弱幅が小さめです。"
                )
            else:
                items.append(
                    f"ベロシティは {velocity_range['min']}〜{velocity_range['max']}（平均 {velocity_range['average']:.1f}）です。"
                )
        if empty_bar_count > 0:
            items.append(f"{bar_count}小節中{empty_bar_count}小節はノートがありません。")
    if isinstance(selected_bars, dict) and not selected_bars.get("out_of_range"):
        items.append(
            f"指定範囲（{selected_bars['start_bar']}〜{selected_bars['end_bar']}小節）は計{selected_bars['note_count']}音（1小節あたり{selected_bars['notes_per_bar']:.2f}音）です。"
        )
    items.append("MIDIノートから計算した事実のみです。音色（UNKNOWN）や実際の聴感品質（UNKNOWN）は断定していません。")
    return items


def _default_preview_action(evaluation: dict[str, Any]) -> str:
    note_count = int(evaluation.get("note_count") or 0)
    if note_count == 0:
        return "none"
    if float(evaluation.get("notes_per_bar") or 0.0) > 6.0 and note_count >= 4:
        return "reduce_density"
    vr = evaluation.get("velocity_range")
    if isinstance(vr, dict) and int(vr["max"]) - int(vr["min"]) <= 5 and note_count >= 2:
        return "velocity_contrast"
    if note_count >= 2:
        return "reduce_density"
    return "velocity_contrast"


def _transform_notes_for_preview(
    original_notes: list[dict[str, Any]],
    *,
    action: str,
    start_beat: float,
    end_beat: float,
    length_beats: float,
    first_bar: int,
    last_bar: int,
) -> tuple[list[dict[str, Any]], str]:
    ordered = sorted(
        (_copy_note(n) for n in original_notes),
        key=lambda item: (float(item["start_time"]), int(item["pitch"])),
    )
    if action == "none" or not ordered:
        return ordered, "ノート数が0のため変更案はありません（元データをそのまま保持）。Liveは変更していません。"

    inside: list[dict[str, Any]] = []
    outside: list[dict[str, Any]] = []
    for note in ordered:
        st = float(note["start_time"])
        if start_beat - 1e-9 <= st < end_beat - 1e-9:
            inside.append(note)
        else:
            outside.append(note)

    if action in {"delete", "clear"}:
        kept = outside
        return (
            kept,
            f"{first_bar}–{last_bar}小節のノートを消去するプレビューです（{len(ordered)} → {len(kept)}音、元データは保持）。Liveは変更していません。",
        )

    if action in {"reduce_density", "thin"}:
        thinned = [note for idx, note in enumerate(inside) if idx % 2 == 0]
        combined = sorted(outside + thinned, key=lambda item: (float(item["start_time"]), int(item["pitch"])))
        return (
            combined,
            f"{first_bar}–{last_bar}小節のノート密度を間引くプレビューです（{len(ordered)} → {len(combined)}音、元データは保持）。Liveは変更していません。",
        )

    if action in {"increase_density", "thicken"}:
        added: list[dict[str, Any]] = []
        limit = MAX_NOTES_PER_CLIP - len(ordered)
        for idx, note in enumerate(inside):
            if len(added) >= limit:
                break
            st = float(note["start_time"])
            dur = float(note["duration"])
            next_start = float(inside[idx + 1]["start_time"]) if idx + 1 < len(inside) else end_beat
            gap = next_start - st
            step = 0.5 if gap >= 0.5 else (gap / 2.0 if gap >= 0.25 else 0.0)
            new_start = round(st + step, 4)
            if step > 0 and start_beat <= new_start < min(end_beat, length_beats) - 1e-4:
                new_Note = _copy_note(note)
                new_Note["start_time"] = new_start
                new_Note["duration"] = round(min(dur, max(0.125, next_start - new_start)), 4)
                new_Note["velocity"] = max(1, min(127, round(int(note["velocity"]) * 0.78)))
                added.append(new_Note)
        combined = sorted(
            outside + inside + added,
            key=lambda item: (float(item["start_time"]), int(item["pitch"])),
        )
        return (
            combined,
            f"{first_bar}–{last_bar}小節に補助ノートを加えるプレビューです（{len(ordered)} → {len(combined)}音、元データは保持）。Liveは変更していません。",
        )

    # velocity_contrast: accentuate downbeats slightly and soften offbeats non-destructively
    contrasted: list[dict[str, Any]] = []
    for idx, note in enumerate(inside):
        updated = _copy_note(note)
        st = float(updated["start_time"])
        vel = int(updated["velocity"])
        on_beat = abs(st - round(st)) <= 1e-4
        delta = 6 if (on_beat and idx % 2 == 0) else -8
        if max(1, min(127, vel + delta)) == vel:
            delta = -6 if vel > 10 else 6
        updated["velocity"] = max(1, min(127, vel + delta))
        contrasted.append(updated)
    combined = sorted(outside + contrasted, key=lambda item: (float(item["start_time"]), int(item["pitch"])))
    return (
        combined,
        f"{first_bar}–{last_bar}小節のベロシティ強弱を調整するプレビューです（{len(ordered)}音、元データは保持）。Liveは変更していません。",
    )


def _copy_note(note: dict[str, Any]) -> dict[str, Any]:
    copied: dict[str, Any] = {
        "pitch": int(note["pitch"]),
        "start_time": float(note["start_time"]),
        "duration": float(note["duration"]),
        "velocity": int(note["velocity"]),
    }
    if "probability" in note and note["probability"] is not None:
        copied["probability"] = float(note["probability"])
    return copied
