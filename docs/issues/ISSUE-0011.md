# ISSUE-0011 — Orchestrator Design

## Status

Implemented.

## Workflow

```text
SongService.generate
  -> ReviewService.review_songspec
  -> MemoryService.remember
  -> ProjectService.create_project_from_songspec
```

`Orchestrator` coordinates services without owning their domain rules. `OrchestrationResult` keeps the SongSpec, ReviewResult, MemoryEntry, and ProjectPlan together.

## Public tool

`orchestrate_song` is additive. Existing tool names, arguments, and JSON shapes remain unchanged.

When `stop_on_review_failure=true`, an unapproved review returns `project: null` after the result is remembered. The default remains `false` for compatibility.

## Boundary

No Ableton mutation, external audio call, or persistent database is performed. Those integrations remain separate follow-up concerns.
