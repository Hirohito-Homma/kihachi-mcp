"""First composer slice: 8 bars of slap bass, offline unless a composer is injected.

The returned object is a MusicPlan. It has no Live client.
"""

from __future__ import annotations

from kihachi_mcp.models.instrument_spec import slap_bass_spec
from kihachi_mcp.models.midi_composer import MidiCompositionRequest, MusicalConstraints
from kihachi_mcp.models.music_plan import (
    GenerationScope,
    MusicClip,
    MusicNote,
    MusicPlan,
    MusicTrack,
)
from kihachi_mcp.services.midi_composer import DeterministicMIDIComposer, MIDIComposer
from kihachi_mcp.services.music_plan_compiler import compile_music_plan, dry_run_summary
from kihachi_mcp.services.music_plan_validator import validate_music_plan
from kihachi_mcp.services.musical_time import beats_per_bar

SLICE_KEY = "D#m"
SLICE_TEMPO = 110
SLICE_BARS = 8
TRACK_ID = "slap_bass"


def compose_slap_bass_slice(composer: MIDIComposer | None = None, seed: int = 110) -> MusicPlan:
    """Compose one slap-bass clip inside a fixed 8-bar scope."""
    engine = composer or DeterministicMIDIComposer(seed=seed)
    constraints = MusicalConstraints(
        key=SLICE_KEY,
        tempo=SLICE_TEMPO,
        bars=SLICE_BARS,
        role="bass",
        syncopation=0.7,
        octave_jump=0.12,
        ghost_notes=True,
        avoid_kick_collision=True,
        kick_onsets=tuple(
            float(beat) for beat in range(int(SLICE_BARS * beats_per_bar(4, 4)))
        ),
    )
    composed = engine.compose(MidiCompositionRequest(constraints))
    notes = tuple(
        MusicNote(note.pitch, note.start, note.duration, note.velocity) for note in composed
    )
    scope = GenerationScope(
        tracks=(TRACK_ID,),
        start_bar=1,
        length_bars=SLICE_BARS,
        preserve=("kick", "drums", "percussion"),
    )
    return MusicPlan(
        tempo=SLICE_TEMPO,
        key=SLICE_KEY,
        bars=SLICE_BARS,
        tracks=(
            MusicTrack(
                id=TRACK_ID,
                name="SLAP BASS",
                clips=(MusicClip(start_bar=1, length_bars=SLICE_BARS, notes=notes),),
            ),
        ),
        scope=scope,
        warnings=(
            "アーティキュレーション番号は Live デバイスに未確認です。ノートの音程と強さだけを使います。",
            f"楽器制約は {slap_bass_spec().instrument_id} です。",
        ),
    )


def preview_slap_bass_slice(composer: MIDIComposer | None = None) -> dict[str, object]:
    """Return the offline plan and a dry-run. Nothing is approved or sent."""
    plan = compose_slap_bass_slice(composer)
    instruments = {TRACK_ID: slap_bass_spec()}
    validation = validate_music_plan(plan, instruments)
    payload: dict[str, object] = {
        "ok": validation.ok,
        "executed": False,
        "live_changed": False,
        "save": False,
        "paid_api": False,
        "validation": validation.to_dict(),
        "music": plan.to_dict(),
    }
    if not validation.ok:
        payload["error"] = validation.issues[0].message
        return payload
    live = compile_music_plan(plan, instruments)
    payload["dry_run"] = dry_run_summary(plan, live)
    payload["operations"] = [operation.op for operation in live.operations]
    return payload
