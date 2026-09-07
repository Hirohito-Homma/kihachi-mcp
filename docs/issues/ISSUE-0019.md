# ISSUE-0019 — Lyria 3.5 Production API Integration

## Goal

Make Google Lyria 3.5 the production audio generator for KIHACHI MUSIC AI
without breaking the existing public MCP contract.

## Scope

- Keep AudioRenderRequest / AudioRenderResult provider-neutral.
- Send Interactions API payloads with `model=lyria-3.5` and
  `response_format.type=audio`.
- Parse `model_output` audio blocks, with `output_audio` as fallback.
- Classify real HTTP failures without collapsing `HTTPError` into `OSError`.
- Verify MP3 artifacts before reporting success.
- Convert Arrangement into tempo-based timestamps in the Lyria prompt.
- Do not add ACE-Step or claim undocumented stem export.

## Acceptance criteria

- Default model is `lyria-3.5`.
- Secrets never appear in results or exceptions.
- Existing public MCP tool names and JSON contracts remain.
- pytest, Ruff, and FastMCP registration stay green.
- Live smoke succeeds or is explicitly blocked when `GEMINI_API_KEY` is absent.
