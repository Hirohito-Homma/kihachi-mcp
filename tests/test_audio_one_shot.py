"""Unwarped audio_one_shot placement against a fake client, not a live song."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import types
import wave
from pathlib import Path
from typing import Any

import pytest

from kihachi_mcp.gateway.client import AbletonCommandError, configure_ableton_client
from kihachi_mcp.gateway.samples.index import index_library
from kihachi_mcp.gateway.samples.oneshot import place_audio_one_shot
from kihachi_mcp.gateway.samples.place import place_indexed_sample
from kihachi_mcp.gateway.samples.tools import place_audio_one_shot_sample

_REMOTE = Path(
    os.environ.get(
        "ABLETONGPT_REMOTE_SCRIPT_PATH",
        "/Users/user/MusicAI/abletongpt/ableton_remote_script/AbletonGPT/__init__.py",
    )
)


def _wav(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "w") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(44100)
        handle.writeframes(b"\x00\x00" * 800)


def _isolate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, root: Path) -> None:
    monkeypatch.setenv("KIHACHI_SAMPLE_LIBRARY_PATHS", str(root))
    monkeypatch.setenv("KIHACHI_SAMPLE_INDEX_PATH", str(tmp_path / "index.json"))
    monkeypatch.setenv("KIHACHI_SAMPLE_MANIFEST_DIR", str(tmp_path / "manifests"))
    monkeypatch.setenv("KIHACHI_SAMPLE_SELECTION_PATH", str(tmp_path / "selections.json"))


class OneShotLive:
    def __init__(
        self,
        *,
        warping: bool = False,
        start_offset: float = 0.0,
        end_delta: float | None = 0.5,
        fail: str | None = None,
        old_rejection: bool = False,
    ) -> None:
        self.warping = warping
        self.start_offset = start_offset
        self.end_delta = end_delta
        self.fail = fail
        self.old_rejection = old_rejection
        self.calls: list[str] = []
        self.params: list[dict[str, Any]] = []
        self.arrangement: list[dict[str, Any]] = []
        self.session: dict[str, Any] | None = None
        self.track_index = 3

    def call(self, command: str, timeout: float, **params: Any) -> dict[str, Any]:
        self.calls.append(command)
        self.params.append({"command": command, **params})
        if command == "get_state":
            return {
                "is_playing": False,
                "tempo": 110.0,
                "tracks": [{"index": self.track_index, "name": "Mutant Dub Circuit - KICK AUDIO PROBE"}],
            }
        if command == "get_audio_clip_paths" and params["view"] == "session":
            return {"clips": [] if self.session is None else [self.session]}
        if command == "get_audio_clip_paths":
            return {"clips": list(self.arrangement)}
        if command == "copy_session_clip_to_arrangement":
            if self.old_rejection:
                raise AbletonCommandError(
                    "collision-safe Arrangement copy requires warped audio: probe"
                )
            if params.get("placement_mode") != "audio_one_shot":
                raise AbletonCommandError("collision-safe Arrangement copy requires warped audio: probe")
            start = float(params["destination_time_beats"]) + self.start_offset
            end = start if self.end_delta is None else start + self.end_delta
            clip = {
                "location": "arrangement",
                "name": "T_TSOTT2_kick_oneshot_funky.wav",
                "start_time": start,
                "end_time": end,
                "warping": self.warping,
                "sample_length": 20306,
                "sample_rate": 44100.0,
                "file_path": params["file_path"],
            }
            self.arrangement.append(clip)
            if self.fail:
                raise AbletonCommandError(self.fail)
            return {
                "placement_mode": "audio_one_shot",
                "start_time": start,
                "end_time": end,
                "warping": self.warping,
                "is_audio_clip": True,
                "rollback_supported": False,
            }
        raise AssertionError(command)


def _ready(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, live: OneShotLive) -> str:
    root = tmp_path / "splice"
    audio = root / "T_TSOTT2_kick_oneshot_funky.wav"
    _wav(audio)
    _isolate(monkeypatch, tmp_path, root)
    assert index_library()["indexed_count"] == 1
    sample_id = json.loads((tmp_path / "index.json").read_text(encoding="utf-8"))["samples"][0]["sample_id"]
    live.session = {
        "location": "session",
        "index": 0,
        "name": audio.name,
        "file_path": str(audio.resolve()),
        "warping": live.warping,
        "sample_length": 20306,
        "sample_rate": 44100.0,
    }
    configure_ableton_client(live)
    return sample_id


def _place(sample_id: str, **overrides: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "target_track": "Mutant Dub Circuit - KICK AUDIO PROBE",
        "beat": 0.0,
        "source_midi_track": "Mutant Dub Circuit - KICK",
        "source_note_index": 0,
        "source_pitch": 36,
        "source_velocity": 118,
        "confirm": True,
        "selection_mode": "technical_probe",
    }
    fields.update(overrides)
    return place_audio_one_shot(sample_id, **fields)


class NormalLive:
    """The existing copy path. Unwarped audio never reaches the Live call."""

    def __init__(self, *, warping: bool) -> None:
        self.warping = warping
        self.calls: list[str] = []
        self.copy_params: list[dict[str, Any]] = []
        self.tracks: list[dict[str, Any]] = []
        self.session: dict[int, list[dict[str, Any]]] = {}

    def call(self, command: str, timeout: float, **params: Any) -> dict[str, Any]:
        self.calls.append(command)
        if command == "get_state":
            return {"is_playing": False, "tracks": list(self.tracks), "tempo": 110.0}
        if command == "create_track":
            track = {"index": len(self.tracks), "name": params["name"]}
            self.tracks.append(track)
            return track
        if command == "get_audio_clip_paths":
            return {"clips": list(self.session.get(params["track_index"], []))}
        if command == "import_audio_clip":
            clip = {
                "location": "session",
                "index": 0,
                "warping": self.warping,
                "file_path": params["file_path"],
            }
            self.session.setdefault(params["track_index"], []).append(clip)
            return {"clip_index": 0}
        if command == "copy_session_clip_to_arrangement":
            self.copy_params.append(params)
            return {"is_audio_clip": True, "warping": True}
        raise AssertionError(command)


def test_one_shot_allows_unwarped_and_normal_copy_still_rejects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    live = OneShotLive()
    sample_id = _ready(tmp_path, monkeypatch, live)
    placed = _place(sample_id)
    assert placed["ok"] is True
    assert placed["warping"] is False
    assert placed["rollback_supported"] is False
    copy = next(item for item in live.params if item["command"] == "copy_session_clip_to_arrangement")
    assert copy["placement_mode"] == "audio_one_shot"
    assert "warp" not in copy
    configure_ableton_client(None)

    unwarped = NormalLive(warping=False)
    configure_ableton_client(unwarped)
    blocked = place_indexed_sample(sample_id, title="Mutant Dub Circuit", role="kick", confirm=True)
    assert blocked["arrangement"] == "FAILED"
    assert "copy_session_clip_to_arrangement" not in unwarped.calls
    warped = NormalLive(warping=True)
    configure_ableton_client(warped)
    normal = place_indexed_sample(sample_id, title="Mutant Dub Circuit", role="kick", confirm=True)
    assert normal["arrangement"] == "SUCCESS"
    assert warped.copy_params
    assert "placement_mode" not in warped.copy_params[0]
    configure_ableton_client(None)


def test_start_tolerance_end_readback_and_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    edge = OneShotLive(start_offset=0.000001)
    sample_id = _ready(tmp_path, monkeypatch, edge)
    accepted = _place(sample_id)
    assert accepted["ok"] is True
    assert abs(accepted["actual_start"] - 0.0) <= 0.000001
    assert accepted["actual_duration"] == accepted["actual_end"] - accepted["actual_start"]
    diagnostic = (20306 / 44100) * (110 / 60)
    assert accepted["diagnostic_duration_beats"] == pytest.approx(diagnostic)
    assert accepted["duration_difference"] == pytest.approx(accepted["actual_duration"] - diagnostic)

    mismatch = OneShotLive(start_offset=0.000002)
    sample_id = _ready(tmp_path, monkeypatch, mismatch)
    refused = _place(sample_id)
    assert refused["ok"] is False
    assert refused["error"] == "start_mismatch"
    assert refused["rollback_supported"] is False

    flat = OneShotLive(end_delta=0.0)
    sample_id = _ready(tmp_path, monkeypatch, flat)
    empty = _place(sample_id)
    assert empty["ok"] is False
    assert empty["error"] == "non_positive_duration"
    assert empty["rollback_supported"] is False
    configure_ableton_client(None)


def test_sample_id_path_escape_identity_confirm_and_partial_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    live = OneShotLive()
    sample_id = _ready(tmp_path, monkeypatch, live)
    dry = _place(sample_id, dry_run=True, confirm=True)
    assert dry["status"] == "dry_run"
    assert live.calls == []
    held = _place(sample_id, confirm=False)
    assert held["status"] == "confirmation_required"
    assert live.calls == []
    missing = _place("../secret")
    assert missing["status"] == "rejected"
    assert live.calls == []

    outside = tmp_path / "secret.wav"
    _wav(outside)
    link = tmp_path / "splice" / "escape.wav"
    link.symlink_to(outside)
    index = json.loads((tmp_path / "index.json").read_text(encoding="utf-8"))
    index["samples"].append({**index["samples"][0], "sample_id": "linked", "path": str(link)})
    (tmp_path / "index.json").write_text(json.dumps(index), encoding="utf-8")
    escaped = _place("linked")
    assert escaped["status"] == "rejected"
    assert "copy_session_clip_to_arrangement" not in live.calls

    first = _place(sample_id)
    assert first["ok"] is True
    assert "file_path" not in json.dumps(first)
    assert str(tmp_path) not in json.dumps(first)
    again = _place(sample_id)
    assert again["status"] == "skipped"
    assert again["idempotency"] == "verified"
    assert live.calls.count("copy_session_clip_to_arrangement") == 1

    partial = OneShotLive(fail="Live did not create exactly one Arrangement clip")
    sample_id = _ready(tmp_path, monkeypatch, partial)
    failed = _place(sample_id)
    assert failed["ok"] is False
    assert failed["partial"] is True
    assert failed["rollback_supported"] is False
    assert "delete" not in "".join(partial.calls)

    old = OneShotLive(old_rejection=True)
    sample_id = _ready(tmp_path, monkeypatch, old)
    rejected = _place(sample_id)
    assert rejected["status"] == "validation_rejected"
    assert rejected["script_loaded"] is False
    assert rejected["live_accepted_unwarped"] is False
    assert old.calls.count("copy_session_clip_to_arrangement") == 1
    public = place_audio_one_shot_sample(
        sample_id,
        target_track="Mutant Dub Circuit - KICK AUDIO PROBE",
        beat=0.0,
        source_midi_track="Mutant Dub Circuit - KICK",
        source_note_index=0,
        source_pitch=36,
        source_velocity=118,
        confirm=False,
    )
    assert "path" not in json.dumps(public)
    configure_ableton_client(None)


def _remote_surface() -> Any:
    live = types.ModuleType("Live")
    framework = types.ModuleType("_Framework")
    control = types.ModuleType("_Framework.ControlSurface")

    class ControlSurface:
        def log_message(self, *_args: object) -> None:
            return None

    control.ControlSurface = ControlSurface
    saved = {name: sys.modules.get(name) for name in ("Live", "_Framework", "_Framework.ControlSurface")}
    sys.modules["Live"] = live
    sys.modules["_Framework"] = framework
    sys.modules["_Framework.ControlSurface"] = control
    spec = importlib.util.spec_from_file_location("abletongpt_one_shot_under_test", _REMOTE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for name, previous in saved.items():
        if previous is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = previous
    surface = module.AbletonGPTControlSurface.__new__(module.AbletonGPTControlSurface)
    return surface


class _Clip:
    def __init__(self, *, warping: bool, length: float, start: float = 0.0, end: float = 1.0) -> None:
        self.is_audio_clip = True
        self.warping = warping
        self.length = length
        self.start_time = start
        self.end_time = end
        self.name = "kick"
        self.file_path = "/tmp/kick.wav"


class _Track:
    def __init__(self, shift: float = 0.0, end_delta: float = 0.5) -> None:
        self.name = "probe"
        self.is_frozen = False
        self.arrangement_clips: list[_Clip] = []
        self.shift = shift
        self.end_delta = end_delta
        self.duplicated = 0

    def duplicate_clip_to_arrangement(self, clip: _Clip, destination: float) -> None:
        self.duplicated += 1
        start = float(destination) + self.shift
        self.arrangement_clips.append(
            _Clip(warping=clip.warping, length=clip.length, start=start, end=start + self.end_delta)
        )


@pytest.mark.skipif(
    os.environ.get("KIHACHI_RUN_REMOTE_SCRIPT_TESTS") != "1" or not _REMOTE.is_file(),
    reason="External Remote Script test requires KIHACHI_RUN_REMOTE_SCRIPT_TESTS=1 and existing AbletonGPT remote script",
)
def test_remote_script_one_shot_is_separate_from_the_normal_rejection() -> None:
    surface = _remote_surface()
    track = _Track()
    source = _Clip(warping=False, length=0.8441646894771895)
    with pytest.raises(ValueError, match="warped audio"):
        surface._prepare_arrangement_copy(0, track, source, 0.0)
    assert track.duplicated == 0
    warped = _Clip(warping=True, length=8.0)
    prepared = surface._prepare_arrangement_copy(0, track, warped, 0.0)
    assert prepared["end_time"] == 8.0
    placed = surface._copy_audio_one_shot(track, source, 0.0, "", "")
    assert track.duplicated == 1
    assert placed["duration"] == placed["end_time"] - placed["start_time"]
    assert placed["warping"] is False
    assert placed["rollback_supported"] is False
    again = surface._copy_audio_one_shot(track, source, 0.0, "", "")
    assert track.duplicated == 1
    assert again["skipped_existing"] is True
    shifted = _Track(shift=0.01)
    with pytest.raises(RuntimeError, match="unexpected time"):
        surface._copy_audio_one_shot(shifted, source, 0.0, "", "")
    flat = _Track(end_delta=0.0)
    with pytest.raises(RuntimeError, match="positive duration"):
        surface._copy_audio_one_shot(flat, source, 0.0, "", "")
