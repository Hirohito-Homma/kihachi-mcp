"""Reproducible, read-only audit of generated MIDI patterns (no Live access)."""

from __future__ import annotations

import argparse
import json
from collections import Counter

from kihachi_mcp.services.brief_parser import extract_explicit
from kihachi_mcp.services.midi_candidate_builder import build_candidate
from kihachi_mcp.services.studio_interpreter import assemble_brief

EXAMPLES = {
    "Tech House": "テックハウス、128 BPM、Aマイナー、64小節",
    "Liquid Drum & Bass": "Liquid Drum & Bass、174 BPM、Dマイナー、64小節、歌うようなリード",
    "J-Pop": "J-Pop、120 BPM、Cメジャー、64小節、歌うようなリード",
}
PARTS = ("Kick", "Snare", "Hats", "Bass", "Stab", "Lead", "Pad", "Arp", "Vocal")


def audit(text: str, seed: int) -> dict:
    """Summarize actual generated notes without persisting a project."""
    intent = {
        "genre": "tech_house", "tempo": 120, "bars": 64, "key": "Dm", "mood": "",
        "hats_first_half": "normal", "hats_second_half": "normal",
        "bass_register": "mid", "note_density": "normal", "drop_start_bar": 0,
        "unhandled": [], "ambiguous": [],
    }
    brief = assemble_brief(extract_explicit(text), intent)
    candidate = build_candidate(brief, seed=seed)
    parts = {}
    for part in PARTS:
        clips = candidate.clips_for_part(part)
        notes = [note for clip in clips for note in clip.notes]
        if not notes:
            continue
        onsets = Counter(round(note.start_beats % brief.beats_per_bar, 3) for note in notes)
        parts[part] = {
            "notes": len(notes),
            "pitches": len({note.pitch for note in notes}),
            "onset_slots": len(onsets),
            "top_onsets": onsets.most_common(4),
        }
    return {
        "genre": candidate.brief.genre.value,
        "seed": seed,
        "sections": [
            {"name": section.name, "start_bar": section.start_bar,
             "length_bars": section.length_bars}
            for section in candidate.brief.sections
        ],
        "parts": parts,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, action="append", dest="seeds")
    args = parser.parse_args()
    result = [audit(text, seed) for text in EXAMPLES.values() for seed in (args.seeds or [3, 4, 5])]
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
