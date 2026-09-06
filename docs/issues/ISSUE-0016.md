# ISSUE-0016 — Deterministic MIDI Plan

## Goal

Convert a `ProjectPlan` into a deterministic, serializable MIDI event plan
for the Ableton handoff boundary.

## Scope

- Derive note/event intent from typed tracks and arrangement sections.
- Preserve tempo, bars, section boundaries, and track identity.
- Return JSON-safe data suitable for a future Ableton adapter.
- Do not connect to Ableton Live or mutate a project.

## Non-goals

- MIDI file rendering or audio generation.
- Live transport, clip creation, or device control.
- Changes to existing public MCP tool contracts.

## Acceptance criteria

- Same `ProjectPlan` always produces the same MIDI plan.
- Invalid track or section ranges return a structured validation error.
- Existing tests, Ruff, and FastMCP registration remain green.
