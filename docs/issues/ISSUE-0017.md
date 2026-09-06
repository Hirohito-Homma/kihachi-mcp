# ISSUE-0017 — MIDI Event Schema

## Goal

Define a JSON-safe event schema that can later carry deterministic MIDI note
intent without coupling KIHACHI MCP to Ableton Live or a MIDI file library.

## Scope

- Add validated event fields: track, bar offset, duration, pitch, velocity.
- Keep event ordering deterministic.
- Reject events outside their target clip or project bar range.
- Preserve the existing `create_midi_plan` contract.

## Non-goals

- Genre-specific composition rules.
- MIDI file export or Ableton Live mutation.
- External audio generation.

## Acceptance criteria

- The schema round-trips through JSON safely.
- Invalid pitch, velocity, duration, and bar ranges return structured errors.
- Existing public tools and tests remain compatible.
