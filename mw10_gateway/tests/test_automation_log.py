"""Plain-assert tests for automation_log.py (stdlib only).

Run: python3 test_automation_log.py
"""

import tempfile
import time
from pathlib import Path

from automation_log import (
    LOG_HEADER,
    AutomationStats,
    update_automation_log,
    update_note_content,
)

FAILURES = []


def check(name: str, cond: bool, extra: object = None) -> None:
    print(f"{'PASS' if cond else 'FAIL'} {name}" + (f" {extra!r}" if extra is not None else ""))
    if not cond:
        FAILURES.append(name)


def stats(**overrides) -> AutomationStats:
    base = {
        "session_id": "s1",
        "track_name": "KIHACHI_Dub_Delay_Send",
        "parameter_name": "Dry/Wet",
        "total_events": 128,
        "duration_beats": 16.0,
        "min_value": 0.1,
        "max_value": 0.85,
    }
    base.update(overrides)
    return AutomationStats(**base)


# 1. New section is appended to a note without one.
note = "# Session MW10 2026-11-09\n\nSome performance notes.\n"
out = update_note_content(note, stats())
check("section appended", LOG_HEADER in out and "| `Dry/Wet` |" in out and "**128 events**" in out)
check("original content kept", out.startswith("# Session MW10 2026-11-09"))

# 2. A second parameter is added as another row; the first row survives.
out2 = update_note_content(out, stats(parameter_name="Frequency", total_events=64))
check("both rows present", "| `Dry/Wet` |" in out2 and "| `Frequency` |" in out2)
check("still one section", out2.count(LOG_HEADER) == 1)

# 3. Re-posting the same parameter replaces its row (latest stats win).
out3 = update_note_content(out2, stats(total_events=200))
check("row replaced, not duplicated", out3.count("| `Dry/Wet` |") == 1 and "**200 events**" in out3)
check("other row untouched", "| `Frequency` |" in out3 and "**64 events**" in out3)

# 4. A following heading is never swallowed by the update.
note_with_next = out + "\n## Next Section\n\nKeep me.\n"
out4 = update_note_content(note_with_next, stats(parameter_name="Gain", total_events=3))
check("next section intact", "## Next Section" in out4 and "Keep me." in out4)
gain_line = next(i for i, line in enumerate(out4.split("\n")) if "`Gain`" in line)
next_line = out4.split("\n").index("## Next Section")
check("new row lands inside the log section", gain_line < next_line)

# 5. A pipe in a track name is escaped and cannot break the table.
out5 = update_note_content(note, stats(track_name="A|B"))
check("pipe escaped", "A\\|B" in out5)

# 6. Validation: min above max is refused.
try:
    update_note_content(note, stats(min_value=0.9, max_value=0.1))
    check("min > max refused", False, "no error")
except ValueError:
    check("min > max refused", True)

# 7. File level: latest note by mtime wins; no notes -> clean error.
with tempfile.TemporaryDirectory() as tmp:
    vault = Path(tmp)
    result = update_automation_log(vault, stats())
    check("no notes -> error result", result["success"] is False and "No MW10 session notes" in result["error"])
    older = vault / "Session_MW10_2026-11-08.md"
    newer = vault / "Session_MW10_2026-11-09.md"
    older.write_text("# old\n", encoding="utf-8")
    time.sleep(0.02)
    newer.write_text("# new\n", encoding="utf-8")
    (vault / "Unrelated.md").write_text("# ignore me\n", encoding="utf-8")
    result = update_automation_log(vault, stats())
    check("latest note updated", result["success"] is True and result["file"] == newer.name)
    check("older note untouched", LOG_HEADER not in older.read_text(encoding="utf-8"))
    check("newer note has the row", "| `Dry/Wet` |" in newer.read_text(encoding="utf-8"))

print("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAILURES: {FAILURES}")
raise SystemExit(0 if not FAILURES else 1)
