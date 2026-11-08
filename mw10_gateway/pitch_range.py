"""Read the pitch range back out of the Obsidian session note.

The reverse direction of automation_log.py: the note's Pitch
Distribution tables (written by /api/midi-log) are parsed, and the
active pitch range is computed for the next generation pass.

Differences from the draft this is based on:

- The draft converted an unparseable note name to a silent default of
  60, which would drag the range toward middle C on any malformed row.
  Here, names go through automation_log.parse_note_name -- the same
  parser the write side sorts with -- and unparseable rows are skipped.
- The draft took raw min/max over every pitch that appeared even once,
  so a single stray hit would stretch the "optimized" range. Here the
  range is the smallest pitch window covering `coverage` (default 98%)
  of all hits; only then is the margin applied.
- Parsing is scoped to the distribution subsections and done line by
  line, not with one regex across the whole note.
- The guard labels: the numeric guards 24..96 are kept, but under the
  Live convention used everywhere here they are C0..C6 (the draft's
  comment called 96 "C7", the Roland reading).
- There is no LangGraph node in the real system; what is real is the
  pure reader plus a state-dict adapter with the draft's shape, which
  any pipeline (or kihachi-mcp's constraint builder) can call.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from automation_log import find_latest_session_note, parse_note_name

DEFAULT_RANGE = (36, 84)  # C1..C5 in Live naming; used when no data exists
_ROW_RE = re.compile(r"^\|\s*\*\*(.+?)\*\*\s*\|\s*(\d+)\s*hits\s*\|")


@dataclass(frozen=True)
class PitchRange:
    min_pitch: int
    max_pitch: int
    source: str  # "Obsidian_Pitch_Distribution" or "default"
    total_hits: int = 0
    pitches_used: list[int] = field(default_factory=list)


def parse_distribution_rows(content: str) -> dict[int, int]:
    """MIDI number -> total hits, from every distribution subsection.

    Rows from all tracks in the note are merged: the range describes the
    session's playing, not one part. Rows outside a Pitch Distribution
    subsection are ignored even if they look similar.
    """
    merged: dict[int, int] = {}
    in_distribution = False
    for line in content.split("\n"):
        stripped = line.strip()
        if stripped.startswith("### "):
            in_distribution = "Pitch Distribution" in stripped
            continue
        if stripped.startswith("## ") or stripped == "---":
            in_distribution = False
            continue
        if not in_distribution:
            continue
        match = _ROW_RE.match(stripped)
        if not match:
            continue
        number = parse_note_name(match.group(1))
        if number is None:
            continue
        merged[number] = merged.get(number, 0) + int(match.group(2))
    return merged


def _covering_window(distribution: dict[int, int], coverage: float) -> tuple[int, int, int]:
    """Smallest [lo, hi] pitch window covering >= coverage of all hits."""
    total = sum(distribution.values())
    target = total * coverage
    pitches = sorted(distribution)
    best: tuple[int, int] | None = None
    covered = 0
    left = 0
    for right, pitch in enumerate(pitches):
        covered += distribution[pitch]
        while covered - distribution[pitches[left]] >= target:
            covered -= distribution[pitches[left]]
            left += 1
        if covered >= target:
            lo, hi = pitches[left], pitch
            if best is None or hi - lo < best[1] - best[0]:
                best = (lo, hi)
    assert best is not None  # coverage <= 1 and total > 0 guarantee a window
    return best[0], best[1], total


def extract_pitch_range(
    vault_dir: Path,
    *,
    margin: int = 2,
    floor: int = 24,
    ceiling: int = 96,
    coverage: float = 0.98,
) -> PitchRange:
    """Optimized generation range from the latest session note.

    margin expands the covered window on both sides (semitones); floor
    and ceiling are hard guards (24 = C0, 96 = C6 in Live naming). With
    no note or no distribution data, DEFAULT_RANGE is returned and the
    source says so.
    """
    if not 0 < coverage <= 1:
        raise ValueError("coverage must be within (0, 1]")
    if margin < 0:
        raise ValueError("margin must be >= 0")
    note = find_latest_session_note(vault_dir)
    if note is None:
        return PitchRange(DEFAULT_RANGE[0], DEFAULT_RANGE[1], source="default")
    distribution = parse_distribution_rows(note.read_text(encoding="utf-8"))
    if not distribution:
        return PitchRange(DEFAULT_RANGE[0], DEFAULT_RANGE[1], source="default")
    lo, hi, total = _covering_window(distribution, coverage)
    return PitchRange(
        min_pitch=max(floor, lo - margin),
        max_pitch=min(ceiling, hi + margin),
        source="Obsidian_Pitch_Distribution",
        total_hits=total,
        pitches_used=sorted(distribution),
    )


def composer_range_state(state: dict) -> dict:
    """The draft's node shape, without LangGraph: state in, state out.

    Reads state["obsidian_vault_path"] and sets
    state["midi_generation_range"] plus state["step"], exactly as the
    draft's Composer node did -- callable from any pipeline.
    """
    vault_path = state.get("obsidian_vault_path", "./obsidian_vault")
    result = extract_pitch_range(Path(vault_path))
    state["step"] = "midi_range_optimized"
    state["midi_generation_range"] = {
        "min_pitch": result.min_pitch,
        "max_pitch": result.max_pitch,
        "source": result.source,
    }
    print(
        f"[Pitch Optimizer] generation range set to "
        f"{result.min_pitch} ~ {result.max_pitch} (source: {result.source})"
    )
    return state
