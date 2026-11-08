"""Plain-assert tests for pitch_range.py, including a full round-trip:
stats written by automation_log's writer are read back by pitch_range.

Run: python3 test_pitch_range.py
"""

import tempfile
from pathlib import Path

from automation_log import MidiStats, parse_note_name, update_midi_log
from pitch_range import (
    composer_range_state,
    extract_pitch_range,
    parse_distribution_rows,
)

FAILURES = []


def check(name: str, cond: bool, extra: object = None) -> None:
    print(f"{'PASS' if cond else 'FAIL'} {name}" + (f" {extra!r}" if extra is not None else ""))
    if not cond:
        FAILURES.append(name)


# Parser: one convention everywhere (Live: C3 = 60).
check("C3 -> 60", parse_note_name("C3") == 60)
check("C-2 -> 0", parse_note_name("C-2") == 0)
check("G8 -> 127", parse_note_name("G8") == 127)
check("G#3 -> 68", parse_note_name("G#3") == 68)
check("Eb3 -> 63", parse_note_name("Eb3") == 63)
check("Bb-1 -> 22", parse_note_name("Bb-1") == 22)
check("garbage -> None", parse_note_name("H3") is None and parse_note_name("C9") is None)


def write_session(vault: Path, distribution: dict[str, int], track: str = "KIHACHI_Slap_Bass") -> None:
    (vault / "Session_MW10_2026-11-09.md").write_text("# Session\n", encoding="utf-8")
    total = sum(distribution.values())
    update_midi_log(
        vault,
        MidiStats(
            session_id="s1",
            track_name=track,
            total_notes=total,
            avg_velocity=90.0,
            max_velocity=100,
            min_velocity=80,
            note_distribution=distribution,
        ),
    )


# Round-trip: the draft's example distribution -> range 60..68, margin +-2.
with tempfile.TemporaryDirectory() as tmp:
    vault = Path(tmp)
    write_session(vault, {"C3": 12, "D3": 8, "G#3": 5})
    content = (vault / "Session_MW10_2026-11-09.md").read_text(encoding="utf-8")
    merged = parse_distribution_rows(content)
    check("rows parsed back", merged == {60: 12, 62: 8, 68: 5}, merged)
    result = extract_pitch_range(vault)
    check("range = 58..70 (window 60..68, margin 2)", (result.min_pitch, result.max_pitch) == (58, 70), result)
    check("source marked as distribution", result.source == "Obsidian_Pitch_Distribution" and result.total_hits == 25)

# Outlier trimming: 200 hits around C3, one stray hit at C6 (96).
# At 98% coverage the stray is outside the window; range stays near C3.
with tempfile.TemporaryDirectory() as tmp:
    vault = Path(tmp)
    write_session(vault, {"C3": 150, "D3": 50, "C6": 1})
    result = extract_pitch_range(vault)
    check("stray top hit trimmed", result.max_pitch == 64, result)  # D3=62 + margin 2
    result_full = extract_pitch_range(vault, coverage=1.0)
    check("coverage 1.0 keeps the stray", result_full.max_pitch == 96, result_full)  # 96 clamped by ceiling

# Guards: margin cannot push past floor/ceiling.
with tempfile.TemporaryDirectory() as tmp:
    vault = Path(tmp)
    write_session(vault, {"C-1": 10, "C7": 10})
    result = extract_pitch_range(vault, coverage=1.0)
    check("floor/ceiling clamp", (result.min_pitch, result.max_pitch) == (24, 96), result)

# No data -> documented default, honestly sourced.
with tempfile.TemporaryDirectory() as tmp:
    result = extract_pitch_range(Path(tmp))
    check("empty vault -> default 36..84", (result.min_pitch, result.max_pitch) == (36, 84) and result.source == "default", result)

# Rows outside distribution subsections are not picked up.
content = "# Note\n\n| **C7** | 999 hits | `x` |\n"
check("lookalike rows outside subsections ignored", parse_distribution_rows(content) == {})

# The state adapter keeps the draft's shape.
with tempfile.TemporaryDirectory() as tmp:
    vault = Path(tmp)
    write_session(vault, {"C3": 12, "D3": 8, "G#3": 5})
    state = composer_range_state({"obsidian_vault_path": tmp})
    gen = state["midi_generation_range"]
    check(
        "adapter sets step + range",
        state["step"] == "midi_range_optimized" and gen["min_pitch"] == 58 and gen["max_pitch"] == 70 and gen["source"] == "Obsidian_Pitch_Distribution",
        gen,
    )

print("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAILURES: {FAILURES}")
raise SystemExit(0 if not FAILURES else 1)
