# Changelog

## Unreleased

### Removed (breaking)

- **Google Lyria is gone.** The `generate_audio` MCP tool, `GoogleLyriaAdapter`,
  `LyriaPromptBuilder`, `AudioService`, `AudioRenderRequest`,
  `AudioRenderResult`, `scripts/lyria_smoke.py`, and the MP3 fetch, validate,
  and store path are all deleted. KIHACHI generates no audio; the deliverable is
  an editable Ableton Live Set
- `GEMINI_API_KEY`, `LYRIA_MODEL`, `LYRIA_BASE_URL`, and `LYRIA_TIMEOUT` are no
  longer read. Live automation needs no external API key
- `AbletonExecutionAdapter`, `AbletonExecutionResult`, `LiveExecutionRequest`,
  and `AbletonService.request_live_execution`. The old adapter returned
  `status="executed"` without reading anything back from Live, which cannot be
  reconciled with the readback requirement

### Added

- Ableton Live automation behind a human approval gate (ISSUE-0020, ADR-0006).
  **Max for Live is now a hard requirement** for the Live tools
- Models: `LiveStateSnapshot`, `LiveMutationOperation`, `LiveMutationPlan`,
  `LiveExecutionReceipt`, `LiveConflict`, `LivePrecondition`,
  `LiveOperationReadback`, `LiveReadbackMismatch`, all schema-versioned and
  JSON round-trip safe
- Services: `LiveStateInspector`, `LiveMutationPlanner`,
  `SessionPatternBuilder`, `LiveDeviceCatalog`, `ApprovalGate`,
  `LiveExecutionService`, `ArrangementExpander`, `LocalhostBridgeTransport`,
  `FakeLiveTransport`
- MCP tools: `inspect_live_state`, `live_device_catalogue`,
  `create_live_mutation_plan`, `verify_live_execution`,
  `expand_session_to_arrangement`
- Session View generation: differential scene creation, empty-slot checks, MIDI
  clips with loop, length, name, and colour, deterministic notes with velocity
  and duration, and immediate readback verification
- Arrangement View expansion, allowed only after the Session receipt is
  `verified`, placed only into free time ranges
- Automatic loading of 11 Live stock devices, blocked rather than substituted
  when unavailable
- Loopback-only bridge: `127.0.0.1`, a fresh session token per start, request
  size cap, operation count cap, timeout, duplicate `request_id` rejection, and
  structured logs that never contain the token
- `maxforlive/kihachi.device.js` with the Live Object Model implementation,
  plus the protocol spec, setup, and packaging steps
- Docs: `MAXFORLIVE.md`, `MANUAL_LIVE_TESTS.md`, `RECOVERY.md`, ADR-0006,
  ISSUE-0020, ISSUE-0021
- Knowledge-driven generation context: `KnowledgeEntry`, `KnowledgeContext`,
  `GenerationRequest`, `GenerationContext`, and `GenerationResult`
- `KnowledgeService` query / select / filter over packaged genre knowledge
- `GenerationService` and `Brain.generate_from_knowledge()` for provenance

### Changed (breaking)

- Public MCP tools go from 13 to 17. `fastmcp list server.py` expects
  `Tools (17)`
- `request_live_execution` keeps its name but now builds a `LiveMutationPlan`
  and writes its approval token to an owner-only file instead of returning it
- `execute_live_request` keeps its name but now requires `approval_token`,
  applies a plan exactly once, and returns a `LiveExecutionReceipt`.
  `verified` means every operation was applied *and* read back matching
- `generate_song` now goes through `GenerationService` while keeping the
  existing SongSpec JSON shape
- ADR-0003, ADR-0004, and ADR-0005 are superseded by ADR-0006

### Security

- The only secret in the Live path is a loopback session token, generated per
  start, passed through a `0600` handshake file, attached by the transport, and
  excluded from logs, errors, tool output, and Git

### Not verified

- **Ableton Live and Max for Live have not been exercised on real hardware.**
  The automated suite uses a fake transport only. See `docs/MANUAL_LIVE_TESTS.md`
- The `.amxd` binary is not in this repository, because Max's binary format
  cannot be generated correctly from text and a fake device file would be worse
  than none
- The `load_live_device` Max patch wiring is incomplete; JavaScript cannot drive
  the Live browser

---

## v0.1.0 - 2026-09-07

### Added

- GitHub Actions CI for tests, Ruff, and FastMCP tool registration

- Knowledge Engine with Dub Techno, Tech House, and Melodic Techno YAML
- Typed Arrangement sections in ProjectPlan with opt-in MCP serialization
- Plan-only AbletonProjectPlan adapter with deterministic track and locator mapping
- ISSUE-0008 AudioRenderRequest、Google Lyria adapter、generate_audio Tool、artifact validation
- ISSUE-0009 ReviewResult を返す `review_songspec` Tool
- Memory entry、JSON永続化、検索フィルターを備えた `remember_song` / `search_memory` Tools
- Review・Memory・Project作成を統合する `orchestrate_song` Tool
- Ableton handoff、承認ゲート、実行アダプターのLive連携境界

### Changed

- SongSpec tempo, key, and tracks can now come from genre templates when omitted
- `generate_songspec` keeps its existing arguments and JSON shape while allowing knowledge-backed inputs to be omitted
- Brain now derives ProjectPlan arrangements from genre knowledge
- Ableton連携は静的計画と承認済み実行境界に分離し、未設定時は実機変更を行わない

### Fixed

-

---

## v0.1

Brain Foundation

### Added

- Project Layout
- Domain Models
- Service Layer
- Brain MCP
