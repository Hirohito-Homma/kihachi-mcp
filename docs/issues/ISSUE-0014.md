# ISSUE-0014 — Ableton Execution Adapter

## Status

Implemented as an injectable adapter boundary.

`AbletonExecutionAdapter` requires explicit approval, performs at most one mutation, and returns `unavailable` when no Live transport is configured. Tests use a fake transport only; no real Ableton mutation occurs.
