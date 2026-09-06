# Changelog

## Unreleased

### Added

- Knowledge Engine with Dub Techno, Tech House, and Melodic Techno YAML
- Typed Arrangement sections in ProjectPlan with opt-in MCP serialization
- Plan-only AbletonProjectPlan adapter with deterministic track and locator mapping
- ISSUE-0008 AudioRenderRequest、ACE-Step adapter、generate_audio Tool、artifact validation

### Changed

- SongSpec tempo, key, and tracks can now come from genre templates when omitted
- `generate_songspec` keeps its existing arguments and JSON shape while allowing knowledge-backed inputs to be omitted
- Brain now derives ProjectPlan arrangements from genre knowledge

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
