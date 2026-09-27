# ISSUE-0019 — Lyria 3.5 Production API Integration

> **Status: 撤回済み（ISSUE-0020 により機能全体を削除）**
>
> Google Lyria は廃止済みです。この Issue が実装した `GoogleLyriaAdapter`、
> `LyriaPromptBuilder`、`generate_audio`、MP3 検証はすべて削除されました。
> 判断の記録は [../adr/0006-ableton-live-automation.md](../adr/0006-ableton-live-automation.md)。
> 履歴として残しています。実装の参照には使わないでください。


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
