# ADR-0003: Plan-only Ableton Adapter

## Status

Superseded by [ADR-0006](0006-ableton-live-automation.md)

`AbletonService.create_plan`、`create_midi_plan`、`prepare_handoff` と、MCP ツール
`create_ableton_plan`、`create_midi_plan`、`prepare_ableton_handoff` はそのまま有効です。

無効になったのは実行境界です。この ADR が予告した「a later execution adapter must add
explicit authorization and readback」の readback 部分が `AbletonExecutionAdapter` に実装されず、
読戻しなしで `status="executed"` を返していました。ADR-0006 が
`LiveStateInspector` / `LiveMutationPlanner` / `ApprovalGate` /
`LiveExecutionService` へ置き換え、`AbletonExecutionAdapter`、
`AbletonExecutionResult`、`LiveExecutionRequest` を削除しました。

## Context

ProjectPlan now contains tracks and arrangement sections.
Ableton is an external-process boundary and must not be mutated by the Brain MCP.

## Decision

Add AbletonService as a deterministic translator from ProjectPlan to
AbletonProjectPlan.
The output uses Ableton-oriented track definitions and arrangement locators.
The adapter performs no Live API calls, filesystem writes, subprocess calls,
or automatic execution.

Expose the translator through the additive create_ableton_plan MCP tool.
Existing tools and their JSON contracts remain unchanged.

## Options Considered

- Mutate Ableton Live directly: rejected because execution needs a separate
  approval and verification boundary.
- Return a generic ProjectPlan: rejected because downstream Ableton mapping
  remains implicit.
- Return a deterministic Ableton plan: accepted as a testable handoff.

## Consequences

- Ableton integration can be tested without Live.
- A later execution adapter must add explicit authorization and readback.
- Plans do not contain MIDI notes, devices, or audio assets yet.
