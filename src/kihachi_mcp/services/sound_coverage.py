"""Detect missing instruments and Drum Rack pads for used MIDI pitches."""

from typing import Any

from kihachi_mcp.models.midi_candidate import MidiCandidate
from kihachi_mcp.models.production_brief import STUDIO_PARTS
from kihachi_mcp.services import live_device_catalog

DRUM_PARTS = frozenset({"Kick", "Hats"})


def expected_pitches(candidate: MidiCandidate) -> dict[str, list[int]]:
    """Return the pitches each part will send to Live."""
    return {part: list(candidate.used_pitches(part)) for part in STUDIO_PARTS}


def coverage_from_snapshot(
    candidate: MidiCandidate,
    track_names: dict[str, str],
    device_names_by_track: dict[str, list[str]],
    drum_summaries: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Report automatic vs manual sound work. Never claim unverified pads."""
    issues: list[dict[str, Any]] = []
    automatic: list[str] = []
    manual: list[str] = []
    summaries = drum_summaries or {}
    for part in STUDIO_PARTS:
        track_name = track_names.get(part, "")
        devices = device_names_by_track.get(part, [])
        pitches = candidate.used_pitches(part)
        suggested = live_device_catalog.suggest_instrument(part)
        if not devices:
            issues.append(
                {
                    "part": part,
                    "track_name": track_name,
                    "kind": "no_instrument",
                    "detail": f"{part} に音源がありません",
                    "pitches": list(pitches),
                    "verified": True,
                }
            )
            manual.append(f"{part}: Liveで {suggested} などを手動で載せてください")
            continue
        automatic.append(f"{part}: デバイス {', '.join(devices)} を確認しました")
        if part not in DRUM_PARTS:
            continue
        summary = summaries.get(part)
        if summary is None:
            issues.append(
                {
                    "part": part,
                    "track_name": track_name,
                    "kind": "pad_unverified",
                    "detail": (
                        f"{part} は {list(pitches)} を使いますが、"
                        "Drum Rackのパッド割当は今回確認できていません"
                    ),
                    "pitches": list(pitches),
                    "verified": False,
                }
            )
            manual.append(
                f"{part}: 使用ノート {list(pitches)} にサンプルを割り当ててください"
            )
            continue
        occupied = {
            int(pad.get("note"))
            for device in summary.get("devices") or []
            for pad in device.get("occupied_pads") or []
            if pad.get("note") is not None
        }
        missing = [pitch for pitch in pitches if pitch not in occupied]
        if missing:
            issues.append(
                {
                    "part": part,
                    "track_name": track_name,
                    "kind": "missing_pads",
                    "detail": (
                        f"{part} のノート {missing} に Drum Rack の音源がありません"
                    ),
                    "pitches": missing,
                    "verified": True,
                }
            )
            manual.append(f"{part}: ノート {missing} にサンプルを入れてください")
        elif not occupied:
            issues.append(
                {
                    "part": part,
                    "track_name": track_name,
                    "kind": "empty_drum_rack",
                    "detail": f"{part} の Drum Rack にサンプルがありません",
                    "pitches": list(pitches),
                    "verified": True,
                }
            )
            manual.append(f"{part}: 空の Drum Rack です。サンプルを入れてください")
    return {
        "expected_pitches": expected_pitches(candidate),
        "issues": issues,
        "automatic": automatic,
        "manual": manual,
        "has_missing_sounds": any(
            issue["kind"] in {"no_instrument", "missing_pads", "empty_drum_rack"}
            for issue in issues
        ),
    }
