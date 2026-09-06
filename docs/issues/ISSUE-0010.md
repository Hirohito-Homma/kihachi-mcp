# ISSUE-0010 — Memory Integration

## Status

Implemented.

## Goal

Keep generated song decisions and review results available to the Brain during the current MCP process.

## Design

```text
SongSpec JSON + ReviewResult JSON -> MemoryService -> MemoryEntry JSON
```

The default implementation is process-local and deterministic. When `KIHACHI_MEMORY_PATH` is set, entries are loaded from and atomically saved to a JSON file. The service boundary can be replaced by another repository later.

## Public tools

- `remember_song(songspec, review)`
- `search_memory(genre)`

## Acceptance

- Entries can be saved and queried case-insensitively by exact genre.
- Separate Brain instances do not share entries.
- Existing tests remain green.
