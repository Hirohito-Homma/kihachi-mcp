"""Plain-assert tests for the MIDI stats log in automation_log.py.

Run: python3 test_midi_log.py
"""

import tempfile
from pathlib import Path

from automation_log import (
    LOG_HEADER,
    MIDI_LOG_HEADER,
    AutomationStats,
    MidiStats,
    update_automation_log,
    update_midi_log,
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
        "total_notes": 342,
        "avg_velocity": 94.6,
        "max_velocity": 118,
        "min_velocity": 35,
    }
    base.update(overrides)
    return MidiStats(**base)


# 1. New MIDI section appended, draft's row format preserved.
note = "# Session MW10 2026-11-09\n"
out = update_midi_note_content(note, midi())
check("midi section appended", MIDI_LOG_HEADER in out)
check("row format", "| `KIHACHI_Slap_Bass` | **342 notes** | 94.6 | 35 / 118 |" in out)

# 2. A second track gets its own row; re-posting a track replaces its row.
out = update_midi_note_content(out, midi(track_name="KIHACHI_Dub_Delay_Send", total_notes=12))
check("two tracks, two rows", out.count("**342 notes**") == 1 and "**12 notes**" in out)
out = update_midi_note_content(out, midi(total_notes=400))
check("same track replaced", out.count("| `KIHACHI_Slap_Bass` |") == 1 and "**400 notes**" in out)

# 3. Velocity validation: range and consistency.
for name, bad in [
    ("velocity 128 refused", midi(max_velocity=128)),
    ("min > max refused", midi(min_velocity=100, max_velocity=90, avg_velocity=95.0)),
    ("avg outside [min,max] refused", midi(avg_velocity=20.0)),
    ("negative notes refused", midi(total_notes=-1)),
]:
    try:
        bad.validate()
        check(name, False, "no error")
    except ValueError:
        check(name, True)

# 4. The two logs coexist: updating one never touches the other's section.
with tempfile.TemporaryDirectory() as tmp:
    vault = Path(tmp)
    note_file = vault / "Session_MW10_2026-11-09.md"
    note_file.write_text("# Session\n", encoding="utf-8")
    r1 = update_automation_log(
        vault,
        AutomationStats(
            session_id="s1",
            track_name="KIHACHI_Dub_Delay_Send",
            parameter_name="Dry/Wet",
            total_events=128,
            duration_beats=16.0,
            min_value=0.1,
            max_value=0.85,
        ),
    )
    r2 = update_midi_log(vault, midi())
    content = note_file.read_text(encoding="utf-8")
    check("both endpoints succeed", r1["success"] and r2["success"], (r1, r2))
    check("both sections present", LOG_HEADER in content and MIDI_LOG_HEADER in content)
    check("automation row intact", "| `Dry/Wet` |" in content and "**128 events**" in content)
    check("midi row intact", "**342 notes**" in content)
    # Update MIDI again: automation section must be byte-identical region-wise.
    before_auto = content[content.index(LOG_HEADER):content.index(MIDI_LOG_HEADER)]
    update_midi_log(vault, midi(total_notes=401))
    content2 = note_file.read_text(encoding="utf-8")
    after_auto = content2[content2.index(LOG_HEADER):content2.index(MIDI_LOG_HEADER)]
    check("automation section untouched by midi update", before_auto == after_auto)
    check("midi row updated in place", "**401 notes**" in content2)

print("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAILURES: {FAILURES}")
raise SystemExit(0 if not FAILURES else 1)
