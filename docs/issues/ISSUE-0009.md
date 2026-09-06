# ISSUE-0009 — Review Integration

## Status

Implemented.

## Goal

Expose the existing Brain review boundary as a public MCP tool without changing existing tool contracts.

## Design

```text
SongSpec JSON -> review_songspec -> Brain.review_song -> ReviewResult JSON
```

The tool performs local validation only. It does not call an external provider or mutate Ableton.

## Acceptance

- `review_songspec` is registered as the sixth MCP tool.
- The public input is a `songspec` dictionary.
- The output contains `approved`, `score`, and `comments`.
- Existing tests remain green.
