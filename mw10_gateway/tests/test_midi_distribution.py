"""Plain-assert tests for the pitch distribution in automation_log.py.

Run: python3 test_midi_distribution.py
"""

from automation_log import (
    MIDI_LOG_HEADER,
    MidiStats,
    distribution_subsection_header,
    update_midi_note_content,
)

FAILURES = []


def check(name: str, cond: bool, extra: object = None) -> None:
    print(f"{'PASS' if cond else 'FAIL'} {name}" + (f" {extra!r}" if extra is not None else ""))
    if not cond:
        FAILURES.append(name)


def midi(**overrides) -> MidiStats:
    base = {
        "session_id": "s1",
        "track_name": "KIHACHI_Slap_Bass",
        "total_notes": 25,
        "avg_velocity": 94.6,
        "max_velocity": 118,
        "min_velocity": 35,
        "note_distribution": {"C3": 12, "D3": 8, "G#3": 5},
    }
    base.update(overrides)
    return MidiStats(**base)


# 1. Distribution subsection is created under the MIDI section, sorted by
# count, with scaled bars (max count fills 20 blocks).
out = update_midi_note_content("# Session\n", midi())
check("subsection created", distribution_subsection_header("KIHACHI_Slap_Bass") in out)
c3 = out.index("**C3**")
d3 = out.index("**D3**")
g3 = out.index("**G#3**")
check("rows sorted by count desc", c3 < d3 < g3)
check("max bar is 20 blocks", "`" + "█" * 20 + "`" in out)
check("8/12 bar is 13 blocks", "`" + "█" * 13 + "`" in out)
check("5/12 bar is 8 blocks", "`" + "█" * 8 + "`" in out)

# 2. A second track's report adds its own subsection; the first survives.
out = update_midi_note_content(
    out,
    midi(track_name="KIHACHI_Lead", total_notes=3, note_distribution={"E4": 3}),
)
check("two subsections coexist", out.count("### 📊 Pitch Distribution") == 2)
check("first distribution intact", "**C3**" in out and "12 hits" in out)
check("two base rows", out.count("**25 notes**") == 1 and "**3 notes**" in out)

# 3. Re-posting the same track replaces its base row AND its subsection,
# leaving the other track's subsection untouched.
lead_block_before = out[out.index(distribution_subsection_header("KIHACHI_Lead")):]
out = update_midi_note_content(out, midi(note_distribution={"C3": 30, "F3": 2}))
check("base row replaced once", out.count("| `KIHACHI_Slap_Bass` |") == 1)
check("old distribution gone", "12 hits" not in out)
check("new distribution present", "30 hits" in out and "**F3**" in out)
check("still one subsection for the track", out.count(distribution_subsection_header("KIHACHI_Slap_Bass")) == 1)
lead_block_after = out[out.index(distribution_subsection_header("KIHACHI_Lead")):]
check("other track's subsection byte-identical", lead_block_before == lead_block_after)

# 4. A report without a distribution leaves the existing one in place.
out = update_midi_note_content(out, midi(note_distribution=None, total_notes=26))
check("stats row updated", "**26 notes**" in out)
check("distribution kept when not reported", "30 hits" in out)

# 5. A following ## section is never absorbed.
out2 = update_midi_note_content(out + "\n## 🎛️ APC40 Live Performance & Automation Log\n\nkeep\n", midi())
check("following section intact", "## 🎛️ APC40 Live Performance & Automation Log" in out2 and "keep" in out2)
check("midi section still before it", out2.index(MIDI_LOG_HEADER) < out2.index("## 🎛️"))

# 6. Validation: non-positive counts refused.
for name, bad in [
    ("zero count refused", midi(note_distribution={"C3": 0})),
    ("negative count refused", midi(note_distribution={"C3": -2})),
    ("empty name refused", midi(note_distribution={"": 3})),
]:
    try:
        bad.validate()
        check(name, False, "no error")
    except ValueError:
        check(name, True)

print("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAILURES: {FAILURES}")
raise SystemExit(0 if not FAILURES else 1)
