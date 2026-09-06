# ISSUE-0018 — MIDI Plan Event Integration

## Goal

Allow `MidiPlan` to carry validated `MidiEvent` values while preserving the
existing deterministic clip placement output.

## Scope

- Add an optional, JSON-safe `events` collection to `MidiPlan`.
- Validate event track names against MIDI clips and project bar limits.
- Keep event ordering deterministic.
- Do not generate genre-specific notes or contact Ableton Live.

## Acceptance criteria

- Plans without events remain valid and round-trip unchanged except for the
  optional empty event collection.
- Events for audio or unknown tracks are rejected.
- Existing tests, Ruff, and FastMCP registration remain green.
