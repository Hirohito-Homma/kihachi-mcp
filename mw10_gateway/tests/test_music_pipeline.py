"""Plain-assert tests for music_pipeline.py.

Run: python3 test_music_pipeline.py
Also writes the final automation cue to /tmp/pipeline_cue.json so the
cross-language check (TS translator) can consume exactly what the
pipeline emitted.
"""

import json
import tempfile
from pathlib import Path

from automation_log import MidiStats, update_midi_log
from music_pipeline import VERIFIED_MONITORING_PATH, run_music_pipeline

FAILURES = []


def check(name: str, cond: bool, extra: object = None) -> None:
    print(f"{'PASS' if cond else 'FAIL'} {name}" + (f" {extra!r}" if extra is not None else ""))
    if not cond:
        FAILURES.append(name)


with tempfile.TemporaryDirectory() as tmp:
    vault = Path(tmp)
    (vault / "Session_MW10_2026-11-09.md").write_text("# Session\n", encoding="utf-8")
    update_midi_log(
        vault,
        MidiStats(
            session_id="session_mw10_2026_1109",
            track_name="KIHACHI_Slap_Bass",
            total_notes=25,
            avg_velocity=92.8,
            max_velocity=100,
            min_velocity=80,
            note_distribution={"C3": 12, "D3": 8, "G#3": 5},
        ),
    )
    final = run_music_pipeline(
        {
            "session_id": "session_mw10_2026_1109",
            "obsidian_vault_path": tmp,
            "step": "idle",
            "progress": 0,
            "midi_generation_range": {},
            "mixer_instructions": {},
        }
    )
    check("ends at mixer_completed / 100", final["step"] == "mixer_completed" and final["progress"] == 100)
    gen = final["midi_generation_range"]
    check("composer range from the note", gen == {"min_pitch": 58, "max_pitch": 70, "source": "Obsidian_Pitch_Distribution"}, gen)
    mixer = final["mixer_instructions"]
    check("monitoring path is the verified order", mixer["monitoring_path"] == VERIFIED_MONITORING_PATH, mixer["monitoring_path"])
    cue = mixer["automation_cue"]
    check(
        "cue is DubDelayCue-shaped",
        cue["target_track"] == "KIHACHI_Dub_Delay_Send"
        and cue["start_beat"] == 32.0
        and cue["end_beat"] == 48.0
        and cue["beats_per_bar"] == 4
        and cue["min_val"] == 0.3
        and cue["max_val"] == 0.85,
        cue,
    )
    Path("/tmp/pipeline_cue.json").write_text(json.dumps(cue), encoding="utf-8")

with tempfile.TemporaryDirectory() as tmp:
    final = run_music_pipeline({"session_id": "s-empty", "obsidian_vault_path": tmp})
    gen = final["midi_generation_range"]
    check("empty vault: default range, honestly sourced", gen["source"] == "default" and (gen["min_pitch"], gen["max_pitch"]) == (36, 84), gen)
    check("pipeline still completes", final["progress"] == 100)

print("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAILURES: {FAILURES}")
raise SystemExit(0 if not FAILURES else 1)
