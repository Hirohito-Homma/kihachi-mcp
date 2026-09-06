# ADR-0001: Use src-layout

## Status

Accepted

## Context

KIHACHI Brain will grow into a multi-package platform
consisting of Brain, Ableton, Google Lyria, Memory and Orchestrator.

To avoid import ambiguity and improve packaging,
the project adopts the standard Python src-layout.

## Decision

All implementation code shall live under

src/kihachi_mcp/

The repository root is reserved for compatibility
entry points and project metadata.

## Consequences

Pros

- Clear package boundaries
- Easier packaging
- Better test isolation
- Standard Python layout

Cons

- Slightly longer import paths
