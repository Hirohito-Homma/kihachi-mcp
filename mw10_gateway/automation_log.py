"""APC40 live automation stats -> Obsidian session note.

Pure-stdlib core for the (future) MW10 gateway's /api/automation-log
endpoint. The FastAPI wiring lives in app.py; everything that touches the
note lives here so it can be tested without a server.

Differences from the draft this is based on:

- The draft's regex replaced the WHOLE log section with a single new row
  on every POST, silently deleting every other parameter's history. Here
  the section is processed line by line: a row for the same
  (parameter, track) is replaced with the latest stats, rows for other
  parameters are kept, and a new parameter's row is appended to the table.
- No regex across the document, so a following heading or rule (---) can
  never be swallowed by an over-long match.
- Cell text is escaped so a "|" or backtick in a track name cannot break
  the Markdown table.
- The draft file itself did not parse (full-width quotes throughout,
  including the route path). This module is plain ASCII-quoted Python.

Which notes count as session notes -- files named Session_MW10_*.md in
the vault directory -- follows the draft's convention; that naming is
from the v2.0 target spec and has not been observed in the real vault
(the known Obsidian folder is the conversation archive), so the vault
path is always supplied explicitly, never defaulted.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

LOG_HEADER = "## 🎛️ APC40 Live Performance & Automation Log"
TABLE_HEADER = (
    "| Parameter | Track | Events Count | Duration (Beats) | Min / Max Val |\n"
    "|:---|:---|:---|:---|:---|"
)
SESSION_NOTE_GLOB = "Session_MW10_*.md"


@dataclass(frozen=True)
class AutomationStats:
    session_id: str
    track_name: str
    parameter_name: str
    total_events: int
    duration_beats: float
    min_value: float
    max_value: float

    def validate(self) -> None:
        if self.total_events < 0:
            raise ValueError("total_events must be >= 0")
        if self.duration_beats < 0:
            raise ValueError("duration_beats must be >= 0")
        if self.min_value > self.max_value:
            raise ValueError("min_value must be <= max_value")


def _escape_cell(text: str) -> str:
    return text.replace("\\", "\\\\").replace("|", "\\|").replace("`", "'")


def render_row(stats: AutomationStats) -> str:
    return (
        f"| `{_escape_cell(stats.parameter_name)}` "
        f"| `{_escape_cell(stats.track_name)}` "
        f"| **{stats.total_events} events** "
        f"| {stats.duration_beats:.2f} beats "
        f"| {stats.min_value:.2f} / {stats.max_value:.2f} |"
    )


def _row_key_prefix(stats: AutomationStats) -> str:
    return f"| `{_escape_cell(stats.parameter_name)}` | `{_escape_cell(stats.track_name)}` |"


def upsert_table_row(
    content: str,
    *,
    header: str,
    table_header: str,
    row: str,
    key_prefix: str,
    extra_boundaries: tuple[str, ...] = (),
) -> str:
    """Insert or replace one row in the note's stats table for `header`.

    The section runs from its header line to the next heading or rule.
    A row whose start matches key_prefix is replaced (latest stats win);
    other rows are preserved; a new row is appended after the last one.
    Shared by every stats log (automation, MIDI, ...) so they all behave
    identically and never disturb each other's sections. Lines starting
    with any of extra_boundaries also end the row region -- the MIDI log
    uses this so its base table stops before its ### subsections.
    """
    lines = content.split("\n")

    header_index = next(
        (i for i, line in enumerate(lines) if line.strip() == header),
        None,
    )
    if header_index is None:
        # New section at the end of the note.
        base = content if content.endswith("\n") or content == "" else content + "\n"
        return f"{base}\n{header}\n\n{table_header}\n{row}\n"

    section_end = len(lines)
    for i in range(header_index + 1, len(lines)):
        stripped = lines[i].strip()
        if (
            stripped.startswith("## ")
            or stripped == "---"
            or any(stripped.startswith(marker) for marker in extra_boundaries)
        ):
            section_end = i
            break

    last_row_index: int | None = None
    for i in range(header_index + 1, section_end):
        if lines[i].startswith("| `"):
            if lines[i].startswith(key_prefix):
                lines[i] = row
                return "\n".join(lines)
            last_row_index = i

    if last_row_index is not None:
        lines.insert(last_row_index + 1, row)
    else:
        # Section exists but has no table yet: add header + row inside it.
        insert_at = header_index + 1
        lines[insert_at:insert_at] = ["", *table_header.split("\n"), row]
    return "\n".join(lines)


def update_note_content(content: str, stats: AutomationStats) -> str:
    """Return the note text with the stats applied to the log section."""
    stats.validate()
    return upsert_table_row(
        content,
        header=LOG_HEADER,
        table_header=TABLE_HEADER,
        row=render_row(stats),
        key_prefix=_row_key_prefix(stats),
    )


def find_latest_session_note(vault_dir: Path) -> Path | None:
    if not vault_dir.is_dir():
        return None
    candidates = sorted(
        vault_dir.glob(SESSION_NOTE_GLOB),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


def _update_latest_note(vault_dir: Path, update, message: str) -> dict:
    target = find_latest_session_note(vault_dir)
    if target is None:
        return {
            "success": False,
            "error": f"No MW10 session notes ({SESSION_NOTE_GLOB}) found in {vault_dir}.",
        }
    content = target.read_text(encoding="utf-8")
    target.write_text(update(content), encoding="utf-8")
    return {"success": True, "message": message.format(name=target.name), "file": target.name}


def update_automation_log(vault_dir: Path, stats: AutomationStats) -> dict:
    """Apply stats to the latest session note in vault_dir."""
    stats.validate()
    return _update_latest_note(
        vault_dir,
        lambda content: update_note_content(content, stats),
        "Automation log synchronized to Obsidian note '{name}'.",
    )


# ---------------------------------------------------------------------------
# MIDI performance statistics (POST /api/midi-log)
# ---------------------------------------------------------------------------

MIDI_LOG_HEADER = "## 🎹 MIDI Performance Statistics"
MIDI_TABLE_HEADER = (
    "| Track | Total Notes Played | Avg Velocity | Min / Max Velocity |\n"
    "|:---|:---|:---|:---|"
)


@dataclass(frozen=True)
class MidiStats:
    session_id: str
    track_name: str
    total_notes: int
    avg_velocity: float
    max_velocity: int
    min_velocity: int
    # Pitch histogram as note-name -> hit count (e.g. {"C3": 12}). None or
    # empty means "no distribution in this report"; an existing
    # distribution subsection in the note is then left as it is.
    note_distribution: dict[str, int] | None = None

    def validate(self) -> None:
        if self.total_notes < 0:
            raise ValueError("total_notes must be >= 0")
        # Velocities are MIDI values: 0..127, and an average that falls
        # outside [min, max] means the sender's stats are inconsistent.
        for label, value in (
            ("min_velocity", self.min_velocity),
            ("max_velocity", self.max_velocity),
        ):
            if not 0 <= value <= 127:
                raise ValueError(f"{label} must be a MIDI velocity (0..127)")
        if self.min_velocity > self.max_velocity:
            raise ValueError("min_velocity must be <= max_velocity")
        if not self.min_velocity <= self.avg_velocity <= self.max_velocity:
            raise ValueError("avg_velocity must lie between min_velocity and max_velocity")
        if self.note_distribution:
            for name, count in self.note_distribution.items():
                if not name:
                    raise ValueError("note_distribution keys must be non-empty note names")
                if not isinstance(count, int) or count < 1:
                    raise ValueError("note_distribution counts must be positive integers")


def render_midi_row(stats: MidiStats) -> str:
    return (
        f"| `{_escape_cell(stats.track_name)}` "
        f"| **{stats.total_notes} notes** "
        f"| {stats.avg_velocity:.1f} "
        f"| {stats.min_velocity} / {stats.max_velocity} |"
    )


_NOTE_BASE = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}


def parse_note_name(name: str) -> int | None:
    """MIDI number for a note name like C3 / G#3 / Eb2 / Bb-1, or None.

    The octave convention is Ableton Live's: C3 = 60, C-2 = 0, G8 = 127.
    It must stay in sync with midiNoteName in the TS monitor, which is
    where these names are produced. This is the one parser for the whole
    gateway folder -- pitch_range.py reads names back through it, so the
    write side and the read side can never drift apart.
    """
    import re as _re

    match = _re.fullmatch(r"([A-G])(#|b)?(-?\d+)", name.strip())
    if not match:
        return None
    letter, accidental, octave = match.groups()
    value = _NOTE_BASE[letter] + (int(octave) + 2) * 12
    if accidental == "#":
        value += 1
    elif accidental == "b":
        value -= 1
    return value if 0 <= value <= 127 else None


def _note_sort_value(name: str) -> int:
    """Sort key for note names; unparseable names sort after real pitches."""
    parsed = parse_note_name(name)
    return parsed if parsed is not None else 100_000


DISTRIBUTION_TABLE_HEADER = (
    "| Pitch / Note | Hit Count | Frequency Bar |\n"
    "|:---|:---|:---|"
)
MAX_BAR_BLOCKS = 20


def distribution_subsection_header(track_name: str) -> str:
    return f"### 📊 Pitch Distribution — `{_escape_cell(track_name)}`"


def render_distribution_block(stats: MidiStats) -> list[str]:
    """The per-track pitch histogram block (subsection header + table).

    Rows sort by hit count, most first, ties by pitch height. Bars are
    scaled so the most-hit pitch fills MAX_BAR_BLOCKS blocks -- the
    draft's flat min(count, 20) cap made a 21-hit and a 500-hit pitch
    indistinguishable, which defeats a histogram.
    """
    distribution = stats.note_distribution or {}
    max_count = max(distribution.values())
    ordered = sorted(
        distribution.items(),
        key=lambda item: (-item[1], _note_sort_value(item[0]), item[0]),
    )
    lines = [
        distribution_subsection_header(stats.track_name),
        "",
        DISTRIBUTION_TABLE_HEADER,
    ]
    for name, count in ordered:
        blocks = max(1, round(count / max_count * MAX_BAR_BLOCKS))
        lines.append(f"| **{_escape_cell(name)}** | {count} hits | `{'█' * blocks}` |")
    return lines


def upsert_subsection(
    content: str,
    *,
    section_header: str,
    subsection_header: str,
    block: list[str],
) -> str:
    """Insert or replace a ### subsection inside a ## section.

    The subsection body runs to the next ### / ## heading or rule within
    the parent section. A missing subsection is appended at the end of
    the parent section. The parent section must already exist.
    """
    lines = content.split("\n")
    section_index = next(
        (i for i, line in enumerate(lines) if line.strip() == section_header),
        None,
    )
    if section_index is None:
        raise ValueError(f"parent section '{section_header}' not found")

    section_end = len(lines)
    for i in range(section_index + 1, len(lines)):
        stripped = lines[i].strip()
        if stripped.startswith("## ") or stripped == "---":
            section_end = i
            break

    subsection_index = next(
        (
            i
            for i in range(section_index + 1, section_end)
            if lines[i].strip() == subsection_header
        ),
        None,
    )
    if subsection_index is None:
        insert_at = section_end
        # Keep one blank line between the previous content and the block.
        lines[insert_at:insert_at] = ["", *block]
        return "\n".join(lines)

    body_end = section_end
    for i in range(subsection_index + 1, section_end):
        stripped = lines[i].strip()
        if stripped.startswith(("### ", "## ")) or stripped == "---":
            body_end = i
            break
    lines[subsection_index:body_end] = block
    return "\n".join(lines)


def update_midi_note_content(content: str, stats: MidiStats) -> str:
    """Return the note text with the MIDI stats applied to their section."""
    stats.validate()
    updated = upsert_table_row(
        content,
        header=MIDI_LOG_HEADER,
        table_header=MIDI_TABLE_HEADER,
        row=render_midi_row(stats),
        key_prefix=f"| `{_escape_cell(stats.track_name)}` |",
        extra_boundaries=("### ",),
    )
    if stats.note_distribution:
        updated = upsert_subsection(
            updated,
            section_header=MIDI_LOG_HEADER,
            subsection_header=distribution_subsection_header(stats.track_name),
            block=render_distribution_block(stats),
        )
    return updated


def update_midi_log(vault_dir: Path, stats: MidiStats) -> dict:
    """Apply MIDI stats to the latest session note in vault_dir."""
    stats.validate()
    return _update_latest_note(
        vault_dir,
        lambda content: update_midi_note_content(content, stats),
        "MIDI statistics synchronized to Obsidian note '{name}'.",
    )
