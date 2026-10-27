"""Compare two generated Drops by their actual MIDI notes, without Live access."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from kihachi_mcp.services.midi_candidate_builder import build_candidate
from kihachi_mcp.services.production_review import review_candidate
from kihachi_mcp.services.production_workspace import project_view
from kihachi_mcp.services.studio_interpreter import interpret_brief_offline


def _notes(candidate, section, part: str) -> list[tuple[float, int, float, int]]:
    beats = candidate.brief.beats_per_bar
    return sorted(
        (
            round((clip.start_bar - section.start_bar) * beats + note.start_beats, 3),
            note.pitch,
            round(note.duration_beats, 3),
            note.velocity,
        )
        for clip in candidate.clips_for_part(part)
        if section.start_bar <= clip.start_bar <= section.end_bar
        for note in clip.notes
    )


def audit(brief_text: str, seed: int) -> dict:
    brief = interpret_brief_offline(brief_text)
    candidate = build_candidate(brief, seed=seed)
    drops = [section for section in brief.sections if section.name == "Drop"]
    if len(drops) != 2 or drops[0].length_bars != drops[1].length_bars:
        raise ValueError("The brief must produce two equally long Drops")
    project = project_view(candidate)
    parts = []
    for part in candidate.parts:
        first, second = (_notes(candidate, section, part) for section in drops)
        if not first and not second:
            continue
        parts.append(
            {
                "part": part,
                "first_notes": len(first),
                "second_notes": len(second),
                "identical": first == second,
                "onsets_identical": [row[0] for row in first] == [row[0] for row in second],
                "pitches_identical": [row[1] for row in first] == [row[1] for row in second],
                "velocities_identical": [row[3] for row in first] == [row[3] for row in second],
            }
        )
    return {
        "brief": brief_text,
        "seed": seed,
        "tempo": brief.tempo.value,
        "key": brief.key.value,
        "bars": brief.bars.value,
        "total_notes": candidate.note_count(),
        "section_notes_total": sum(section["notes"] for section in project["arrangement"]["sections"]),
        "sections": [
            {key: section[key] for key in ("name", "start_bar", "end_bar", "notes", "notes_per_bar")}
            for section in project["arrangement"]["sections"]
        ],
        "drop_parts": parts,
        "review_issues": review_candidate(candidate)["issues"],
        "musical_quality_claimed": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--brief", default="124 BPM、D# minor、128小節")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output", type=Path, help="Write a new JSON file; never overwrite")
    args = parser.parse_args()
    result = json.dumps(audit(args.brief, args.seed), ensure_ascii=False, indent=2) + "\n"
    if args.output:
        with args.output.open("x", encoding="utf-8") as handle:
            handle.write(result)
        print(args.output)
    else:
        print(result, end="")


if __name__ == "__main__":
    main()
