"""Turn a MIDI candidate into a Live plan without regenerating notes."""

import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from kihachi_mcp.models.live_contract import (
    OP_CREATE_MIDI_TRACK,
    OP_CREATE_SCENE,
    OP_CREATE_SESSION_CLIP,
    OP_LOAD_DRUM_PAD_SAMPLE,
    OP_LOAD_LIVE_DEVICE,
    OP_REPLACE_CLIP_NOTES,
    OP_SET_TEMPO,
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
from kihachi_mcp.models.production_brief import STUDIO_PARTS
from kihachi_mcp.services import live_device_catalog
from kihachi_mcp.services.drum_samples import HAT_NOTE, KICK_NOTE, sample_path_for_note
from kihachi_mcp.services.live_approval_gate import APPROVAL_TTL_SECONDS

CONFLICT_RECORDING = "live_is_recording"
CONFLICT_PLAYING = "live_is_playing"
CONFLICT_USER_CLIP = "user_owned_clip"

_TRACK_COLORS = {"Kick": "2", "Hats": "20", "Bass": "14", "Stab": "9"}
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
    ) -> LiveMutationPlan:
        """Return an inert plan. Conflicts block execution."""
        builder = _Builder(candidate, snapshot, change_tempo)
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


class _Builder:
    def __init__(
        self,
        candidate: MidiCandidate | AppliedTracks,
        snapshot: LiveStateSnapshot,
        change_tempo: bool,
    ) -> None:
        self._candidate = candidate
        self._snapshot = snapshot
        self._change_tempo = change_tempo
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
        self._plan_devices(tracks)
        self._plan_drum_samples(tracks)
        scenes = self._plan_scenes()
        self._plan_clips(tracks, scenes)
        if self._conflicts:
            return [], self._conflicts, self._warnings
        return self._operations, self._conflicts, self._warnings

    def build_drum_samples_only(
        self,
    ) -> tuple[list[LiveMutationOperation], list[LiveConflict], list[str]]:
        self._guard_transport()
        if self._conflicts:
            return [], self._conflicts, self._warnings
        tracks: list[dict[str, Any]] = []
        for part in ("Kick", "Hats"):
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

    def _guard_transport(self) -> None:
        if self._snapshot.is_recording:
            self._conflicts.append(
                LiveConflict(
                    CONFLICT_RECORDING,
                    "Ableton Live が録音中です。録音を止めてから適用してください",
                )
            )
        if self._snapshot.is_playing:
            self._conflicts.append(
                LiveConflict(
                    CONFLICT_PLAYING,
                    "Ableton Live が再生中です。再生を止めてから適用してください",
                )
            )

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
        for part in STUDIO_PARTS:
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
        for track in tracks:
            if track["device_names"]:
                continue
            device_name = live_device_catalog.suggest_instrument(track["part"])
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

    def _plan_drum_samples(self, tracks: list[dict[str, Any]]) -> None:
        if not _supports_replace_sample(self._snapshot.live_version):
            self._warnings.append(
                "Drum Rack へのサンプル自動配置は Live 12.4 以降が必要です。"
                "Kick/Hats のパッドは手動で入れてください"
            )
            return
        for track in tracks:
            if track["part"] not in {"Kick", "Hats"}:
                continue
            if "Drum Rack" not in track["device_names"]:
                continue
            for note in self._candidate.used_pitches(track["part"]):
                path = sample_path_for_note(note)
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
        for section_name, start_bar in keys:
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
        managed_id = canonical_hash(
            {
                "candidate": self._candidate.candidate_id,
                "part": clip.part,
                "section": clip.section_name,
                "start_bar": clip.start_bar,
            }
        )[:8]
        clip_name = managed_clip_name(clip.section_name, managed_id)
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


def _supports_replace_sample(live_version: str) -> bool:
    head = (live_version or "").split(".", 2)
    try:
        major = int(head[0])
        minor = int(head[1]) if len(head) > 1 else 0
    except ValueError:
        return False
    return major > 12 or (major == 12 and minor >= 4)
