# KIHACHI MUSIC AI
# System Architecture

Version: 1.0

Status: Living Document

---

# Overview

KIHACHI MUSIC AI is composed of independent MCP services.

Each MCP has a single responsibility.

Communication happens through shared domain models.

```
                    User
                      │
                      ▼
              Orchestrator MCP
                      │
    ┌─────────────────┼──────────────────┐
    ▼                 ▼                  ▼
 Brain MCP       Memory MCP        Review MCP
    │                 │                  │
    ▼                 │                  ▼
 SongSpec             │          ReviewResult
    │                 │
    ▼                 ▼
 Ableton MCP     Knowledge Store
    │
    ▼
 Project Builder
    │
    ▼
 ACE-Step MCP
    │
    ▼
 Audio
```

---

# Design Principles

## Single Responsibility

Each MCP owns one responsibility.

Brain thinks.

Ableton builds.

ACE-Step creates audio.

Memory remembers.

Review evaluates.

Orchestrator coordinates.

---

## Loose Coupling

Modules communicate only through shared models.

Never import another MCP's internal implementation.

Communication occurs via public interfaces.

---

## Shared Domain Models

All MCPs share common models.

Examples

- SongSpec
- TrackSpec
- Arrangement
- ProjectPlan
- ReviewResult

These models define the platform language.

---

# Layered Architecture

```
Presentation
    ↓
MCP Tools
    ↓
Brain Facade
    ↓
Services
    ↓
Domain Models
    ↓
Infrastructure
```

---

## Presentation Layer

FastMCP tools

Responsibilities

- receive requests
- validate inputs
- call services
- return responses

Business logic is forbidden.

---

## Service Layer

Contains application logic.

Examples

- SongService
- ProjectService
- ReviewService
- MemoryService
- AbletonService
- AceStepService

---

## Domain Layer

Contains pure data structures.

No FastMCP dependency.

No filesystem dependency.

No network dependency.

---

## Infrastructure Layer

Responsible for

- filesystem
- Ableton communication
- ACE-Step execution
- database
- external APIs

---

# MCP Responsibilities

## Brain MCP

Produces

- SongSpec
- Arrangement
- Track Plans

Never renders audio.

---

## Ableton MCP

Produces

- Ableton Project
- MIDI
- Device Chains
- Track Routing

Never decides composition.

---

## ACE-Step MCP

Produces

- Audio
- Stems
- Samples

Never edits arrangements.

---

## Review MCP

Produces

- Scores
- Comments
- Suggestions

Never modifies projects.

---

## Memory MCP

Stores

- successful songs
- failed songs
- reusable knowledge

---

## Orchestrator MCP

Coordinates every MCP.

Responsibilities

- workflow
- retries
- scheduling
- state management

---

# Data Flow

```
User
    ↓
Brain
    ↓
SongSpec
    ↓
Ableton
    ↓
ProjectPlan
    ↓
ACE-Step
    ↓
Audio
    ↓
Review
    ↓
Memory
    ↓
Knowledge
    ↓
Brain
```

This feedback loop enables continuous improvement.

---

# Repository Layout

```
src/
    kihachi_mcp/
        models/
        services/
        tools/
        shared/

tests/

docs/
```

Implementation lives under `src/kihachi_mcp/` ([ADR-0001](adr/0001-use-src-layout.md)). The repository root keeps `server.py` as a compatibility entry point.

---

# Coding Rules

- Use dataclasses for domain models.
- Prefer dependency injection.
- Avoid global state.
- Use type hints everywhere.
- Keep functions small and focused.
- Write unit tests before integration tests.

---

# Testing Strategy

```
Unit Tests
    ↓
Service Tests
    ↓
Integration Tests
    ↓
End-to-End Tests
```

---

# Documentation Strategy

Every feature requires

- Issue
- ADR (if architecture changes)
- Tests
- Changelog

---

# Future Expansion

Future MCPs

- MIDI Generator
- Lyrics Generator
- Stem Separator
- Mastering
- Mixing
- Publishing
- Streaming
- Analytics

The architecture should allow adding new MCPs without changing existing ones.

---

# Success Criteria

The platform can

Think

↓

Plan

↓

Build

↓

Generate

↓

Review

↓

Learn

↓

Improve

↓

Repeat

without breaking modularity.

---

# Current Brain MCP (this repository)

This repo is Phase 1 Brain Foundation. Public tools remain `hello`, `generate_songspec`, and `create_project_from_songspec`. Details: [API.md](API.md).

## Current services

| Service | Responsibility |
| --- | --- |
| SongService | Generate, validate, defaults, bar/duration conversion |
| ProjectService | ProjectPlan, name, output path, metadata, created_at |
| ReviewService | Score, comments, warnings, suggestions (internal) |

## Current conversion rules

SongSpec

- `bars = max(1, round(length_minutes * 32))`
- tracks come from genre YAML via the Knowledge Engine
- `mood` is accepted and not stored

ProjectPlan

- `project_name` is `"Untitled"`
- copies genre / tempo / key / length_minutes / bars
- track types and colors:

| name | type | color |
| --- | --- | --- |
| Kick | MIDI | Red |
| Bass | MIDI | Blue |
| Dub Chords | MIDI | Purple |
| Lead | MIDI | Green |
| FX | Audio | Gray |

Unknown track names use type=`MIDI`, color=`Gray`.
