# ADR-0004: Audio Tool Boundary

## Status

Accepted

## Context

AbletonProjectPlan describes arrangement and track structure, but it is not an audio render request. ACE-Step is an external generation service with its own authentication, asynchronous task lifecycle, quota, and output artifact. The Brain MCP must keep those concerns out of domain models and must not mutate Ableton or invent an audio result when the service is unavailable.

## Decision

Keep the handoff one-way and explicit:

    ProjectPlan
        -> AudioRenderRequest
        -> ACE-Step adapter
        -> AudioRenderResult (artifact receipt)

The request is renderer-neutral and contains project context, target track or stem role, duration or bars, tempo, key, and generation instructions. ACE-Step-specific fields belong only in the adapter boundary.

The adapter owns the external REST lifecycle: submit, poll, download, validate existence, non-zero size, expected duration, and checksum, then return an artifact receipt without credentials.

Credentials come from ACESTEP_API_KEY and optional ACESTEP_BASE_URL. They are never included in MCP arguments, JSON results, logs, or persisted plans. A submitted or progressing task is not a completed audio result.

The first implementation remains additive. A future generate_audio MCP tool may expose this boundary, while the current four public tools remain unchanged. This issue defines the contract only; it does not call ACE-Step, write audio files, or execute Ableton.

## Options Considered

- Embed ACE-Step calls in SongService: rejected because domain generation becomes vendor- and network-dependent.
- Put audio paths in AbletonProjectPlan: rejected because a plan is not evidence that audio was generated or verified.
- Let ACE-Step mutate Ableton: rejected because generation and DAW execution need separate authorization and readback.
- Add a renderer-neutral request and an ACE-Step adapter: accepted because planning, generation, and artifact verification stay independently testable.

## Consequences

- Audio generation can be retried and audited without changing arrangement plans.
- Authentication, quota, timeout, backend, and download failures become explicit blocked or failed states.
- A successful response must carry a verified artifact receipt; progress metadata alone is insufficient.
- A later Ableton execution step must explicitly adopt the receipt and verify the resulting Live state.

## Action Items

- Add typed AudioRenderRequest and AudioRenderResult models.
- Add an ACE-Step infrastructure adapter with injectable HTTP and storage clients.
- Add focused contract tests using fake clients; do not require ACE-Step in CI.
- Add an additive MCP tool only after the service contract is tested.
