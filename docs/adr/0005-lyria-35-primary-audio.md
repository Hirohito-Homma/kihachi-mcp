# ADR-0005: Use Google Lyria 3.5 as Primary Audio Generation Provider

## Status

Accepted

## Context

KIHACHI MUSIC AI needs a production audio generation path that can turn Brain
output (SongSpec, GenreTemplate, GenerationContext, Arrangement, ProjectPlan)
into a verified stereo reference mix. Local ACE-Step inference is not operable
on the current Intel Mac runtime (dependency, wheel, model size, and GPU
constraints). The existing `GoogleLyriaAdapter` already owns the provider
boundary, but the real urllib path collapsed `HTTPError` into `OSError`, the
request omitted `response_format: audio`, and prompts did not carry
time-based arrangement.

Official Lyria 3.5 output is MP3 at 44.1 kHz stereo. Duration is prompt-guided
and is not a reliable machine-readable field unless separately parsed.

## Decision

Use Google Lyria 3.5 (`lyria-3.5`) as the only primary audio generation
provider in this repository.

    ProjectPlan / Knowledge
        -> AudioRenderRequest (renderer-neutral)
        -> LyriaPromptBuilder (provider-specific prompt)
        -> GoogleLyriaAdapter
        -> AudioRenderResult (verified MP3 receipt)

The adapter calls:

    POST https://generativelanguage.googleapis.com/v1beta/interactions

with `GEMINI_API_KEY` in `x-goog-api-key`, model `lyria-3.5`, and
`response_format: { "type": "audio" }`. Audio is taken from
`steps[type=model_output].content[type=audio].data`, with `output_audio` as a
compatibility fallback. A response without a verified audio block is a
failure.

`duration_seconds` stays `0` unless a real duration is parsed. `sample_rate`
and `channels` may be filled with the documented Lyria 3.5 defaults (44100 /
2) after a verified MP3 is written. Stems, isolated kick/bass, and
multi-track export are not implemented.

Lyria never writes Ableton. Successful generation does not authorize Live
adoption.

## Options Considered

- Keep ACE-Step as a local fallback: rejected because it is not operable in
  this runtime and must not re-enter the primary path.
- Rewrite the adapter as a Google-only domain service: rejected because
  AudioRenderRequest / AudioRenderResult must stay provider-neutral.
- Treat lyrics/`output_text` as success: rejected because an audio artifact is
  the completion condition.

## Consequences

- Full-song / reference-mix generation is the first production role.
- HTTP 401/403/429 are blocked; 400/404/5xx, timeout, DNS, invalid JSON, and
  malformed audio are failed. Response bodies are never copied into errors.
- Secrets stay in environment variables. They must not appear in MCP
  arguments, SongSpec, ProjectPlan, AudioRenderResult, logs, tests, or Git.
- CI continues to use fake HTTP. Live smoke is optional and credential-gated.
