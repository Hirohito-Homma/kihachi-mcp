"""Turn a MIDI candidate into a Live plan without regenerating notes."""

import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from kihachi_mcp.knowledge.part_sounds import (
    MASTER_CHAIN,
    PART_EFFECTS,
    PART_INSTRUMENTS,
    PART_MIX,
    SIDECHAIN_DUCKING,
)
from kihachi_mcp.knowledge.sound_recipes import DeviceRecipe, recipe_for, tuned
from kihachi_mcp.models.live_contract import (
    OP_CREATE_LOCATOR,
    OP_CREATE_MIDI_TRACK,
    OP_CREATE_SCENE,
    OP_CREATE_SESSION_CLIP,
    OP_LOAD_DRUM_PAD_SAMPLE,
    OP_LOAD_LIVE_DEVICE,
    OP_PLACE_ARRANGEMENT_CLIP,
    OP_REPLACE_CLIP_NOTES,
    OP_SET_DEVICE_PARAMETER,
    OP_SET_SIDECHAIN_SOURCE,
    OP_SET_TEMPO,
    OP_SET_TRACK_MIXER,
    canonical_hash,
    managed_clip_name,
    managed_track_name,
)
from kihachi_mcp.models.live_mutation import (
    LiveConflict,
    LiveMutationOperation,
    LiveMutationPlan,
    LivePrecondition,
)
from kihachi_mcp.models.live_state import TRACK_TYPE_MIDI, LiveStateSnapshot
from kihachi_mcp.models.midi_candidate import CandidateClip, MidiCandidate
from kihachi_mcp.models.production_brief import DRUM_PARTS, STUDIO_PARTS
from kihachi_mcp.services import live_device_catalog
from kihachi_mcp.services.drum_samples import HAT_NOTE, KICK_NOTE, sample_path_for_note
from kihachi_mcp.services.live_approval_gate import APPROVAL_TTL_SECONDS

CONFLICT_RECORDING = "live_is_recording"
CONFLICT_PLAYING = "live_is_playing"
CONFLICT_USER_CLIP = "user_owned_clip"
CONFLICT_SESSION_MISSING = "session_clip_missing"
CONFLICT_ARRANGEMENT_OCCUPIED = "arrangement_range_occupied"

_TRACK_COLORS = {
    "Kick": "2",
    "Snare": "3",
    "Hats": "20",
    "OpenHat": "21",
    "Perc": "22",
    "Sub": "13",
    "Bass": "14",
    "Stab": "9",
    "Pad": "10",
    "Arp": "17",
    "Guitar": "18",
    "Horn": "12",
    "Lead": "16",
    "Vocal": "25",
    "FX": "26",
}
_SHORT_ID_RE = re.compile(r"[0-9a-f]{8}")


@dataclass(frozen=True)
class AppliedTracks:
    """Tracks an earlier apply left in Live, known only by their short id.

    Placing the bundled one-shots needs just two facts from a candidate: the
    short id in the track names and the drum pitches. When a restart has lost
    the candidate, the pitches are the ones the bundled samples cover, which
    are the only ones the candidate builder writes for Kick and Hats.
    """

    short_id: str

    def __post_init__(self) -> None:
        if not _SHORT_ID_RE.fullmatch(self.short_id):
            raise ValueError("トラックIDは8桁の16進数で指定してください")

    @property
    def candidate_id(self) -> str:
        """Return the short id where a candidate would give its full id."""
        return self.short_id

    @property
    def parts(self) -> tuple[str, ...]:
        """The legacy four-track layout used for a short-ID pad repair."""
        return STUDIO_PARTS

    @property
    def note_fingerprint(self) -> str:
        """Hash what the plan relies on. There are no candidate notes here."""
        return canonical_hash(
            {"applied_tracks": self.short_id, "pitches": _BUNDLED_PITCHES}
        )

    def used_pitches(self, part: str) -> tuple[int, ...]:
        """Return the bundled pad for Kick and Hats, and nothing otherwise."""
        return _BUNDLED_PITCHES.get(part, ())


_BUNDLED_PITCHES = {"Kick": (KICK_NOTE,), "Hats": (HAT_NOTE,)}


class CandidateLivePlanner:
    """Plan dedicated new tracks whose clip notes equal the candidate notes."""

    def __init__(
        self,
        request_id_factory: Callable[[], str] | None = None,
        clock: Callable[[], datetime] | None = None,
        ttl_seconds: int = APPROVAL_TTL_SECONDS,
    ) -> None:
        self._request_id_factory = request_id_factory or (lambda: uuid.uuid4().hex)
        self._clock = clock or (lambda: datetime.now(UTC))
        self._ttl_seconds = ttl_seconds

    def create_plan(
        self,
        candidate: MidiCandidate,
        snapshot: LiveStateSnapshot,
        change_tempo: bool = False,
        skip_instruments: bool = False,
        external_kit_parts: frozenset[str] = frozenset(),
    ) -> LiveMutationPlan:
        """Return an inert plan. Conflicts block execution.

        ``skip_instruments`` leaves the new tracks without Drum Rack, synths or
        samples, so another tool (AbletonGPT's browser loader, or the user) can
        put a kit or preset there: that loader refuses a track that already has
        an instrument.
        """
        builder = _Builder(
            candidate, snapshot, change_tempo, skip_instruments, external_kit_parts
        )
        operations, conflicts, warnings = builder.build()
        request_id = self._request_id_factory()
        source_plan_hash = candidate.note_fingerprint
        return LiveMutationPlan(
            request_id=request_id,
            idempotency_key=canonical_hash(
                {
                    "request_id": request_id,
                    "candidate_id": candidate.candidate_id,
                    "note_fingerprint": source_plan_hash,
                    "set_fingerprint": snapshot.set_fingerprint,
                    "change_tempo": change_tempo,
                    "skip_instruments": skip_instruments,
                    "external_kit_parts": sorted(external_kit_parts),
                }
            ),
            source_plan_hash=source_plan_hash,
            set_fingerprint=snapshot.set_fingerprint,
            expires_at=(
                self._clock() + timedelta(seconds=self._ttl_seconds)
            ).isoformat(),
            operations=operations,
            conflicts=conflicts,
            warnings=warnings,
        )

    def create_arrangement_plan(
        self,
        candidate: MidiCandidate,
        snapshot: LiveStateSnapshot,
    ) -> LiveMutationPlan:
        """Copy the candidate's Session clips to their bars in the Arrangement.

        Every Session clip already holds its whole window of notes, so one copy
        per clip at its start bar lays out the song exactly as previewed. The
        snapshot must include Arrangement clips: an occupied range is refused,
        never moved or trimmed.
        """
        builder = _Builder(candidate, snapshot, change_tempo=False)
        operations, conflicts, warnings = builder.build_arrangement()
        request_id = self._request_id_factory()
        return LiveMutationPlan(
            request_id=request_id,
            idempotency_key=canonical_hash(
                {
                    "request_id": request_id,
                    "candidate_id": candidate.candidate_id,
                    "note_fingerprint": candidate.note_fingerprint,
                    "set_fingerprint": snapshot.set_fingerprint,
                    "stage": "arrangement",
                }
            ),
            source_plan_hash=candidate.note_fingerprint,
            set_fingerprint=snapshot.set_fingerprint,
            expires_at=(
                self._clock() + timedelta(seconds=self._ttl_seconds)
            ).isoformat(),
            operations=operations,
            conflicts=conflicts,
            warnings=warnings,
        )

    def create_drum_sample_plan(
        self,
        candidate: MidiCandidate | AppliedTracks,
        snapshot: LiveStateSnapshot,
    ) -> LiveMutationPlan:
        """Plan only empty-pad sample loads. Does not create tracks or clips."""
        builder = _Builder(candidate, snapshot, change_tempo=False)
        operations, conflicts, warnings = builder.build_drum_samples_only()
        request_id = self._request_id_factory()
        return LiveMutationPlan(
            request_id=request_id,
            idempotency_key=canonical_hash(
                {
                    "request_id": request_id,
                    "candidate_id": candidate.candidate_id,
                    "kind": "drum_samples",
                    "set_fingerprint": snapshot.set_fingerprint,
                }
            ),
            source_plan_hash=candidate.note_fingerprint,
            set_fingerprint=snapshot.set_fingerprint,
            expires_at=(
                self._clock() + timedelta(seconds=self._ttl_seconds)
            ).isoformat(),
            operations=operations,
            conflicts=conflicts,
            warnings=warnings,
        )

    def create_effects_plan(
        self,
        candidate: MidiCandidate,
        snapshot: LiveStateSnapshot,
    ) -> LiveMutationPlan:
        """Plan effect chains on tracks an earlier apply created. Adds only."""
        return self._stage_plan(candidate, snapshot, "effects")

    def create_mix_plan(
        self,
        candidate: MidiCandidate,
        snapshot: LiveStateSnapshot,
    ) -> LiveMutationPlan:
        """Plan fader and pan settings on tracks an earlier apply created."""
        return self._stage_plan(candidate, snapshot, "mix")

    def create_sidechain_plan(
        self,
        candidate: MidiCandidate,
        snapshot: LiveStateSnapshot,
        already_ducked: frozenset[str] = frozenset(),
    ) -> LiveMutationPlan:
        """Append a kick-keyed Compressor to the low parts. Adds only.

        ``already_ducked`` names parts whose track already has a Compressor
        keyed from this kick; they are left alone.
        """
        builder = _Builder(candidate, snapshot, change_tempo=False)
        operations, conflicts, warnings = builder.build_sidechain_only(already_ducked)
        request_id = self._request_id_factory()
        return LiveMutationPlan(
            request_id=request_id,
            idempotency_key=canonical_hash(
                {
                    "request_id": request_id,
                    "candidate_id": candidate.candidate_id,
                    "kind": "sidechain",
                    "set_fingerprint": snapshot.set_fingerprint,
                }
            ),
            source_plan_hash=candidate.note_fingerprint,
            set_fingerprint=snapshot.set_fingerprint,
            expires_at=(
                self._clock() + timedelta(seconds=self._ttl_seconds)
            ).isoformat(),
            operations=operations,
            conflicts=conflicts,
            warnings=warnings,
        )

    def create_master_plan(
        self, snapshot: LiveStateSnapshot, retune: bool = False
    ) -> LiveMutationPlan:
        """Append the club mastering chain to the master track.

        Devices already on the master stay where they are and are never
        changed; a chain device that is already there is not added twice.
        ``retune`` sets the chain's knobs again, and only when the master ends
        with exactly this chain, which is how an earlier run left it.
        """
        conflicts = _transport_conflicts(snapshot)
        operations: list[LiveMutationOperation] = []
        warnings: list[str] = []
        present = list(snapshot.master_device_names)
        chain = [recipe.device for recipe in MASTER_CHAIN]
        if retune:
            if present[-len(chain):] != chain:
                conflicts.append(
                    LiveConflict(
                        "master_chain_not_found",
                        "マスターの最後が KIHACHI のマスタリング "
                        f"（{' → '.join(chain)}）ではないため、つまみは変えません",
                    )
                )
            start = len(present) - len(chain)
            for offset, recipe in enumerate(MASTER_CHAIN):
                for setting in recipe.settings:
                    operations.append(
                        _master_parameter_operation(
                            f"{len(operations) + 1:03d}-{OP_SET_DEVICE_PARAMETER}",
                            start + offset,
                            recipe.device,
                            setting,
                        )
                    )
            return self._master_plan(snapshot, operations, conflicts, warnings, "master_retune")
        missing = [recipe for recipe in MASTER_CHAIN if recipe.device not in present]
        available = snapshot.available_device_names()
        unavailable = [
            recipe.device for recipe in missing if recipe.device not in available
        ]
        if unavailable:
            warnings.append(
                f"このLiveでは {', '.join(unavailable)} を読み込めないため、マスタリングは計画しません"
            )
            missing = []
        if present:
            warnings.append(
                f"マスターの既存デバイス（{' → '.join(present)}）は変えず、その後ろに追加します"
            )
        if not conflicts:
            sequence = 0
            for offset, recipe in enumerate(missing):
                position = len(present) + offset
                sequence += 1
                operations.append(
                    LiveMutationOperation(
                        operation_id=f"{sequence:03d}-{OP_LOAD_LIVE_DEVICE}",
                        op=OP_LOAD_LIVE_DEVICE,
                        target={"master": True},
                        arguments={"device_name": recipe.device},
                        preconditions=[
                            LivePrecondition("not_recording"),
                            LivePrecondition("device_available", {"device_name": recipe.device}),
                        ],
                        destructive=False,
                        expected_readback={
                            "master": True,
                            "device_name": recipe.device,
                            "device_index": position,
                        },
                    )
                )
                for setting in recipe.settings:
                    sequence += 1
                    operations.append(
                        _master_parameter_operation(
                            f"{sequence:03d}-{OP_SET_DEVICE_PARAMETER}", position, recipe.device, setting
                        )
                    )
            if not missing and not unavailable:
                warnings.append("マスタリングのデバイスはすべて載っています")
        return self._master_plan(snapshot, operations, conflicts, warnings, "master")

    def _master_plan(
        self,
        snapshot: LiveStateSnapshot,
        operations: list[LiveMutationOperation],
        conflicts: list[LiveConflict],
        warnings: list[str],
        kind: str,
    ) -> LiveMutationPlan:
        request_id = self._request_id_factory()
        return LiveMutationPlan(
            request_id=request_id,
            idempotency_key=canonical_hash(
                {
                    "request_id": request_id,
                    "kind": kind,
                    "set_fingerprint": snapshot.set_fingerprint,
                }
            ),
            source_plan_hash=canonical_hash({"master_chain": [r.device for r in MASTER_CHAIN]}),
            set_fingerprint=snapshot.set_fingerprint,
            expires_at=(
                self._clock() + timedelta(seconds=self._ttl_seconds)
            ).isoformat(),
            operations=operations if not conflicts else [],
            conflicts=conflicts,
            warnings=warnings,
        )

    def _stage_plan(
        self, candidate: MidiCandidate, snapshot: LiveStateSnapshot, kind: str
    ) -> LiveMutationPlan:
        builder = _Builder(candidate, snapshot, change_tempo=False)
        build = builder.build_effects_only if kind == "effects" else builder.build_mix_only
        operations, conflicts, warnings = build()
        request_id = self._request_id_factory()
        return LiveMutationPlan(
            request_id=request_id,
            idempotency_key=canonical_hash(
                {
                    "request_id": request_id,
                    "candidate_id": candidate.candidate_id,
                    "kind": kind,
                    "set_fingerprint": snapshot.set_fingerprint,
                }
            ),
            source_plan_hash=candidate.note_fingerprint,
            set_fingerprint=snapshot.set_fingerprint,
            expires_at=(
                self._clock() + timedelta(seconds=self._ttl_seconds)
            ).isoformat(),
            operations=operations,
            conflicts=conflicts,
            warnings=warnings,
        )


class _Builder:
    def __init__(
        self,
        candidate: MidiCandidate | AppliedTracks,
        snapshot: LiveStateSnapshot,
        change_tempo: bool,
        skip_instruments: bool = False,
        external_kit_parts: frozenset[str] = frozenset(),
    ) -> None:
        self._candidate = candidate
        self._snapshot = snapshot
        self._change_tempo = change_tempo
        self._skip_instruments = skip_instruments
        # Parts whose kit another loader brings: they must stay empty here,
        # because that loader refuses a track that already has an instrument.
        self._external_kit_parts = external_kit_parts
        self._operations: list[LiveMutationOperation] = []
        self._conflicts: list[LiveConflict] = []
        self._warnings: list[str] = []
        self._sequence = 0
        self._next_track_index = len(snapshot.tracks)
        self._next_scene_index = len(snapshot.scenes)
        self._short_id = candidate.candidate_id[:8]

    def build(
        self,
    ) -> tuple[list[LiveMutationOperation], list[LiveConflict], list[str]]:
        self._guard_transport()
        if self._conflicts:
            return [], self._conflicts, self._warnings
        self._plan_tempo()
        tracks = self._plan_tracks()
        if self._skip_instruments:
            self._warnings.append(
                "音源は入れません。トラックは無音のままなので、キットやプリセットを"
                "後から入れてください"
            )
        else:
            self._plan_devices(tracks)
            self._plan_drum_samples(tracks)
        scenes = self._plan_scenes()
        self._plan_clips(tracks, scenes)
        if self._conflicts:
            return [], self._conflicts, self._warnings
        return self._operations, self._conflicts, self._warnings

    def build_arrangement(
        self,
    ) -> tuple[list[LiveMutationOperation], list[LiveConflict], list[str]]:
        self._guard_transport()
        if self._conflicts:
            return [], self._conflicts, self._warnings
        beats = self._snapshot.time_signature.beats_per_bar
        reserved: dict[int, list[tuple[float, float]]] = {}
        for part in self._candidate.parts:
            track_name = managed_track_name(f"KIHACHI {part} {self._short_id}")
            track = self._snapshot.track_by_name(track_name)
            if track is None:
                self._conflicts.append(
                    LiveConflict(
                        CONFLICT_SESSION_MISSING,
                        f"'{track_name}' がありません。先にSessionへ適用してください",
                        {"track_name": track_name},
                    )
                )
                continue
            clips = self._candidate.clips_for_part(part)
            missing = [
                clip
                for clip in clips
                if not self._plan_arrangement_clip(
                    track.index, track_name, clip, beats, reserved
                )
            ]
            if missing:
                self._conflicts.append(
                    LiveConflict(
                        CONFLICT_SESSION_MISSING,
                        f"{track_name} のSessionクリップが {len(clips)} 個中 "
                        f"{len(missing)} 個見つかりません（最初は {missing[0].section_name} "
                        f"{missing[0].start_bar}小節目）。Sessionのシーンやクリップが"
                        "消えている場合は、候補を作り直して適用してください",
                        {"track_name": track_name, "missing": len(missing)},
                    )
                )
        # Clips first: an empty Arrangement can keep Live from moving the
        # insert marker far enough to create later locators.
        for section in self._candidate.brief.sections:
            self._plan_section_locator(section.name, section.start_bar, beats)
        if self._conflicts:
            return [], self._conflicts, self._warnings
        self._warnings.append(
            "既存のロケーターと同じ位置に置く場合、そのロケーター操作は失敗として報告し、"
            "そこで止まります（既存のロケーターは消しません）"
        )
        return self._operations, self._conflicts, self._warnings

    def _plan_arrangement_clip(
        self,
        track_index: int,
        track_name: str,
        clip: CandidateClip,
        beats: float,
        reserved: dict[int, list[tuple[float, float]]],
    ) -> bool:
        """Plan one copy. Returns False only when the Session source is missing."""
        scene_name = managed_track_name(
            f"{clip.section_name} {clip.start_bar} {self._short_id}"
        )
        session_name = managed_clip_name(clip.section_name, self._session_clip_id(clip))
        scene = self._snapshot.scene_by_name(scene_name)
        source = (
            self._snapshot.session_clip_at(track_index, scene.index)
            if scene is not None
            else None
        )
        if source is None or source.name != session_name:
            return False
        start = round((clip.start_bar - 1) * beats, 6)
        length = round(clip.length_bars * beats, 6)
        end = start + length
        for existing in self._snapshot.arrangement_clips_on(track_index):
            if start < existing.end_beats and existing.start_beats < end:
                self._conflicts.append(
                    LiveConflict(
                        CONFLICT_ARRANGEMENT_OCCUPIED,
                        f"{track_name} の {clip.start_bar}〜"
                        f"{clip.start_bar + clip.length_bars - 1}小節には "
                        f"'{existing.name}' があります。既存のクリップは動かしません",
                        {"track_index": track_index, "existing_clip": existing.name},
                    )
                )
                return True
        for other_start, other_end in reserved.get(track_index, []):
            if start < other_end and other_start < end:
                self._conflicts.append(
                    LiveConflict(
                        CONFLICT_ARRANGEMENT_OCCUPIED,
                        f"{track_name} で候補のクリップ同士が重なっています",
                        {"track_index": track_index},
                    )
                )
                return True
        reserved.setdefault(track_index, []).append((start, end))
        name = managed_clip_name(
            clip.section_name,
            canonical_hash(
                {
                    "candidate": self._candidate.candidate_id,
                    "part": clip.part,
                    "start_bar": clip.start_bar,
                    "stage": "arrangement",
                }
            )[:8],
        )
        self._operations.append(
            LiveMutationOperation(
                operation_id=self._next_id(OP_PLACE_ARRANGEMENT_CLIP),
                op=OP_PLACE_ARRANGEMENT_CLIP,
                target={"track_index": track_index, "scene_index": scene.index},
                arguments={
                    "name": name,
                    "start_beats": start,
                    "length_beats": length,
                    "source_clip_name": session_name,
                },
                preconditions=[
                    LivePrecondition("not_recording"),
                    LivePrecondition("transport_stopped"),
                    LivePrecondition(
                        "track_name_at_index",
                        {"track_index": track_index, "name": track_name},
                    ),
                    LivePrecondition(
                        "clip_is_managed",
                        {"track_index": track_index, "scene_index": scene.index},
                    ),
                    LivePrecondition(
                        "arrangement_range_free",
                        {
                            "track_index": track_index,
                            "start_beats": start,
                            "length_beats": length,
                        },
                    ),
                ],
                destructive=False,
                expected_readback={
                    "track_index": track_index,
                    "name": name,
                    "start_beats": start,
                    "length_beats": length,
                    # The copy must hold exactly the notes that were previewed.
                    "note_count": len(clip.notes),
                },
            )
        )
        return True

    def _plan_section_locator(self, section: str, start_bar: int, beats: float) -> None:
        name = managed_track_name(f"{section} {self._short_id}")
        position = round((start_bar - 1) * beats, 6)
        self._operations.append(
            LiveMutationOperation(
                operation_id=self._next_id(OP_CREATE_LOCATOR),
                op=OP_CREATE_LOCATOR,
                target={"scope": "arrangement"},
                arguments={"name": name, "beats": position},
                preconditions=[
                    LivePrecondition("not_recording"),
                    LivePrecondition("transport_stopped"),
                ],
                destructive=False,
                expected_readback={"name": name, "beats": position},
            )
        )

    def _session_clip_id(self, clip: CandidateClip) -> str:
        """The managed id _plan_one_clip gave this clip's Session copy."""
        return canonical_hash(
            {
                "candidate": self._candidate.candidate_id,
                "part": clip.part,
                "section": clip.section_name,
                "start_bar": clip.start_bar,
            }
        )[:8]

    def build_drum_samples_only(
        self,
    ) -> tuple[list[LiveMutationOperation], list[LiveConflict], list[str]]:
        self._guard_transport()
        if self._conflicts:
            return [], self._conflicts, self._warnings
        tracks: list[dict[str, Any]] = []
        for part in self._candidate.parts:
            if part not in DRUM_PARTS:
                continue
            name = managed_track_name(f"KIHACHI {part} {self._short_id}")
            existing = self._snapshot.track_by_name(name)
            if existing is None:
                self._warnings.append(f"{name} が無いのでサンプルは置きません")
                continue
            tracks.append(
                {
                    "name": name,
                    "index": existing.index,
                    "created": False,
                    "part": part,
                    "device_names": list(existing.device_names),
                }
            )
        self._plan_drum_samples(tracks)
        if not self._operations:
            self._warnings.append("載せられる空の Drum Rack パッドはありません")
        return self._operations, self._conflicts, self._warnings

    def build_effects_only(
        self,
    ) -> tuple[list[LiveMutationOperation], list[LiveConflict], list[str]]:
        """Append each part's effect chain after whatever its track already holds.

        A device already on the track (a genre recipe's Echo, or an earlier run
        of this plan) is left as it is, so running twice adds nothing.
        """
        self._guard_transport()
        if self._conflicts:
            return [], self._conflicts, self._warnings
        available = self._snapshot.available_device_names()
        for part in self._candidate.parts:
            chain = PART_EFFECTS.get(part)
            if not chain:
                continue
            name = managed_track_name(f"KIHACHI {part} {self._short_id}")
            existing = self._snapshot.track_by_name(name)
            if existing is None:
                self._warnings.append(f"{name} が無いのでエフェクトは載せません")
                continue
            present = set(existing.device_names)
            missing = tuple(device for device in chain if device.device not in present)
            if not missing:
                continue
            track = {
                "name": name,
                "index": existing.index,
                "created": False,
                "part": part,
                "device_names": list(existing.device_names),
            }
            self._plan_recipe_chain(
                track, missing, available, first_index=len(existing.device_names)
            )
        if not self._operations:
            self._warnings.append("追加するエフェクトはありません（すべて載っています）")
        return self._operations, self._conflicts, self._warnings

    def build_sidechain_only(
        self, already_ducked: frozenset[str]
    ) -> tuple[list[LiveMutationOperation], list[LiveConflict], list[str]]:
        self._guard_transport()
        if self._conflicts:
            return [], self._conflicts, self._warnings
        kick = managed_track_name(f"KIHACHI Kick {self._short_id}")
        if self._snapshot.track_by_name(kick) is None:
            self._warnings.append(f"{kick} が無いのでサイドチェインは組めません")
            return [], self._conflicts, self._warnings
        available = self._snapshot.available_device_names()
        for part in self._candidate.parts:
            recipe = SIDECHAIN_DUCKING.get(part)
            if recipe is None:
                continue
            if part in already_ducked:
                self._warnings.append(f"{part} は既にキックでダッキングされています")
                continue
            name = managed_track_name(f"KIHACHI {part} {self._short_id}")
            existing = self._snapshot.track_by_name(name)
            if existing is None:
                self._warnings.append(f"{name} が無いのでサイドチェインは組めません")
                continue
            track = {
                "name": name,
                "index": existing.index,
                "created": False,
                "part": part,
                "device_names": list(existing.device_names),
            }
            position = len(existing.device_names)
            before = len(self._operations)
            self._plan_recipe_chain(track, (recipe,), available, first_index=position)
            if len(self._operations) == before:
                continue
            # Route the key right after loading, before any knob is set, so a
            # refusal leaves an unkeyed Compressor with S/C off, not a pumping one.
            self._operations.insert(
                before + 1,
                LiveMutationOperation(
                    operation_id=self._next_id(OP_SET_SIDECHAIN_SOURCE),
                    op=OP_SET_SIDECHAIN_SOURCE,
                    target={"track_index": existing.index, "device_index": position},
                    arguments={"device_name": recipe.device, "source_name": kick},
                    preconditions=[
                        LivePrecondition("not_recording"),
                        LivePrecondition(
                            "track_name_at_index",
                            {"track_index": existing.index, "name": name},
                        ),
                        LivePrecondition(
                            "device_name_at_index",
                            {
                                "track_index": existing.index,
                                "device_index": position,
                                "name": recipe.device,
                            },
                        ),
                    ],
                    destructive=False,
                    expected_readback={
                        "track_index": existing.index,
                        "device_index": position,
                        "input_routing_type": kick,
                    },
                ),
            )
        return self._operations, self._conflicts, self._warnings

    def build_mix_only(
        self,
    ) -> tuple[list[LiveMutationOperation], list[LiveConflict], list[str]]:
        """Set fader and pan on each [KIHACHI] track of the candidate. Nothing else."""
        self._guard_transport()
        if self._conflicts:
            return [], self._conflicts, self._warnings
        for part in self._candidate.parts:
            if part not in PART_MIX:
                continue
            name = managed_track_name(f"KIHACHI {part} {self._short_id}")
            existing = self._snapshot.track_by_name(name)
            if existing is None:
                self._warnings.append(f"{name} が無いのでMIXしません")
                continue
            volume_db, panning = PART_MIX[part]
            self._operations.append(
                LiveMutationOperation(
                    operation_id=self._next_id(OP_SET_TRACK_MIXER),
                    op=OP_SET_TRACK_MIXER,
                    target={"track_index": existing.index},
                    arguments={"volume_db": volume_db, "panning": panning},
                    preconditions=[
                        LivePrecondition("not_recording"),
                        LivePrecondition(
                            "track_name_at_index",
                            {"track_index": existing.index, "name": name},
                        ),
                    ],
                    destructive=False,
                    expected_readback={
                        "track_index": existing.index,
                        "volume_db": volume_db,
                        "panning": panning,
                    },
                )
            )
        return self._operations, self._conflicts, self._warnings

    def _guard_transport(self) -> None:
        self._conflicts.extend(_transport_conflicts(self._snapshot))

    def _plan_tempo(self) -> None:
        target = float(self._candidate.brief.tempo.value)
        current = self._snapshot.tempo
        if abs(current - target) < 1e-6:
            return
        if not self._change_tempo:
            self._warnings.append(
                f"このSetのテンポは {current:g} BPM です。候補は "
                f"{target:g} BPM ですが、既存曲を保護するためテンポは変更しません"
            )
            return
        operation_id = self._next_id(OP_SET_TEMPO)
        self._operations.append(
            LiveMutationOperation(
                operation_id=operation_id,
                op=OP_SET_TEMPO,
                target={"scope": "song"},
                arguments={"tempo": target},
                preconditions=[
                    LivePrecondition("not_recording"),
                    LivePrecondition("transport_stopped"),
                ],
                destructive=True,
                expected_readback={"tempo": target},
            )
        )
        self._warnings.append(f"Setテンポを {current:g} から {target:g} BPM へ変更します")

    def _plan_tracks(self) -> list[dict[str, Any]]:
        planned: list[dict[str, Any]] = []
        for part in self._candidate.parts:
            name = managed_track_name(f"KIHACHI {part} {self._short_id}")
            existing = self._snapshot.track_by_name(name)
            if existing is not None:
                self._warnings.append(
                    f"同名の専用トラック '{name}' が既にあるため、新規作成せずそのトラックを使います"
                )
                planned.append(
                    {
                        "name": name,
                        "index": existing.index,
                        "created": False,
                        "part": part,
                        "device_names": list(existing.device_names),
                    }
                )
                continue
            index = self._next_track_index
            self._next_track_index += 1
            self._operations.append(
                LiveMutationOperation(
                    operation_id=self._next_id(OP_CREATE_MIDI_TRACK),
                    op=OP_CREATE_MIDI_TRACK,
                    target={"track_index": index},
                    arguments={
                        "name": name,
                        "color": _TRACK_COLORS[part],
                        "index": index,
                    },
                    preconditions=[
                        LivePrecondition("not_recording"),
                        LivePrecondition("track_missing", {"name": name}),
                    ],
                    destructive=False,
                    expected_readback={
                        "track_index": index,
                        "name": name,
                        "track_type": TRACK_TYPE_MIDI,
                    },
                )
            )
            planned.append(
                {
                    "name": name,
                    "index": index,
                    "created": True,
                    "part": part,
                    "device_names": [],
                }
            )
        return planned

    def _plan_devices(self, tracks: list[dict[str, Any]]) -> None:
        available = self._snapshot.available_device_names()
        recipe = self._sound_recipe()
        for track in tracks:
            if track["device_names"] or track["part"] in self._external_kit_parts:
                continue
            part_recipe = recipe.parts.get(track["part"]) if recipe else None
            if part_recipe is not None:
                self._plan_recipe_chain(track, part_recipe.chain, available)
                continue
            device_name = live_device_catalog.suggest_instrument(track["part"])
            patch = PART_INSTRUMENTS.get(track["part"])
            if patch is not None and patch.device == device_name:
                self._plan_recipe_chain(track, (patch,), available)
                continue
            try:
                live_device_catalog.resolve(device_name, available)
            except live_device_catalog.LiveDeviceUnavailableError:
                self._warnings.append(
                    f"{track['name']} の音源 '{device_name}' は自動読込できません。"
                    "MIDIは配置します。Liveで手動で音源を載せてください"
                )
                continue
            self._operations.append(
                LiveMutationOperation(
                    operation_id=self._next_id(OP_LOAD_LIVE_DEVICE),
                    op=OP_LOAD_LIVE_DEVICE,
                    target={"track_index": track["index"]},
                    arguments={"device_name": device_name},
                    preconditions=[
                        LivePrecondition("not_recording"),
                        LivePrecondition(
                            "track_name_at_index",
                            {"track_index": track["index"], "name": track["name"]},
                        ),
                        LivePrecondition(
                            "device_available", {"device_name": device_name}
                        ),
                    ],
                    destructive=False,
                    expected_readback={
                        "track_index": track["index"],
                        "device_name": device_name,
                    },
                )
            )
            track["device_names"] = [device_name]

    def _sound_recipe(self):
        brief = getattr(self._candidate, "brief", None)
        if brief is None:
            return None
        recipe = recipe_for(str(brief.genre.value))
        if recipe is None:
            return None
        return tuned(recipe, tone_steps(brief))

    def _plan_recipe_chain(
        self,
        track: dict[str, Any],
        chain: tuple[DeviceRecipe, ...],
        available: Any,
        first_index: int = 0,
    ) -> None:
        """Insert each device of the recipe, then set its knobs by name.

        Live appends an inserted device to the end of the chain, so on a track
        holding ``first_index`` devices the n-th new one sits at index
        ``first_index + n``.
        """
        for device in chain:
            try:
                live_device_catalog.resolve(device.device, available)
            except live_device_catalog.LiveDeviceUnavailableError:
                self._warnings.append(
                    f"{track['name']} の '{device.device}' はこのLiveでは読み込めないため、"
                    "レシピのこの部分は使いません"
                )
                return
        names: list[str] = list(track["device_names"]) if first_index else []
        for position, device in enumerate(chain, start=first_index):
            self._operations.append(
                LiveMutationOperation(
                    operation_id=self._next_id(OP_LOAD_LIVE_DEVICE),
                    op=OP_LOAD_LIVE_DEVICE,
                    target={"track_index": track["index"]},
                    arguments={"device_name": device.device},
                    preconditions=[
                        LivePrecondition("not_recording"),
                        LivePrecondition(
                            "track_name_at_index",
                            {"track_index": track["index"], "name": track["name"]},
                        ),
                        LivePrecondition("device_available", {"device_name": device.device}),
                    ],
                    destructive=False,
                    expected_readback={
                        "track_index": track["index"],
                        "device_name": device.device,
                        "device_index": position,
                    },
                )
            )
            names.append(device.device)
            for setting in device.settings:
                self._operations.append(
                    _parameter_operation(
                        self._next_id(OP_SET_DEVICE_PARAMETER),
                        track,
                        position,
                        device.device,
                        setting,
                    )
                )
        track["device_names"] = names

    def _plan_drum_samples(self, tracks: list[dict[str, Any]]) -> None:
        if not _supports_replace_sample(self._snapshot.live_version):
            self._warnings.append(
                "Drum Rack へのサンプル自動配置は Live 12.4 以降が必要です。"
                "ドラム系トラックのパッドは手動で入れてください"
            )
            return
        for track in tracks:
            if track["part"] not in DRUM_PARTS:
                continue
            if "Drum Rack" not in track["device_names"]:
                continue
            for note in self._candidate.used_pitches(track["part"]):
                brief = getattr(self._candidate, "brief", None)
                genre = str(brief.genre.value) if brief is not None else ""
                path = sample_path_for_note(note, genre=genre)
                self._operations.append(
                    LiveMutationOperation(
                        operation_id=self._next_id(OP_LOAD_DRUM_PAD_SAMPLE),
                        op=OP_LOAD_DRUM_PAD_SAMPLE,
                        target={"track_index": track["index"]},
                        arguments={"note": note, "sample_path": str(path)},
                        preconditions=[
                            LivePrecondition("not_recording"),
                            LivePrecondition(
                                "track_name_at_index",
                                {"track_index": track["index"], "name": track["name"]},
                            ),
                        ],
                        destructive=False,
                        expected_readback={
                            "track_index": track["index"],
                            "note": note,
                            "occupied": True,
                        },
                    )
                )

    def _plan_scenes(self) -> dict[tuple[str, int], int]:
        indices: dict[tuple[str, int], int] = {}
        keys: list[tuple[str, int]] = []
        for clip in self._candidate.clips:
            key = (clip.section_name, clip.start_bar)
            if key not in keys:
                keys.append(key)
        # Clips are grouped by part, whose UDP splits can differ. Collect all
        # scene boundaries before appending them in musical time order.
        for section_name, start_bar in sorted(keys, key=lambda key: key[1]):
            scene_name = managed_track_name(
                f"{section_name} {start_bar} {self._short_id}"
            )
            existing = self._snapshot.scene_by_name(scene_name)
            if existing is not None:
                indices[(section_name, start_bar)] = existing.index
                continue
            index = self._next_scene_index
            self._next_scene_index += 1
            self._operations.append(
                LiveMutationOperation(
                    operation_id=self._next_id(OP_CREATE_SCENE),
                    op=OP_CREATE_SCENE,
                    target={"scene_index": index},
                    arguments={"name": scene_name, "index": index},
                    preconditions=[LivePrecondition("not_recording")],
                    destructive=False,
                    expected_readback={"scene_index": index, "name": scene_name},
                )
            )
            indices[(section_name, start_bar)] = index
        return indices

    def _plan_clips(
        self, tracks: list[dict[str, Any]], scenes: dict[tuple[str, int], int]
    ) -> None:
        beats = self._snapshot.time_signature.beats_per_bar
        for track in tracks:
            for clip in self._candidate.clips_for_part(track["part"]):
                self._plan_one_clip(
                    track,
                    clip,
                    scenes[(clip.section_name, clip.start_bar)],
                    beats,
                )

    def _plan_one_clip(
        self,
        track: dict[str, Any],
        clip: CandidateClip,
        scene_index: int,
        beats: float,
    ) -> None:
        existing = self._snapshot.session_clip_at(track["index"], scene_index)
        clip_name = managed_clip_name(clip.section_name, self._session_clip_id(clip))
        length_beats = round(clip.length_bars * beats, 6)
        notes = [note.to_dict() for note in clip.notes]
        if existing is not None and not existing.is_managed:
            self._conflicts.append(
                LiveConflict(
                    CONFLICT_USER_CLIP,
                    f"トラック {track['index']} シーン {scene_index} にある "
                    f"'{existing.name}' は利用者所有のため置換しません",
                    {
                        "track_index": track["index"],
                        "scene_index": scene_index,
                        "clip_name": existing.name,
                    },
                )
            )
            return
        if existing is None:
            self._operations.append(
                LiveMutationOperation(
                    operation_id=self._next_id(OP_CREATE_SESSION_CLIP),
                    op=OP_CREATE_SESSION_CLIP,
                    target={
                        "track_index": track["index"],
                        "scene_index": scene_index,
                    },
                    arguments={
                        "name": clip_name,
                        "length_beats": length_beats,
                        "looping": True,
                    },
                    preconditions=[
                        LivePrecondition("not_recording"),
                        LivePrecondition(
                            "track_name_at_index",
                            {"track_index": track["index"], "name": track["name"]},
                        ),
                        LivePrecondition(
                            "session_slot_empty",
                            {
                                "track_index": track["index"],
                                "scene_index": scene_index,
                            },
                        ),
                    ],
                    destructive=False,
                    expected_readback={
                        "track_index": track["index"],
                        "scene_index": scene_index,
                        "name": clip_name,
                        "length_beats": length_beats,
                        "looping": True,
                    },
                )
            )
        else:
            self._warnings.append(
                f"KIHACHIクリップ '{existing.name}' のノートを候補 "
                f"{self._short_id} の内容で置き換えます"
            )
        self._operations.append(
            LiveMutationOperation(
                operation_id=self._next_id(OP_REPLACE_CLIP_NOTES),
                op=OP_REPLACE_CLIP_NOTES,
                target={"track_index": track["index"], "scene_index": scene_index},
                arguments={"notes": notes, "clip_name": clip_name},
                preconditions=[
                    LivePrecondition("not_recording"),
                    LivePrecondition(
                        "track_name_at_index",
                        {"track_index": track["index"], "name": track["name"]},
                    ),
                ],
                destructive=existing is not None,
                expected_readback={
                    "track_index": track["index"],
                    "scene_index": scene_index,
                    "note_count": len(notes),
                },
            )
        )

    def _next_id(self, op: str) -> str:
        self._sequence += 1
        return f"{self._sequence:03d}-{op}"


def tone_steps(brief: Any) -> dict[str, int]:
    """The brief's tone controls as recipe steps; absent fields mean no change."""
    steps: dict[str, int] = {}
    for control in ("brightness", "length", "delay"):
        sourced = getattr(brief, f"tone_{control}", None)
        value = getattr(sourced, "value", 0) if sourced is not None else 0
        if value:
            steps[control] = int(value)
    return steps


def _transport_conflicts(snapshot: LiveStateSnapshot) -> list[LiveConflict]:
    conflicts: list[LiveConflict] = []
    if snapshot.is_recording:
        conflicts.append(
            LiveConflict(
                CONFLICT_RECORDING,
                "Ableton Live が録音中です。録音を止めてから適用してください",
            )
        )
    if snapshot.is_playing:
        conflicts.append(
            LiveConflict(
                CONFLICT_PLAYING,
                "Ableton Live が再生中です。再生を止めてから適用してください",
            )
        )
    return conflicts


def _master_parameter_operation(
    operation_id: str, position: int, device: str, setting: Any
) -> LiveMutationOperation:
    arguments: dict[str, Any] = {"device_name": device, "parameter_name": setting.parameter}
    readback: dict[str, Any] = {
        "master": True,
        "device_index": position,
        "parameter_name": setting.parameter,
    }
    if setting.item is not None:
        arguments["item"] = setting.item
        readback["item"] = setting.item
    else:
        arguments["value"] = setting.value
        readback["normalized_value"] = round(setting.expected, 3)
    return LiveMutationOperation(
        operation_id=operation_id,
        op=OP_SET_DEVICE_PARAMETER,
        target={"master": True, "device_index": position},
        arguments=arguments,
        preconditions=[
            LivePrecondition("not_recording"),
            LivePrecondition(
                "device_name_at_index",
                {"master": True, "device_index": position, "name": device},
            ),
        ],
        destructive=False,
        expected_readback=readback,
    )


def _parameter_operation(
    operation_id: str,
    track: dict[str, Any],
    position: int,
    device: str,
    setting: Any,
) -> LiveMutationOperation:
    arguments: dict[str, Any] = {"device_name": device, "parameter_name": setting.parameter}
    readback: dict[str, Any] = {
        "track_index": track["index"],
        "device_index": position,
        "parameter_name": setting.parameter,
    }
    if setting.item is not None:
        arguments["item"] = setting.item
        readback["item"] = setting.item
    else:
        arguments["value"] = setting.value
        # The device reads back to three decimals; Live stores 0.35 as 0.3499.
        readback["normalized_value"] = round(setting.expected, 3)
    return LiveMutationOperation(
        operation_id=operation_id,
        op=OP_SET_DEVICE_PARAMETER,
        target={"track_index": track["index"], "device_index": position},
        arguments=arguments,
        preconditions=[
            LivePrecondition("not_recording"),
            LivePrecondition(
                "track_name_at_index",
                {"track_index": track["index"], "name": track["name"]},
            ),
            LivePrecondition(
                "device_name_at_index",
                {"track_index": track["index"], "device_index": position, "name": device},
            ),
        ],
        destructive=False,
        expected_readback=readback,
    )


def _supports_replace_sample(live_version: str) -> bool:
    head = (live_version or "").split(".", 2)
    try:
        major = int(head[0])
        minor = int(head[1]) if len(head) > 1 else 0
    except ValueError:
        return False
    return major > 12 or (major == 12 and minor >= 4)
