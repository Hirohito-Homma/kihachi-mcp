"""Secure sample_id import. The fake client is not a live meter."""

from __future__ import annotations

import json
import wave
from pathlib import Path
from typing import Any

import pytest

from kihachi_mcp.gateway.client import configure_ableton_client
from kihachi_mcp.gateway.samples.index import index_library, resolve_import_target
from kihachi_mcp.gateway.samples.load import (
    probe_sample_audio_clip,
    sample_loading_capabilities,
)
from kihachi_mcp.models.songspec import SongSpec


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


class ProbeLive:
    def __init__(self, *, playing: bool = False, stop_sticks: bool = False) -> None:
        self.playing = playing
        self.stop_sticks = stop_sticks
        self.calls: list[str] = []
        self.tracks: list[dict[str, Any]] = []
        self.clips: dict[int, dict[str, Any]] = {}
        self.volume = 0.85

    def call(self, command: str, timeout: float, **params: Any) -> dict[str, Any]:
        self.calls.append(command)
        if command == "get_state":
            return {"is_playing": self.playing, "tracks": list(self.tracks)}
        if command == "set_transport":
            if not self.stop_sticks:
                self.playing = params["action"] == "play"
            return {"is_playing": self.playing}
        if command == "create_track":
            track = {"index": len(self.tracks), "name": params["name"]}
            self.tracks.append(track)
            return {"index": track["index"], "name": track["name"]}
        if command == "get_audio_clip_paths":
            clip = self.clips.get(params["track_index"])
            return {"clips": [clip] if clip else []}
        if command == "import_audio_clip":
            clip = {
                "location": "session",
                "index": 0,
                "name": params["name"],
                "file_path": params["file_path"],
                "warping": False,
                "sample_length": 800,
                "sample_rate": 44100.0,
            }
            self.clips[params["track_index"]] = clip
            return {"clip": params["name"], "file_path": params["file_path"], "length": 1.0}
        if command == "get_mix_snapshot":
            return {
                "tracks": [
                    {"index": track["index"], "volume": self.volume, "output_meter_level": 0.4 if self.playing else 0.0}
                    for track in self.tracks
                ],
                "master": {"output_meter_level": 0.5 if self.playing else 0.0},
            }
        if command == "set_track_volume":
            self.volume = params["volume"]
            return {"volume": self.volume}
        if command == "fire_clip":
            self.playing = True
            return {"fired": True}
        if command == "stop_track_clips":
            self.playing = False
            return {"stopped": True}
        raise AssertionError(command)


def _indexed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str = "kick.wav") -> tuple[Path, str]:
    root = tmp_path / "splice"
    audio = root / name
    _wav(audio)
    _isolate(monkeypatch, tmp_path, root)
    assert index_library()["indexed_count"] == 1
    sample_id = json.loads((tmp_path / "index.json").read_text(encoding="utf-8"))["samples"][0]["sample_id"]
    return audio, sample_id


def test_resolution_stays_inside_the_root_and_rejects_escape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    audio, sample_id = _indexed(tmp_path, monkeypatch)
    resolved = resolve_import_target(sample_id)
    assert resolved["ok"] is True
    assert resolved["path"] == audio.resolve()
    assert resolved["path"].is_relative_to(tmp_path / "splice")
    outside = tmp_path / "secret.wav"
    _wav(outside)
    link = tmp_path / "splice" / "escape.wav"
    link.symlink_to(outside)
    index = json.loads((tmp_path / "index.json").read_text(encoding="utf-8"))
    index["samples"].append({**index["samples"][0], "sample_id": "link", "path": str(link)})
    index["samples"].append({**index["samples"][0], "sample_id": "dot", "path": str(tmp_path / "splice" / ".." / "secret.wav")})
    index["samples"].append({**index["samples"][0], "sample_id": "gone", "path": str(tmp_path / "splice" / "missing.wav")})
    index["samples"].append({**index["samples"][0], "sample_id": "mp3", "path": str(tmp_path / "splice" / "tone.mp3")})
    (tmp_path / "splice" / "tone.mp3").write_bytes(b"nope")
    (tmp_path / "index.json").write_text(json.dumps(index), encoding="utf-8")
    assert resolve_import_target("link")["error"] == "rejected"
    assert resolve_import_target("dot")["error"] == "rejected"
    assert resolve_import_target("gone")["error"] == "missing"
    assert resolve_import_target("mp3")["error"] == "unsupported_format"
    assert resolve_import_target("nope")["error"] == "not_found"


def test_probe_is_confirm_gated_and_strips_the_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _audio, sample_id = _indexed(tmp_path, monkeypatch)
    held = probe_sample_audio_clip(sample_id, confirm=False)
    assert held["status"] == "confirmation_required"
    assert held["live_changed"] is False
    refused = probe_sample_audio_clip(sample_id, confirm=True, selection_mode="assisted")
    assert refused["status"] == "rejected"
    fake = ProbeLive()
    configure_ableton_client(fake)
    result = probe_sample_audio_clip(sample_id, confirm=True)
    dumped = json.dumps(result)
    assert str(tmp_path) not in dumped
    assert "file_path" not in dumped
    assert result["sample"]["sample_id"] == sample_id
    assert result["sample"]["channels"] is None
    assert result["assignment"]["actual_source_type"] == "audio"
    assert result["assignment"]["preferred_source_type"] == "sample_instrument"
    assert result["assignment"]["selection_mode"] == "technical_probe"
    assert result["playback"]["stopped"] is True
    assert "copy_session_clip_to_arrangement" not in fake.calls
    again = probe_sample_audio_clip(sample_id, confirm=True)
    assert again["duplicate"] is True
    assert fake.calls.count("import_audio_clip") == 1
    configure_ableton_client(None)
    spec = SongSpec.from_dict({"genre": "house", "tempo": 110, "key": "D# minor", "bars": 32, "length_minutes": 1, "tracks": ["KICK"]})
    assert "sample_id" not in spec.to_dict()


def test_probe_does_not_play_when_stop_is_not_guaranteed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _audio, sample_id = _indexed(tmp_path, monkeypatch)
    fake = ProbeLive(playing=True, stop_sticks=True)
    configure_ableton_client(fake)
    result = probe_sample_audio_clip(sample_id, confirm=True)
    assert result["stopped"] is False
    assert "fire_clip" not in fake.calls
    assert "create_track" not in fake.calls
    configure_ableton_client(None)


def test_capabilities_do_not_call_live() -> None:
    fake = ProbeLive()
    configure_ableton_client(fake)
    report = sample_loading_capabilities()
    assert fake.calls == []
    assert report["audio_clip_import"] == "SUPPORTED"
    assert report["simpler_sample_load"] == "IMPLEMENTED"
    assert report["live_acceptance"] == "NOT RUN"
    assert report["live_changed"] is False
    configure_ableton_client(None)
