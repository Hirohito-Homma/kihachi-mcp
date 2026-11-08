"""The Supervisor -> Composer -> Mixer pipeline, without LangGraph.

The draft of this pipeline was written against LangGraph's StateGraph.
LangGraph exists nowhere in the real system (no package, no graph, in
kihachi-mcp or elsewhere), so this is the same three nodes and the same
state shape on a plain sequential runner. The node functions keep the
draft's signatures (state dict in, state dict out), so they would drop
into a real StateGraph unchanged if one is ever adopted.

What each node actually does here:

- supervisor: bookkeeping only, as in the draft.
- composer: delegates to pitch_range.extract_pitch_range -- coverage-
  trimmed window, margin, guards, the shared note parser -- and reports
  the range's real source ("default" when no session data exists; the
  draft hardcoded "Obsidian_Pitch_Distribution" even for defaults).
- mixer: emits the monitoring path in the VERIFIED order
  MW10 -> FX-1001Jx2 (amp) -> Q100 (EQ) -> BOSE 101. The draft, like
  several before it, wrote Q100 before the amp; the user's own
  correction of 2026-11-05 puts the amp first. The automation cue is
  emitted in the exact DubDelayCue shape the TS translator
  (dubDelaySendAutomation.ts) consumes -- target_track, parameter as
  intent, beats_per_bar and curve_type made explicit -- so the
  handoff needs no reshaping (verified cross-language in the tests).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pitch_range import extract_pitch_range

VERIFIED_MONITORING_PATH = "YAMAHA MW10 ➔ FX-AUDIO FX-1001Jx2 ➔ YAMAHA Q100 ➔ BOSE 101"


def supervisor_node(state: dict[str, Any]) -> dict[str, Any]:
    print(f"\n[Pipeline: Supervisor] Starting music generation session: {state['session_id']}")
    state["step"] = "supervisor_initialized"
    state["progress"] = 10
    return state


def composer_node(state: dict[str, Any]) -> dict[str, Any]:
    print("[Pipeline: Composer] Optimizing MIDI generation range from Obsidian session history...")
    vault_path = state.get("obsidian_vault_path", "./obsidian_vault")
    result = extract_pitch_range(Path(vault_path))
    state["step"] = "midi_range_optimized"
    state["progress"] = 60
    state["midi_generation_range"] = {
        "min_pitch": result.min_pitch,
        "max_pitch": result.max_pitch,
        "source": result.source,
    }
    print(f"[Pipeline: Composer] Optimized range: {result.min_pitch} ~ {result.max_pitch} (source: {result.source})")
    return state


def mixer_node(state: dict[str, Any]) -> dict[str, Any]:
    print("[Pipeline: Mixer] Configuring MW10 hardware & Dub Delay automation cues...")
    state["step"] = "mixer_completed"
    state["progress"] = 100
    state["mixer_instructions"] = {
        "audio_interface": "YAMAHA MW10",
        "equalizer": "YAMAHA Q100",
        "monitoring_path": VERIFIED_MONITORING_PATH,
        # DubDelayCue shape, ready for translateDubDelayCue on the TS
        # side: beats 32..48 = 16 beats = 4 bars; "Feedback" is the
        # intent -- the executor resolves the real parameter (Dry/Wet on
        # an External Audio Effect, which has no Feedback).
        "automation_cue": {
            "target_track": "KIHACHI_Dub_Delay_Send",
            "parameter": "Feedback",
            "start_beat": 32.0,
            "end_beat": 48.0,
            "beats_per_bar": 4,
            "min_val": 0.3,
            "max_val": 0.85,
            "curve_type": "sigmoid",
        },
    }
    print("[Pipeline: Mixer] Pipeline execution completed.")
    return state


PIPELINE_NODES = (supervisor_node, composer_node, mixer_node)


def run_music_pipeline(initial_state: dict[str, Any]) -> dict[str, Any]:
    """Run supervisor -> composer -> mixer over a plain state dict."""
    state = dict(initial_state)
    state.setdefault("step", "idle")
    state.setdefault("progress", 0)
    state.setdefault("midi_generation_range", {})
    state.setdefault("mixer_instructions", {})
    for node in PIPELINE_NODES:
        state = node(state)
    return state
