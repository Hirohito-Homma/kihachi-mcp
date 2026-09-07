# Changelog

## Unreleased

### Added

- Knowledge-driven generation context: `KnowledgeEntry`, `KnowledgeContext`,
  `GenerationRequest`, `GenerationContext`, and `GenerationResult`
- `KnowledgeService` query / select / filter over packaged genre knowledge
- `GenerationService` and `Brain.generate_from_knowledge()` for provenance
- Audio generation consumes genre knowledge when the project genre is known

### Changed

- `generate_song` now goes through `GenerationService` while keeping the
  existing SongSpec JSON shape

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
