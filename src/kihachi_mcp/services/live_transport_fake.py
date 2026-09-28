"""An in-memory stand-in for the Max for Live device.

This simulator exists so the whole plan, execute, and read back path can be
tested without Ableton Live. It deliberately mirrors the real device's refusal
behaviour: preconditions are re-checked here, not just in the planner, and a
refused operation reports a failure rather than applying something adjacent.

It is not a Live emulator. Passing tests against this simulator do not mean the
Max device has been verified on a real machine.
"""

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from kihachi_mcp.models.live_contract import (
    OP_CREATE_AUDIO_TRACK,
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
    OP_SET_TRACK_COLOR,
    OP_SET_TRACK_MIXER,
    OP_SET_TRACK_NAME,
    SCHEMA_VERSION,
    is_managed_name,
)
from kihachi_mcp.services.live_device_catalog import DEFAULT_SUITE_DEVICES
from kihachi_mcp.services.live_transport import (
    ERROR_DISCONNECTED,
    ERROR_OPERATION_FAILED,
    ERROR_PROTOCOL,
    METHOD_APPLY_OPERATION,
    METHOD_GET_DEVICE_PARAMETERS,
    METHOD_GET_DRUM_RACK_SUMMARY,
    METHOD_GET_STATE,
    METHOD_PING,
    PROTOCOL_NAME,
    PROTOCOL_VERSION,
    LiveTransportError,
)


class FakeLiveOperationRefused(Exception):
    """Raised inside the simulator when a precondition is not satisfied."""


@dataclass
class FakeSessionClip:
    """A Session View clip held by the simulator."""

    name: str
    length_beats: float
    looping: bool = True
    is_midi: bool = True
    notes: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class FakeArrangementClip:
    """An Arrangement View clip held by the simulator."""

    track_index: int
    name: str
    start_beats: float
    length_beats: float
    note_count: int = 0


@dataclass
class FakeLiveSet:
    """Mutable simulated Live Set."""

    live_version: str = "12.1.0"
    set_name: str = "Untitled"
    set_path: str = "/Users/example/Music/Ableton/Untitled/Untitled.als"
    tempo: float = 120.0
    numerator: int = 4
    denominator: int = 4
    is_playing: bool = False
    is_recording: bool = False
    tracks: list[dict[str, Any]] = field(default_factory=list)
    scenes: list[dict[str, Any]] = field(default_factory=list)
    session_clips: dict[tuple[int, int], FakeSessionClip] = field(default_factory=dict)
    arrangement_clips: list[FakeArrangementClip] = field(default_factory=list)
    locators: list[dict[str, Any]] = field(default_factory=list)
    available_devices: list[str] = field(
        default_factory=lambda: list(DEFAULT_SUITE_DEVICES)
    )
    #: Parameter lists by device name, in the shape device_probe saves.
    device_parameters: dict[str, dict[str, Any]] = field(default_factory=dict)
    master: dict[str, Any] = field(default_factory=lambda: {"device_names": []})
    #: Knobs Live moves in whole steps, by (device, parameter): the step count.
    #: Live floors a value set between steps, as Glue Compressor did in 12.4.5.
    stepped: dict[tuple[str, str], int] = field(
        default_factory=lambda: {
            ("Glue Compressor", "Attack"): 6,
            ("Glue Compressor", "Ratio"): 2,
            ("Glue Compressor", "Release"): 6,
        }
    )

    def add_track(
        self, name: str, track_type: str = "midi", color: str = ""
    ) -> dict[str, Any]:
        """Append one track the way Live appends a newly created track."""
        track = {
            "index": len(self.tracks),
            "name": name,
            "track_type": track_type,
            "color": color,
            "is_armed": False,
            "is_frozen": False,
            "device_names": [],
        }
        self.tracks.append(track)
        return track

    def add_scene(self, name: str = "") -> dict[str, Any]:
        """Append one scene."""
        scene = {"index": len(self.scenes), "name": name, "color": ""}
        self.scenes.append(scene)
        return scene


class FakeLiveTransport:
    """A ``LiveTransport`` backed by :class:`FakeLiveSet`."""

    def __init__(
        self,
        live_set: FakeLiveSet | None = None,
        failures: dict[str, tuple[str, str]] | None = None,
        readback_overrides: dict[str, dict[str, Any]] | None = None,
        disconnect_after: int | None = None,
        observed_at: str = "2026-01-01T00:00:00Z",
    ) -> None:
        self.live_set = live_set or FakeLiveSet()
        self.failures = dict(failures or {})
        self.readback_overrides = dict(readback_overrides or {})
        self.disconnect_after = disconnect_after
        self.observed_at = observed_at
        self.applied_operation_ids: list[str] = []
        self.request_count = 0

    def request(self, message: dict[str, Any]) -> dict[str, Any]:
        """Handle one protocol message against the simulated Set."""
        self.request_count += 1
        if message.get("protocol") != PROTOCOL_NAME:
            raise LiveTransportError(ERROR_PROTOCOL, "unknown protocol")
        request_id = str(message.get("request_id") or "")
        method = str(message.get("method") or "")
        payload = message.get("payload")
        payload = payload if isinstance(payload, dict) else {}
        if method == METHOD_PING:
            return self._ok(request_id, self._ping())
        if method == METHOD_GET_STATE:
            return self._ok(request_id, self.snapshot_payload(payload))
        if method == METHOD_GET_DRUM_RACK_SUMMARY:
            return self._ok(request_id, self._drum_rack_summary(payload))
        if method == METHOD_GET_DEVICE_PARAMETERS:
            return self._device_parameters(request_id, payload)
        if method == METHOD_APPLY_OPERATION:
            return self._apply(request_id, payload)
        raise LiveTransportError(ERROR_PROTOCOL, f"unsupported method '{method}'")

    def _ok(self, request_id: str, result: dict[str, Any]) -> dict[str, Any]:
        return {
            "protocol": PROTOCOL_NAME,
            "version": PROTOCOL_VERSION,
            "request_id": request_id,
            "ok": True,
            "result": result,
        }

    def _failure(
        self, request_id: str, code: str, detail: str
    ) -> dict[str, Any]:
        return {
            "protocol": PROTOCOL_NAME,
            "version": PROTOCOL_VERSION,
            "request_id": request_id,
            "ok": False,
            "error": {"code": code, "message": detail},
        }

    def _ping(self) -> dict[str, Any]:
        return {
            "live_version": self.live_set.live_version,
            "protocol_version": PROTOCOL_VERSION,
            "device_version": "kihachi-live-device/0.1.0",
        }

    def _device_parameters(self, request_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Answer from the parameter table real Live gave, per device name."""
        index = int(payload.get("track_index") or 0)
        position = int(payload.get("device_index") or 0)
        track = next((item for item in self.live_set.tracks if item["index"] == index), None)
        names = (track or {}).get("device_names") or []
        if position >= len(names):
            return self._failure(request_id, ERROR_OPERATION_FAILED, f"no device {position}")
        name = names[position]
        known = self.live_set.device_parameters.get(name) or {}
        reply: dict[str, Any] = {
            "device_index": position,
            "device_name": name,
            "class_name": str(known.get("class_name") or ""),
            "parameters": [
                {**item, "value": item.get("default")}
                for item in known.get("parameters") or []
            ],
        }
        if name == "Compressor":
            reply["sidechain"] = {
                "available_types": [item["name"] for item in self.live_set.tracks],
                "input_routing_type": (track.get("sidechains") or {}).get(str(position), "No Input"),
                "input_routing_channel": "Post FX",
            }
        return self._ok(request_id, reply)

    def _drum_rack_summary(self, payload: dict[str, Any]) -> dict[str, Any]:
        index = int(payload.get("track_index") or 0)
        track = next((item for item in self.live_set.tracks if item["index"] == index), None)
        if track is None:
            return {"track_index": index, "track_name": "", "devices": []}
        devices = []
        for device_index, name in enumerate(track.get("device_names") or []):
            devices.append(
                {
                    "device_index": device_index,
                    "name": name,
                    "class_display_name": name,
                    "can_have_drum_pads": name == "Drum Rack",
                    "chain_count": 0,
                    "occupied_pads": list(
                        track.get("occupied_pads") or []
                    ),
                }
            )
        return {
            "track_index": index,
            "track_name": str(track.get("name") or ""),
            "devices": devices,
        }

    def snapshot_payload(self, options: dict[str, Any] | None = None) -> dict[str, Any]:
        """Return the Set as a ``LiveStateSnapshot``-compatible payload.

        ``options`` are the get_state flags, honored the way the Max device
        honors them, so a caller that reads the Set two different ways sees
        two different fingerprints here too.
        """
        live = self.live_set
        options = options or {}
        include_arrangement = options.get("include_arrangement") is not False
        include_session_clips = options.get("include_session_clips") is not False
        count_session_notes = options.get("count_session_notes") is not False
        return {
            "schema_version": SCHEMA_VERSION,
            "live_version": live.live_version,
            "set_name": live.set_name,
            "set_path": live.set_path,
            "tempo": live.tempo,
            "time_signature": {
                "numerator": live.numerator,
                "denominator": live.denominator,
            },
            "is_playing": live.is_playing,
            "is_recording": live.is_recording,
            "observed_at": self.observed_at,
            "tracks": [dict(track) for track in live.tracks],
            "master_device_names": list(live.master["device_names"]),
            "scenes": [dict(scene) for scene in live.scenes],
            "session_clips": [
                {
                    "track_index": track_index,
                    "scene_index": scene_index,
                    "name": clip.name,
                    "length_beats": clip.length_beats,
                    "note_count": len(clip.notes) if count_session_notes else 0,
                    "is_midi": clip.is_midi,
                    "looping": clip.looping,
                }
                for (track_index, scene_index), clip in sorted(
                    live.session_clips.items()
                )
            ]
            if include_session_clips
            else [],
            "arrangement_clips": [
                {
                    "track_index": clip.track_index,
                    "name": clip.name,
                    "start_beats": clip.start_beats,
                    "length_beats": clip.length_beats,
                    "note_count": clip.note_count,
                }
                for clip in live.arrangement_clips
            ]
            if include_arrangement
            else [],
            "devices": [
                {"name": name, "available": True, "category": ""}
                for name in live.available_devices
            ],
        }

    def _apply(self, request_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        operation = payload.get("operation")
        operation = operation if isinstance(operation, dict) else {}
        operation_id = str(operation.get("operation_id") or "")
        if (
            self.disconnect_after is not None
            and len(self.applied_operation_ids) >= self.disconnect_after
        ):
            raise LiveTransportError(
                ERROR_DISCONNECTED, "Live bridge closed the connection"
            )
        if operation_id in self.failures:
            code, detail = self.failures[operation_id]
            return self._failure(request_id, code, detail)
        try:
            self._check_preconditions(operation)
            observed = self._mutate(operation)
        except FakeLiveOperationRefused as exc:
            return self._failure(request_id, ERROR_OPERATION_FAILED, str(exc))
        self.applied_operation_ids.append(operation_id)
        if operation_id in self.readback_overrides:
            observed = dict(self.readback_overrides[operation_id])
        return self._ok(
            request_id,
            {
                "operation_id": operation_id,
                "op": str(operation.get("op") or ""),
                "observed": observed,
            },
        )

    def _check_preconditions(self, operation: dict[str, Any]) -> None:
        live = self.live_set
        for raw in operation.get("preconditions") or []:
            if not isinstance(raw, dict):
                continue
            kind = str(raw.get("kind") or "")
            arguments = raw.get("arguments")
            arguments = arguments if isinstance(arguments, dict) else {}
            if kind == "not_recording" and live.is_recording:
                raise FakeLiveOperationRefused("Live is recording")
            if kind == "transport_stopped" and live.is_playing:
                raise FakeLiveOperationRefused("Live transport is running")
            if kind == "track_name_at_index":
                index = int(arguments.get("track_index") or 0)
                expected = str(arguments.get("name") or "")
                track = self._track(index)
                if track is None or track["name"] != expected:
                    raise FakeLiveOperationRefused(
                        f"track {index} is not '{expected}'"
                    )
            if kind == "track_missing":
                expected = str(arguments.get("name") or "")
                if any(track["name"] == expected for track in live.tracks):
                    raise FakeLiveOperationRefused(
                        f"track '{expected}' already exists"
                    )
            if kind == "scene_name_at_index":
                index = int(arguments.get("scene_index") or 0)
                expected = str(arguments.get("name") or "")
                if index >= len(live.scenes) or live.scenes[index]["name"] != expected:
                    raise FakeLiveOperationRefused(
                        f"scene {index} is not '{expected}'"
                    )
            if kind == "session_slot_empty":
                slot = (
                    int(arguments.get("track_index") or 0),
                    int(arguments.get("scene_index") or 0),
                )
                if slot in live.session_clips:
                    raise FakeLiveOperationRefused(
                        f"session slot {slot} already holds a clip"
                    )
            if kind == "clip_is_managed":
                slot = (
                    int(arguments.get("track_index") or 0),
                    int(arguments.get("scene_index") or 0),
                )
                clip = live.session_clips.get(slot)
                if clip is None or not is_managed_name(clip.name):
                    raise FakeLiveOperationRefused(
                        f"session slot {slot} is not KIHACHI owned"
                    )
            if kind == "device_available":
                name = str(arguments.get("device_name") or "")
                if name not in live.available_devices:
                    raise FakeLiveOperationRefused(
                        f"device '{name}' is not available in this Live installation"
                    )
            if kind == "arrangement_range_free":
                self._require_free_range(arguments)
            if kind == "device_name_at_index":
                track = (
                    live.master
                    if arguments.get("master") is True
                    else self._track(int(arguments.get("track_index") or 0))
                )
                names = list(track["device_names"]) if track else []
                position = int(arguments.get("device_index") or 0)
                expected = str(arguments.get("name") or "")
                if position >= len(names) or names[position] != expected:
                    raise FakeLiveOperationRefused(f"device {position} is not '{expected}'")

    def _require_free_range(self, arguments: dict[str, Any]) -> None:
        track_index = int(arguments.get("track_index") or 0)
        start = float(arguments.get("start_beats") or 0.0)
        end = start + float(arguments.get("length_beats") or 0.0)
        for clip in self.live_set.arrangement_clips:
            if clip.track_index != track_index:
                continue
            if start < clip.start_beats + clip.length_beats and clip.start_beats < end:
                raise FakeLiveOperationRefused(
                    f"arrangement range [{start}, {end}) on track "
                    f"{track_index} overlaps '{clip.name}'"
                )

    def _track(self, index: int) -> dict[str, Any] | None:
        for track in self.live_set.tracks:
            if track["index"] == index:
                return track
        return None

    def _owner(self, target: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        """The master or the addressed track, and how a readback names it."""
        if target.get("master") is True:
            return self.live_set.master, {"master": True}
        track = self._require_track(int(target.get("track_index") or 0))
        return track, {"track_index": track["index"], "master": False}

    def _require_track(self, index: int) -> dict[str, Any]:
        track = self._track(index)
        if track is None:
            raise FakeLiveOperationRefused(f"track {index} does not exist")
        return track

    def _mutate(self, operation: dict[str, Any]) -> dict[str, Any]:
        live = self.live_set
        op = str(operation.get("op") or "")
        target = operation.get("target")
        target = target if isinstance(target, dict) else {}
        arguments = operation.get("arguments")
        arguments = arguments if isinstance(arguments, dict) else {}

        if op == OP_SET_TEMPO:
            live.tempo = float(arguments.get("tempo") or live.tempo)
            return {"tempo": live.tempo}

        if op in {OP_CREATE_MIDI_TRACK, OP_CREATE_AUDIO_TRACK}:
            track_type = "midi" if op == OP_CREATE_MIDI_TRACK else "audio"
            track = live.add_track(
                name=str(arguments.get("name") or ""),
                track_type=track_type,
                color=str(arguments.get("color") or ""),
            )
            return {
                "track_index": track["index"],
                "name": track["name"],
                "track_type": track["track_type"],
            }

        if op == OP_SET_TRACK_NAME:
            track = self._require_track(int(target.get("track_index") or 0))
            track["name"] = str(arguments.get("name") or "")
            return {"track_index": track["index"], "name": track["name"]}

        if op == OP_SET_TRACK_COLOR:
            track = self._require_track(int(target.get("track_index") or 0))
            track["color"] = str(arguments.get("color") or "")
            return {"track_index": track["index"], "color": track["color"]}

        if op == OP_CREATE_SCENE:
            scene = live.add_scene(name=str(arguments.get("name") or ""))
            return {"scene_index": scene["index"], "name": scene["name"]}

        if op == OP_CREATE_SESSION_CLIP:
            return self._create_session_clip(target, arguments)

        if op == OP_REPLACE_CLIP_NOTES:
            return self._replace_clip_notes(target, arguments)

        if op == OP_LOAD_LIVE_DEVICE:
            track, owner = self._owner(target)
            device_name = str(arguments.get("device_name") or "")
            track["device_names"].append(device_name)
            return {
                **owner,
                "device_name": device_name,
                "device_index": len(track["device_names"]) - 1,
            }

        if op == OP_LOAD_DRUM_PAD_SAMPLE:
            track = self._require_track(int(target.get("track_index") or 0))
            note = int(arguments.get("note") or 0)
            path = str(arguments.get("sample_path") or "")
            pads = track.setdefault("occupied_pads", [])
            if not any(int(pad.get("note") or 0) == note for pad in pads):
                pads.append({"note": note, "name": Path(path).stem, "chain_count": 1})
            return {
                "track_index": track["index"],
                "note": note,
                "occupied": True,
                "already_occupied": False,
                "sample_path": path,
            }

        if op == OP_CREATE_LOCATOR:
            locator = {
                "name": str(arguments.get("name") or ""),
                "beats": float(arguments.get("beats") or 0.0),
            }
            live.locators.append(locator)
            return dict(locator)

        if op == OP_PLACE_ARRANGEMENT_CLIP:
            return self._place_arrangement_clip(target, arguments)

        if op == OP_SET_SIDECHAIN_SOURCE:
            track, owner = self._owner(target)
            position = int(target.get("device_index") or 0)
            names = track["device_names"]
            if position >= len(names) or names[position] != arguments.get("device_name"):
                raise FakeLiveOperationRefused(f"device {position} is not '{arguments.get('device_name')}'")
            source = str(arguments.get("source_name") or "")
            if not any(item["name"] == source for item in live.tracks):
                raise FakeLiveOperationRefused(f"'{source}' is not offered as a sidechain source")
            track.setdefault("sidechains", {})[str(position)] = source
            return {
                **owner,
                "device_index": position,
                "input_routing_type": source,
                "input_routing_channel": "Post FX",
            }

        if op == OP_SET_TRACK_MIXER:
            track = self._require_track(int(target.get("track_index") or 0))
            volume_db = float(arguments.get("volume_db"))
            panning = float(arguments.get("panning"))
            if volume_db > 6 or not -1 <= panning <= 1:
                raise FakeLiveOperationRefused("volume_db must be at most +6 dB and panning within -1..1")
            track["mixer"] = {"volume_db": round(volume_db, 1), "panning": round(panning, 2)}
            return {"track_index": track["index"], **track["mixer"]}

        if op == OP_SET_DEVICE_PARAMETER:
            # The simulator knows no parameter lists: it records what was set,
            # keyed by device position and parameter name, and reads it back.
            track, owner = self._owner(target)
            position = int(target.get("device_index") or 0)
            names = track["device_names"]
            if position >= len(names) or names[position] != arguments.get("device_name"):
                raise FakeLiveOperationRefused(f"device {position} is not '{arguments.get('device_name')}'")
            store = track.setdefault("device_parameters", {}).setdefault(str(position), {})
            name = str(arguments.get("parameter_name") or "")
            readback: dict[str, Any] = {
                **owner,
                "device_index": position,
                "parameter_name": name,
            }
            if arguments.get("item"):
                store[name] = str(arguments["item"])
                readback["item"] = store[name]
                readback["normalized_value"] = 0.0
            else:
                amount = float(arguments.get("value") or 0.0)
                span = live.stepped.get((str(arguments.get("device_name")), name))
                if span:
                    amount = math.floor(amount * span + 1e-9) / span
                store[name] = round(amount, 3)
                readback["normalized_value"] = store[name]
            return readback

        raise FakeLiveOperationRefused(f"simulator cannot apply '{op}'")

    def _create_session_clip(
        self, target: dict[str, Any], arguments: dict[str, Any]
    ) -> dict[str, Any]:
        track_index = int(target.get("track_index") or 0)
        scene_index = int(target.get("scene_index") or 0)
        self._require_track(track_index)
        if scene_index >= len(self.live_set.scenes):
            raise FakeLiveOperationRefused(f"scene {scene_index} does not exist")
        slot = (track_index, scene_index)
        if slot in self.live_set.session_clips:
            raise FakeLiveOperationRefused(f"session slot {slot} is occupied")
        clip = FakeSessionClip(
            name=str(arguments.get("name") or ""),
            length_beats=float(arguments.get("length_beats") or 0.0),
            looping=bool(arguments.get("looping", True)),
        )
        self.live_set.session_clips[slot] = clip
        return {
            "track_index": track_index,
            "scene_index": scene_index,
            "name": clip.name,
            "length_beats": clip.length_beats,
            "looping": clip.looping,
        }

    def _replace_clip_notes(
        self, target: dict[str, Any], arguments: dict[str, Any]
    ) -> dict[str, Any]:
        track_index = int(target.get("track_index") or 0)
        scene_index = int(target.get("scene_index") or 0)
        slot = (track_index, scene_index)
        clip = self.live_set.session_clips.get(slot)
        if clip is None:
            raise FakeLiveOperationRefused(f"session slot {slot} holds no clip")
        notes = [note for note in arguments.get("notes") or [] if isinstance(note, dict)]
        clip.notes = [dict(note) for note in notes]
        return {
            "track_index": track_index,
            "scene_index": scene_index,
            "note_count": len(clip.notes),
        }

    def _place_arrangement_clip(
        self, target: dict[str, Any], arguments: dict[str, Any]
    ) -> dict[str, Any]:
        track_index = int(target.get("track_index") or 0)
        self._require_track(track_index)
        source_scene = int(target.get("scene_index") or 0)
        source = self.live_set.session_clips.get((track_index, source_scene))
        if source is None:
            raise FakeLiveOperationRefused(
                f"session slot ({track_index}, {source_scene}) holds no source clip"
            )
        clip = FakeArrangementClip(
            track_index=track_index,
            name=str(arguments.get("name") or source.name),
            start_beats=float(arguments.get("start_beats") or 0.0),
            length_beats=float(arguments.get("length_beats") or 0.0),
            note_count=len(source.notes),
        )
        self.live_set.arrangement_clips.append(clip)
        return {
            "track_index": clip.track_index,
            "name": clip.name,
            "start_beats": clip.start_beats,
            "length_beats": clip.length_beats,
            "note_count": clip.note_count,
        }
