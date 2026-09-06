# ADR-0002: Opt-in Arrangement Serialization

## Status

Accepted

## Context

ProjectPlan needs song sections for downstream DAW planning.
The stable MCP JSON contract must remain compatible with existing clients.

## Decision

`ProjectPlan` stores typed `Arrangement` values internally.
`Brain` derives them from genre knowledge and scales them to the SongSpec bars.
`create_project_from_songspec` keeps its existing JSON response by default
and includes `arrangement` only when `include_arrangement=true`.
Unknown genres keep producing ProjectPlan values with an empty arrangement.

## Options Considered

- Always add `arrangement` to JSON: rejected because exact-shape clients break.
- Keep arrangement internal only: rejected because MCP clients cannot use it.
- Add opt-in serialization: accepted as additive and backward compatible.

## Consequences

- Existing callers receive the same JSON keys.
- New callers can request structured song sections.
- Downstream adapters must explicitly request arrangement data.
