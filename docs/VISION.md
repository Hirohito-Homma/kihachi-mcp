# PROJECT VISION

> "Music production is not a single task.
> It is a collaboration between specialists."

---

# Vision

KIHACHI MUSIC AI is not a music generator.

It is an AI-powered music production platform.

Our goal is to build a system where specialized AI agents collaborate
to create professional-quality music from an initial idea to a finished production.

The platform should think, compose, arrange, review, improve,
and finally deliver a complete music project.

---

# Mission

Enable musicians to work with AI as creative partners,
not as replacements.

Every AI component should have a clear responsibility,
just like members of a professional music production team.

---

# Core Principles

## 1. Human First

The human creator always makes the final decision.

AI proposes.

Humans decide.

---

## 2. Modular Architecture

Every capability belongs to an independent module.

Modules communicate through well-defined interfaces.

No module should know internal implementation details of another module.

---

## 3. Maintainability

Long-term maintainability is more important than short-term speed.

Readable code wins.

Simple architecture wins.

Documentation is part of the product.

---

## 4. Backward Compatibility

Existing workflows should continue working whenever possible.

Breaking changes require architectural review.

---

## 5. Testability

Every important feature must be testable.

Continuous Integration is required.

---

# Platform Architecture

```
                User
                  │
                  ▼
          Orchestrator MCP
                  │
     ┌────────────┼────────────┐
     ▼            ▼            ▼
 Brain MCP   Memory MCP   Review MCP
     │            │            │
     ▼            │            ▼
 SongSpec         │      ReviewResult
     │            │
     ▼            ▼
 Ableton MCP   Knowledge Base
     │
     ▼
 Live Mutation Planner
     │
     ▼
 Approval Gate
     │
     ▼
 Max for Live
     │
     ▼
 Session View → Arrangement View
     │
     ▼
 Editable Live Set
```

---

# Platform Components

## Brain

Creates musical ideas.

Produces

- SongSpec
- Arrangement
- Track plans

---

## Ableton

Builds projects. **Max for Live is required.**

Creates

- Tracks
- Scenes
- Session View clips and MIDI notes
- Live stock devices
- Locators
- Arrangement View clips

Every change requires human approval and is confirmed by reading Live back.
Existing user content is never overwritten.

---

## Audio

KIHACHI generates no audio.

Google Lyria was removed in [adr/0006-ableton-live-automation.md](adr/0006-ableton-live-automation.md).
A rendered audio file cannot be edited in a DAW, so it is not the deliverable of
a production platform. The deliverable is an editable Live Set.

The user renders audio from Ableton Live when the Set is ready.

---

## Review

Evaluates music.

Produces

- scores
- comments
- improvement suggestions

---

## Memory

Learns from previous projects.

Stores

- successful ideas
- failed ideas
- reusable knowledge

---

## Orchestrator

Coordinates every MCP.

Responsible for

- workflow execution
- task scheduling
- dependency management

---

# Development Philosophy

Every implementation should satisfy

- simplicity
- modularity
- testability
- documentation
- maintainability

before adding new functionality.

---

# Definition of Success

A successful platform can

- understand a musical request
- design a complete production plan
- build an Ableton project
- generate audio
- review the result
- improve the music
- remember what it learned
- help create better songs over time

---

# Long-Term Roadmap

## Phase 1

Brain Foundation

---

## Phase 2

Ableton Integration (Max for Live, approval-gated, readback-verified)

---

## Phase 3

Set persistence and external plugin allow list

Previously "Google Lyria Integration", withdrawn in
[adr/0006-ableton-live-automation.md](adr/0006-ableton-live-automation.md).

---

## Phase 4

Memory System

---

## Phase 5

Multi-Agent Orchestration

---

## Phase 6

Autonomous Music Production

---

# Non Goals

The project is NOT designed to replace musicians.

The project exists to amplify human creativity.

---

# Motto

Think.

Create.

Review.

Learn.

Improve.

Together.
