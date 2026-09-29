# Ableton Live Smoke Test

Status: **LIVE TEST PENDING**
Last attempted: 2026-09-29 — no live connection or Set mutation was attempted in this audit.
Configured workspace: `/Users/user/kihachi-music-ai` was absent; source checkout reviewed: `/Users/user/MusicAI/kihachi-mcp` (`f0d8f70`, pre-existing dirty worktree).

## Evidence recorded in this run

- KIHACHI automated tests with loopback permission: **626 passed**. They use FakeLiveTransport and do not prove Ableton Live behavior.
- Ruff: **All checks passed**.
- Existing `docs/CURRENT_SYSTEM_STATUS.md` reports earlier Live 12.4.5 tests. Those are historical evidence and were not revalidated here.
- Current Live version, connection, open Set, and KIHACHI device are **not verified**.

## Real-Live checklist

Run only against a named, disposable or user-approved Set. For each step, retain the before state, action receipt, and independent readback. Stop after any partial result; do not retry an operation automatically. Do not mark success from a socket bind, a FakeLiveTransport result, or an Apply button response alone.

| Step | Required check | Result in this run |
| --- | --- | --- |
| 1 | Connect to the installed KIHACHI Max for Live device; verify protocol and Live version by ping | PENDING — not attempted |
| 2 | Read current Set state: tempo, time signature, transport, tracks, clips and devices | PENDING — not attempted |
| 3 | Set tempo and read the exact value back | PENDING — not attempted |
| 4 | Create a KIHACHI-managed MIDI track and read its identity back | PENDING — not attempted |
| 5 | Load a selected sample into a confirmed empty Drum Rack pad and read sample/pad state back | PENDING — not attempted; current KIHACHI path only covers bundled samples / empty pads |
| 6 | Create a MIDI clip, insert notes, then read the exact notes back | PENDING — not attempted; current KIHACHI snapshot records note counts, not arbitrary clip note arrays |
| 7 | Start playback and stop playback, verifying transport state | PENDING — not attempted; transport control is not part of the current KIHACHI operation contract |
| 8 | Read a managed clip’s existing note events, revise only Bass timing, and verify all non-target notes are unchanged | PENDING — not attempted; unified Live-note read/revision/write path is not implemented |
| 9 | Replace one selected sample, verify the replacement, then restore the original sample | PENDING — not attempted; verified user-selected A/B replacement is not implemented |
| 10 | Exercise Live Undo or a KIHACHI snapshot restore and verify the prior state | PENDING — not attempted; general undo/redo and snapshot restore are not implemented |
| 11 | Change one supported device parameter and independently read it back | PENDING — not attempted |

## Result rule

A step is `PASS` only when the real Live operation completes and the expected state is independently read back. Record unsupported actions as `UNSUPPORTED` and failures/partial application verbatim. Do not convert a pending step into a pass based on simulation.
