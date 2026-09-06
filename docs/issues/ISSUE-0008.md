# ISSUE-0008

# Audio Tool

Status: Done

Priority: High

Milestone: v0.3

---

## Goal

Define the boundary between the deterministic Ableton plan and asynchronous ACE-Step audio generation without changing the existing public MCP tools.

## Contract

    ProjectPlan
      -> AudioRenderRequest
      -> ACE-Step adapter
      -> AudioRenderResult

AudioRenderRequest is renderer-neutral. It identifies the project and target track or stem, and carries tempo, key, duration or bars, and generation instructions. It does not contain an API key or an Ableton mutation command.

AudioRenderResult is an artifact receipt, not raw provider output. It identifies lifecycle status, task identifier, output location, duration, format metadata, and SHA-256 when an artifact is verified.

## Lifecycle

    planned -> submitted -> running -> succeeded
                                      failed

Authentication, quota, timeout, unavailable backend, invalid response, and download or validation errors are explicit failures or blocked states. A progress response is never treated as a completed audio file. No dummy WAV, placeholder path, or automatic Ableton adoption is allowed.

## Ownership

- Brain/Domain: decide project and target intent.
- Ableton adapter: translate ProjectPlan to AbletonProjectPlan only.
- Audio service: build the renderer-neutral request and manage lifecycle.
- ACE-Step adapter: authenticate, submit, poll, download, and validate.
- Ableton execution: a later separately authorized step.

## Non-goals

- No live ACE-Step credentials or production backend verification in CI.
- No Ableton Live API, subprocess, MIDI, or automatic clip placement.
- No automatic adoption of generated audio into an Ableton set.
- Existing four MCP tool names and JSON shapes remain unchanged; generate_audio is additive.

## Acceptance Criteria

- ADR-0004 records the one-way handoff and failure semantics.
- Future implementation can test the audio service with fakes and no ACE-Step credentials.
- Secrets cannot appear in request, result, logs, or persisted plans.
- An artifact is accepted only after file and checksum validation.
