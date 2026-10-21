"""Place candidates E/F (144 bars) into AB_Comparison as two 96-bar clips each.

Usage: uv run python scratch/candidate_eval/place_candidates_ef.py [--execute]
Without --execute only the preconditions are checked; Live is not changed.
"""

import json
import sys
import time
from pathlib import Path

from kihachi_mcp.gateway.drum_architecture import (
    duplicate_arrangement_clip,
    get_arrangement_midi_notes,
    replace_arrangement_midi_notes,
)
from kihachi_mcp.gateway.live_tools import ableton_get_arrangement, ableton_list_tracks
from kihachi_mcp.gateway.transport import ableton_get_playback_state

SRC = Path(__file__).with_name("source_candidates")
CANDIDATES = {
    "E": ("E", 401),
    "F": ("F", 601),
}
PARTS = ["Kick", "Snare", "Hats", "OpenHat", "Perc", "Sub", "Bass", "Stab", "Pad", "Arp", "Vocal", "FX"]
TRACKS = {part: index for index, part in enumerate(PARTS, start=2)}
CLIP_BEATS = 384.0
SOURCE_SPAN = (400.0, 784.0)
EXISTING = [(0.0, 384.0), (400.0, 784.0), (800.0, 1184.0), (1200.0, 1584.0)]
OUT = Path(__file__).with_name("live_placement_ef_verified.json")


def load_halves(candidate_name: str) -> dict[str, list[list[dict]]]:
    data = json.loads((SRC / f"{candidate_name}.json").read_text(encoding="utf-8"))
    halves = {part: [[], []] for part in PARTS}
    for clip in data["clips"]:
        for note in clip["notes"]:
            beat = (clip["start_bar"] - 1) * 4.0 + float(note.get("start", note.get("start_time")))
            duration = float(note.get("duration", note.get("duration_beats")))
            half = 0 if beat < CLIP_BEATS else 1
            if half == 0 and beat + duration > CLIP_BEATS + 1e-6:
                raise SystemExit(f"{candidate_name} {clip['part']}: note crosses bar 97")
            halves[clip["part"]][half].append(
                {
                    "pitch": int(note["pitch"]),
                    "start_time": round(beat - half * CLIP_BEATS, 4),
                    "duration": round(duration, 4),
                    "velocity": int(note["velocity"]),
                }
            )
    return halves


def destinations() -> list[tuple[str, int, float]]:
    spans = []
    for label, (_cid, start_bar) in CANDIDATES.items():
        base = (start_bar - 1) * 4.0
        spans += [(label, 0, base), (label, 1, base + CLIP_BEATS)]
    return spans


def preconditions() -> list[str]:
    problems = []
    if ableton_get_playback_state().get("is_playing"):
        problems.append("Live is playing")
    names = {t.get("index"): t.get("name") for t in ableton_list_tracks().get("tracks", [])}
    lowest = min(dest for _l, _h, dest in destinations())
    for part, index in TRACKS.items():
        if names.get(index) != part:
            problems.append(f"track {index} is {names.get(index)!r}, expected {part}")
            continue
        spans = [(c["start_time"], c["end_time"]) for c in ableton_get_arrangement(track_index=index).get("clips", [])]
        if spans != EXISTING:
            problems.append(f"{part}: unexpected clips {spans}")
        if any(end > lowest for _s, end in spans):
            problems.append(f"{part}: destination not empty")
    return problems


def main() -> None:
    execute = "--execute" in sys.argv
    notes = {label: load_halves(name) for label, (name, _bar) in CANDIDATES.items()}
    for label in notes:
        counts = {p: [len(h[0]), len(h[1])] for p, h in notes[label].items()}
        print(label, counts)
    problems = preconditions()
    if problems:
        print("PRECONDITION FAILED:", *problems, sep="\n  ")
        raise SystemExit(1)
    print("preconditions OK")
    if not execute:
        return

    for part, index in TRACKS.items():
        for label, half, dest in destinations():
            res = duplicate_arrangement_clip(index, *SOURCE_SPAN, dest, confirm=True)
            if not res.get("ok"):
                raise SystemExit(f"duplicate failed {part} {label}{half} @ {dest}: {res}")
            payload = notes[label][part][half]
            res = replace_arrangement_midi_notes(
                index, dest, dest + CLIP_BEATS, payload, confirm=True, allow_clear=not payload
            )
            if not res.get("ok"):
                raise SystemExit(f"replace failed {part} {label}{half} @ {dest}: {res}")
            time.sleep(0.05)
        print(f"placed {part}")

    report = {}
    for part, index in TRACKS.items():
        row = {"clips": [(c["start_time"], c["end_time"]) for c in ableton_get_arrangement(track_index=index)["clips"]]}
        for label, half, dest in destinations():
            live = get_arrangement_midi_notes(index, dest, dest + CLIP_BEATS).get("notes") or []
            row[f"{label}{half + 1}"] = len(live)
            if half == 0:
                row[f"{label}_bars73_76"] = sum(1 for n in live if 288.0 <= n["start_time"] < 304.0)
            if len(live) != len(notes[label][part][half]):
                row["mismatch"] = True
        report[part] = row
        print(part, {k: v for k, v in row.items() if k != "clips"})
    OUT.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"saved {OUT}")


if __name__ == "__main__":
    main()
