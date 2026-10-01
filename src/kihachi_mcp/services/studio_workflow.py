"""Workflow facts shared by the Studio, the `kihachi` CLI and the MCP tools.

Everything here is pure or touches only local files, so it can be tested
without Live or Ollama. The Live read-back compares what Live reports with
what the approved plan said it would create; a successful send is not proof.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

from kihachi_mcp.models.live_contract import (
    OP_CREATE_MIDI_TRACK,
    OP_CREATE_SESSION_CLIP,
    OP_REPLACE_CLIP_NOTES,
    managed_track_name,
)
from kihachi_mcp.models.midi_candidate import MidiCandidate
from kihachi_mcp.services.live_paths import bridge_state_dir

READY = "READY"
WARNING = "WARNING"
OFFLINE = "OFFLINE"

PASS = "PASS"
FAIL = "FAIL"
SKIP = "SKIP"

#: Keys the Studio may change. Anything else in a request is ignored.
EDITABLE_SETTINGS = ("ai_provider", "ollama_url", "ollama_model", "openai_model", "monthly_ai_limit_jpy")
AI_PROVIDERS = ("ollama", "openai", "deterministic")


def system_statuses(
    ollama: dict[str, Any],
    live: dict[str, Any],
    abletongpt: bool | str | None = None,
    selected_candidate_id: str = "",
) -> list[dict[str, str]]:
    """One READY / WARNING / OFFLINE word per component, never colour alone."""
    if ollama.get("ok"):
        ollama_line = (READY, str(ollama.get("message") or ""))
    elif ollama.get("state") == "model_missing":
        ollama_line = (WARNING, str(ollama.get("message") or ""))
    else:
        ollama_line = (OFFLINE, "Ollamaが応答しません。AIなしの既定解釈で続けられます")
    if live.get("state") == "ready":
        live_line = (READY, str(live.get("message") or ""))
    elif live.get("connected"):
        live_line = (WARNING, str(live.get("message") or ""))
    else:
        live_line = (OFFLINE, str(live.get("message") or "Liveに接続できません"))
    if abletongpt is None:
        gpt_line = (WARNING, "未確認")
    elif abletongpt == "no_reply":
        gpt_line = (
            WARNING,
            "Remote Script は接続できますが応答しません（Liveでダイアログが開いていないか確認）",
        )
    elif abletongpt in {True, "ready"}:
        gpt_line = (READY, "Remote Script が応答しています")
    else:
        gpt_line = (
            OFFLINE,
            "Remote Script が応答しません（Control Surface で AbletonGPT_MCP を選ぶと付属キットを使えます）",
        )
    project_line = (
        (READY, selected_candidate_id[:8])
        if selected_candidate_id
        else (WARNING, "まだ候補がありません")
    )
    return [
        {"name": "Ollama", "status": ollama_line[0], "detail": ollama_line[1]},
        {"name": "kihachi-mcp", "status": READY, "detail": "制作サービスが応答しています"},
        {"name": "AbletonGPT", "status": gpt_line[0], "detail": gpt_line[1]},
        {"name": "Ableton Live", "status": live_line[0], "detail": live_line[1]},
        {"name": "Project", "status": project_line[0], "detail": project_line[1]},
    ]


def track_names(candidate: MidiCandidate) -> dict[str, str]:
    """The managed Live track name each part is applied to."""
    short = candidate.candidate_id[:8]
    return {
        part: managed_track_name(f"KIHACHI {part} {short}") for part in candidate.parts
    }


def expected_from_plan(plan: dict[str, Any]) -> dict[str, dict[str, int]]:
    """Per-track clip and note counts an approved apply plan will create."""
    names: dict[int, str] = {}
    for operation in plan.get("operations") or []:
        if operation.get("op") == OP_CREATE_MIDI_TRACK:
            index = int((operation.get("target") or {}).get("track_index", -1))
            names[index] = str((operation.get("arguments") or {}).get("name") or "")
    expected: dict[str, dict[str, int]] = {}
    for operation in plan.get("operations") or []:
        op = operation.get("op")
        if op not in {OP_CREATE_SESSION_CLIP, OP_REPLACE_CLIP_NOTES}:
            continue
        index = int((operation.get("target") or {}).get("track_index", -1))
        name = names.get(index)
        if not name:
            continue
        row = expected.setdefault(name, {"clips": 0, "notes": 0})
        if op == OP_CREATE_SESSION_CLIP:
            row["clips"] += 1
        else:
            row["notes"] += len((operation.get("arguments") or {}).get("notes") or [])
    return expected


def readback_verification(
    candidate: MidiCandidate,
    snapshot: Any,
    expected: dict[str, dict[str, int]] | None,
    tempo_changed: bool,
    arranged: bool,
) -> dict[str, Any]:
    """Compare what Live reports with the plan. PASS only on an exact match."""
    names = track_names(candidate)
    lines: list[dict[str, str]] = []
    tempo = float(candidate.brief.tempo.value)
    if abs(snapshot.tempo - tempo) < 0.01:
        lines.append(_line("Tempo", PASS, f"{snapshot.tempo:g} BPM"))
    elif tempo_changed:
        lines.append(_line("Tempo", FAIL, f"Live {snapshot.tempo:g} BPM / 計画 {tempo:g} BPM"))
    else:
        lines.append(
            _line(
                "Tempo",
                SKIP,
                f"Live {snapshot.tempo:g} BPM（Setのテンポは変えない設定で適用しました。候補は {tempo:g} BPM）",
            )
        )
    tracks = {part: snapshot.track_by_name(name) for part, name in names.items()}
    missing = [names[part] for part, track in tracks.items() if track is None]
    lines.append(
        _line(
            "Tracks",
            FAIL if missing else PASS,
            f"見つからないトラック: {', '.join(missing)}" if missing else f"{len(names)} トラック",
        )
    )
    clip_problems: list[str] = []
    note_problems: list[str] = []
    clip_total = 0
    note_total = 0
    for part, name in names.items():
        track = tracks[part]
        if track is None:
            continue
        clips = [clip for clip in snapshot.session_clips if clip.track_index == track.index]
        clip_total += len(clips)
        notes = sum(clip.note_count for clip in clips)
        note_total += notes
        want = (expected or {}).get(name)
        want_notes = want["notes"] if want else candidate.note_count(part)
        if want is not None and len(clips) != want["clips"]:
            clip_problems.append(f"{part}: Live {len(clips)} / 計画 {want['clips']}")
        if notes != want_notes:
            note_problems.append(f"{part}: Live {notes} / 計画 {want_notes}")
    if expected is None:
        lines.append(_line("Clips", SKIP, f"Live {clip_total} 個（適用時の計画が残っていません）"))
    else:
        lines.append(
            _line(
                "Clips",
                FAIL if clip_problems or missing else PASS,
                "; ".join(clip_problems) or f"{clip_total} 個",
            )
        )
    lines.append(
        _line(
            "Notes",
            FAIL if note_problems or missing else PASS,
            "; ".join(note_problems) or f"{note_total} ノート",
        )
    )
    lines.append(_arrangement_line(candidate, snapshot, tracks, arranged))
    failed = [line for line in lines if line["status"] == FAIL]
    return {
        "ok": not failed,
        "status": "PROJECT READY" if not failed else "VERIFICATION FAILED",
        "lines": lines,
        "set_name": snapshot.set_name,
        "verified_means": "Liveから読み戻した値が計画と一致したこと。音楽的な良し悪しは含みません",
    }


def _arrangement_line(
    candidate: MidiCandidate, snapshot: Any, tracks: dict[str, Any], arranged: bool
) -> dict[str, str]:
    bars = int(candidate.brief.bars.value)
    if not arranged:
        return _line("Arrangement", SKIP, "まだアレンジメントへ展開していません")
    beats = snapshot.time_signature.beats_per_bar
    placed = [
        clip
        for track in tracks.values()
        if track is not None
        for clip in snapshot.arrangement_clips_on(track.index)
    ]
    if not placed:
        return _line("Arrangement", FAIL, "アレンジメントにクリップがありません")
    first = min(clip.start_beats for clip in placed) / beats + 1
    last = max(clip.end_beats for clip in placed) / beats
    outside = first < 1 or last > bars + 0.001
    return _line(
        "Arrangement",
        FAIL if outside else PASS,
        f"{first:g}–{last:g} 小節 / 計画 1–{bars} 小節",
    )


def _line(name: str, status: str, detail: str) -> dict[str, str]:
    return {"name": name, "status": status, "detail": detail}


def list_saved_projects(directory: Path | None, limit: int = 50) -> list[dict[str, Any]]:
    """Saved candidates, newest first, without loading every note into memory."""
    if directory is None or not directory.is_dir():
        return []
    rows = []
    paths = sorted(
        (path for path in directory.glob("*.json") if "." not in path.stem),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for path in paths[:limit]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        brief = data.get("brief") if isinstance(data, dict) else None
        if not isinstance(brief, dict):
            continue
        stem = path.stem
        rows.append(
            {
                "candidate_id": stem,
                "parent_candidate_id": data.get("parent_candidate_id") or "",
                "title": _title(str(brief.get("original_text") or "")),
                "tempo": (brief.get("tempo") or {}).get("value"),
                "key": (brief.get("key") or {}).get("value"),
                "genre": (brief.get("genre") or {}).get("value"),
                "bars": (brief.get("bars") or {}).get("value"),
                "notes": (data.get("note_counts") or {}).get("total"),
                "saved_at": path.stat().st_mtime,
                "approved": (directory / f"{stem}.approved").is_file(),
                "applied": (directory / f"{stem}.applied").is_file(),
                "arranged": (directory / f"{stem}.arranged").is_file(),
            }
        )
    return rows


def _title(text: str) -> str:
    first = next((line.strip() for line in text.splitlines() if line.strip()), "")
    return first[:60] or "(無題)"


def settings_path() -> Path:
    """Studio choices live next to the candidates, outside Git."""
    override = os.environ.get("KIHACHI_SETTINGS_FILE")
    if override:
        return Path(override)
    return Path(str(bridge_state_dir())) / "settings.json"


def load_saved_settings(path: Path | None = None) -> dict[str, Any]:
    """Return only the editable keys from the saved settings file."""
    source = path or settings_path()
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {key: data[key] for key in EDITABLE_SETTINGS if key in data}


def save_settings(values: dict[str, Any], path: Path | None = None) -> None:
    """Write the editable keys atomically. No secrets are stored here."""
    target = path or settings_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {key: values[key] for key in EDITABLE_SETTINGS if key in values}
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, target)


def validate_settings(changes: dict[str, Any], installed_models: list[str]) -> str:
    """Return an error message, or "" when the change is acceptable."""
    provider = changes.get("ai_provider")
    if provider is not None and provider not in AI_PROVIDERS:
        return "AIプロバイダは ollama、openai、deterministic から選んでください"
    openai_model = changes.get("openai_model")
    if openai_model is not None and openai_model not in {"gpt-6.1-sol", "gpt-6-astra"}:
        return "OpenAIモデルは Sol または Astra を選んでください"
    limit = changes.get("monthly_ai_limit_jpy")
    if limit is not None and (not isinstance(limit, int) or not 100 <= limit <= 100_000):
        return "月間上限は100〜100000円の整数で指定してください"
    model = changes.get("ollama_model")
    if model is not None:
        if not isinstance(model, str) or not model.strip():
            return "モデル名が空です"
        if installed_models and model not in installed_models and f"{model}:latest" not in installed_models:
            return "導入済みのモデルから選んでください（KIHACHIはモデルをダウンロードしません）"
    return ""


def revision_record(result: dict[str, Any]) -> dict[str, Any]:
    """A pending revision the user has not accepted or rejected yet."""
    child = result["candidate"]
    return {
        "revision_id": uuid.uuid4().hex[:12],
        "parent_candidate_id": result["parent_candidate_id"],
        "child_candidate_id": child.candidate_id,
        "scopes": result["scopes"],
        "before": result["before"],
        "after": result["after"],
        "review_after": result["review"],
        "created_at": time.time(),
        "decision": "pending",
    }
