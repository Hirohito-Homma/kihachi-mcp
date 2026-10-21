"""Offline MusicPlan preview for the role selected in Studio.

This does not call a paid model, approve a plan, or send anything to Live.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from kihachi_mcp.models.instrument_spec import slap_bass_spec
from kihachi_mcp.models.live_mutation import LiveMutationPlan
from kihachi_mcp.models.midi_composer import (
    ROLE_ALL,
    MidiCompositionRequest,
    MusicalConstraints,
)
from kihachi_mcp.models.music_plan import (
    GenerationScope,
    MusicClip,
    MusicNote,
    MusicPlan,
    MusicTrack,
)
from kihachi_mcp.models.production_brief import PART_ORDER
from kihachi_mcp.services.midi_composer import DeterministicMIDIComposer
from kihachi_mcp.services.music_plan_compiler import compile_music_plan, dry_run_summary
from kihachi_mcp.services.music_plan_validator import validate_music_plan
from kihachi_mcp.services.slap_bass_slice import TRACK_ID, compose_slap_bass_slice

_ROLE_NAME = {
    "bass": "Bass",
    "chords": "Stab",
    "melody": "Lead",
    "arp": "Arp",
    "percussion": "Perc",
    "drums": "Drums",
}


@dataclass(frozen=True)
class PreparedMusicPlan:
    """JSON payload plus the exact Live plan that produced its hash."""

    payload: dict[str, object]
    live: LiveMutationPlan | None
    music: MusicPlan | None = None
    instruments: dict[str, Any] | None = None


def preview_music_plan(constraints: dict[str, Any] | None = None) -> dict[str, object]:
    """Preview the fixed slap-bass slice, or the posted role, without sending it."""
    return prepare_music_plan(constraints).payload


def prepare_music_plan(constraints: dict[str, Any] | None = None) -> PreparedMusicPlan:
    """Build the preview and keep the compiled plan for a later approval."""
    if not constraints:
        return _pack(compose_slap_bass_slice(), {TRACK_ID: slap_bass_spec()}, role="bass")
    try:
        posted = MusicalConstraints.from_dict(constraints)
    except (TypeError, ValueError) as exc:
        return PreparedMusicPlan(_rejected(str(exc)), None)
    roles = PART_ORDER if posted.role == ROLE_ALL else (posted.role,)
    engine = DeterministicMIDIComposer(seed=posted.tempo)
    tracks: list[MusicTrack] = []
    note_constraints = posted.to_dict()
    for key in ("genre", "mood", "prompt"):
        note_constraints.pop(key, None)
    for role in roles:
        composed = engine.compose(
            MidiCompositionRequest(
                MusicalConstraints.from_dict({**note_constraints, "role": role})
            )
        )
        name = role if role in PART_ORDER else _ROLE_NAME.get(role, role)
        tracks.append(
            MusicTrack(
                id=name,
                name=name,
                clips=(
                    MusicClip(
                        start_bar=1,
                        length_bars=posted.bars,
                        notes=tuple(
                            MusicNote(note.pitch, note.start, note.duration, note.velocity)
                            for note in composed
                        ),
                    ),
                ),
            )
        )
    planned = {track.id for track in tracks}
    direction = {
        key: value
        for key, value in (
            ("genre", posted.genre),
            ("mood", posted.mood),
            ("prompt", posted.prompt),
        )
        if value
    }
    warnings = (
        "この計画は画面の中だけで作っています。Astraへは送っていません。",
        "全パートはパートごとのトラックです。" if posted.role == ROLE_ALL else "選んだ役割だけを計画しています。",
    )
    if direction:
        warnings += ("ジャンル・雰囲気・指示は方向の記録です。この確認のノートは変えていません。",)
    plan = MusicPlan(
        tempo=posted.tempo,
        key=posted.key,
        bars=posted.bars,
        tracks=tuple(tracks),
        scope=GenerationScope(
            tracks=tuple(track.id for track in tracks),
            start_bar=1,
            length_bars=posted.bars,
            preserve=tuple(part for part in PART_ORDER if part not in planned),
        ),
        warnings=warnings,
    )
    packed = _pack(plan, None, role=posted.role)
    if direction:
        packed.payload["direction"] = direction
    return packed


def _pack(
    plan: MusicPlan,
    instruments: dict[str, Any] | None,
    *,
    role: str,
) -> PreparedMusicPlan:
    validation = validate_music_plan(plan, instruments)
    payload: dict[str, object] = {
        "ok": validation.ok,
        "executed": False,
        "live_changed": False,
        "save": False,
        "paid_api": False,
        "approved": False,
        "role": role,
        "validation": validation.to_dict(),
        "music": plan.to_dict(),
    }
    if not validation.ok:
        payload["error"] = validation.issues[0].message
        return PreparedMusicPlan(payload, None)
    live = compile_music_plan(plan, instruments)
    payload["plan_hash"] = live.plan_hash
    payload["dry_run"] = dry_run_summary(plan, live)
    payload["operations"] = [operation.op for operation in live.operations]
    return PreparedMusicPlan(payload, live, plan, instruments)


def _rejected(message: str) -> dict[str, object]:
    return {
        "ok": False,
        "error": message,
        "executed": False,
        "live_changed": False,
        "save": False,
        "paid_api": False,
    }
