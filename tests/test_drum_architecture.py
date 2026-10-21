"""Gateway checks for the drum architecture. The fake never contacts Live."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from kihachi_mcp.gateway import drum_architecture as drums


class FakeLive:
    def __init__(self, responses: dict[str, Any] | None = None, error: str | None = None) -> None:
        self.responses = responses or {}
        self.error = error
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, command: str, *, timeout: float, **params: Any) -> tuple[dict[str, Any] | None, str | None]:
        self.calls.append((command, params))
        if self.error:
            return None, self.error
        payload = self.responses.get(command)
        if payload is None:
            return None, "missing fake response"
        return dict(payload), None


@pytest.fixture
def wav(tmp_path: Path) -> str:
    path = tmp_path / "kick.wav"
    path.write_bytes(b"RIFF")
    return str(path)


def _patch(monkeypatch: pytest.MonkeyPatch, live: FakeLive) -> None:
    monkeypatch.setattr(drums, "invoke", live)


def test_simpler_load_requires_confirm_and_rejects_a_bad_path(monkeypatch: pytest.MonkeyPatch, wav: str) -> None:
    live = FakeLive()
    _patch(monkeypatch, live)
    held = drums.load_sample_to_simpler(1, wav)
    preview = drums.load_sample_to_simpler(1, wav, confirm=True, dry_run=True)
    missing = drums.load_sample_to_simpler(1, str(Path(wav).with_name("gone.wav")), confirm=True)
    relative = drums.load_sample_to_simpler(1, "kick.wav", confirm=True)
    bad_track = drums.load_sample_to_simpler(-1, wav, confirm=True)
    assert held["status"] == "confirmation_required"
    assert preview["status"] == "dry_run"
    assert missing["status"] == "rejected"
    assert relative["status"] == "rejected"
    assert bad_track["status"] == "rejected"
    assert live.calls == []


def test_simpler_load_is_verified_only_when_the_path_matches(monkeypatch: pytest.MonkeyPatch, wav: str) -> None:
    live = FakeLive(
        {
            "load_simpler_sample": {
                "verified": True,
                "device_class": "OriginalSimpler",
                "sample_path": wav,
                "sample_name": "kick.wav",
                "playback_mode": 0,
            }
        }
    )
    _patch(monkeypatch, live)
    loaded = drums.load_sample_to_simpler(4, wav, confirm=True)
    assert loaded["status"] == "VERIFIED"
    assert loaded["device"] == "Simpler"
    assert loaded["sample_path"] == wav
    assert live.calls[0][0] == "load_simpler_sample"


def test_simpler_mismatch_and_disconnect_are_not_success(monkeypatch: pytest.MonkeyPatch, wav: str) -> None:
    mismatch = FakeLive(
        {
            "load_simpler_sample": {
                "verified": False,
                "device_class": "OriginalSimpler",
                "sample_path": "/somewhere/else.wav",
            }
        }
    )
    _patch(monkeypatch, mismatch)
    unverified = drums.load_sample_to_simpler(4, wav, confirm=True)
    assert unverified["status"] == "UNVERIFIED"
    assert unverified["ok"] is False

    down = FakeLive(error="unavailable")
    _patch(monkeypatch, down)
    failed = drums.load_sample_to_simpler(4, wav, confirm=True)
    assert failed["status"] == "FAILED"
    assert failed["live_changed"] is False


def test_missing_simpler_is_a_failure(monkeypatch: pytest.MonkeyPatch, wav: str) -> None:
    live = FakeLive(error="missing device: Simpler")
    _patch(monkeypatch, live)
    failed = drums.load_sample_to_simpler(2, wav, confirm=True, insert_if_missing=False)
    assert failed["status"] == "FAILED"
    assert live.calls[0][1]["insert_if_missing"] is False


def test_drum_pad_list_and_assignment(monkeypatch: pytest.MonkeyPatch, wav: str) -> None:
    live = FakeLive(
        {
            "list_drum_pads": {
                "pad_count": 1,
                "pads": [{"note": 36, "name": "Kick", "sample_path": "", "devices": []}],
            },
            "load_drum_pad_sample": {
                "verified": True,
                "device_class": "OriginalSimpler",
                "sample_path": wav,
                "sample_name": "kick.wav",
                "note": 36,
            },
        }
    )
    _patch(monkeypatch, live)
    listed = drums.list_drum_pads(3)
    assert listed["ok"] is True
    assert listed["pads"][0]["note"] == 36
    held = drums.load_sample_to_pad(3, 36, wav)
    assert held["live_changed"] is False
    assigned = drums.load_sample_to_pad(3, 36, wav, confirm=True)
    assert assigned["status"] == "VERIFIED"
    assert assigned["note"] == 36
    bad_note = drums.load_sample_to_pad(3, 200, wav, confirm=True)
    assert bad_note["status"] == "rejected"


def test_arrangement_midi_read_write_and_delete(monkeypatch: pytest.MonkeyPatch) -> None:
    notes = [{"pitch": 36, "start_time": 0.0, "duration": 0.5, "velocity": 100, "mute": False}]
    live = FakeLive(
        {
            "get_arrangement_midi_notes": {
                "notes": notes,
                "note_count": 1,
                "start_time": 0.0,
                "end_time": 4.0,
            },
            "replace_arrangement_midi_notes": {
                "verified": True,
                "notes": notes + [{"pitch": 38, "start_time": 1.0, "duration": 0.25, "velocity": 40, "mute": True}],
                "note_count": 2,
                "start_time": 0.0,
                "end_time": 4.0,
            },
        }
    )
    _patch(monkeypatch, live)
    read = drums.get_arrangement_midi_notes(1, 0.0, 4.0)
    assert read["note_count"] == 1
    added = drums.add_arrangement_midi_notes(
        1,
        0.0,
        4.0,
        [{"pitch": 38, "start": 1.0, "duration": 0.25, "velocity": 40, "mute": True}],
        confirm=True,
    )
    assert added["status"] == "VERIFIED"
    assert added["note_count"] == 2
    replace_call = live.calls[-1]
    assert replace_call[0] == "replace_arrangement_midi_notes"
    assert replace_call[1]["expected_source_note_count"] == 1

    live.responses["get_arrangement_midi_notes"] = {
        "notes": notes,
        "note_count": 1,
        "start_time": 0.0,
        "end_time": 4.0,
    }
    live.responses["replace_arrangement_midi_notes"] = {
        "verified": True,
        "notes": [],
        "note_count": 0,
        "start_time": 0.0,
        "end_time": 4.0,
    }
    deleted = drums.delete_arrangement_midi_notes(
        1, 0.0, 4.0, [{"pitch": 36, "start_time": 0.0}], confirm=True
    )
    assert deleted["status"] == "VERIFIED"
    assert deleted["note_count"] == 0
    ambiguous = drums.delete_arrangement_midi_notes(
        1, 0.0, 4.0, [{"pitch": 99, "start_time": 0.0}], confirm=True
    )
    assert ambiguous["status"] == "rejected"


def test_arrangement_clip_move_and_delete_read_back(monkeypatch: pytest.MonkeyPatch) -> None:
    live = FakeLive(
        {
            "move_arrangement_clip": {"verified": True, "start_time": 8.0, "end_time": 12.0},
            "get_arrangement_clips": {"clips": [{"start_time": 8.0, "end_time": 12.0, "name": "take"}]},
            "delete_arrangement_clip": {"verified": True, "remaining": 0, "clip_count_after": 0},
        }
    )
    _patch(monkeypatch, live)
    moved = drums.move_arrangement_clip(2, 0.0, 4.0, 8.0, confirm=True, name="take")
    assert moved["status"] == "VERIFIED"
    assert moved["start_time"] == 8.0
    live.responses["get_arrangement_clips"] = {"clips": []}
    deleted = drums.delete_arrangement_clip(2, 0.0, 4.0, confirm=True)
    assert deleted["status"] == "VERIFIED"
    refused = drums.delete_arrangement_clip(2, 4.0, 4.0, confirm=True)
    assert refused["status"] == "rejected"
    assert drums.move_arrangement_clip(True, 0.0, 4.0, 8.0, confirm=True)["status"] == "rejected"  # type: ignore[arg-type]


def test_move_readback_failure_is_unverified(monkeypatch: pytest.MonkeyPatch) -> None:
    live = FakeLive(
        {
            "duplicate_arrangement_clip": {"verified": False},
            "get_arrangement_clips": {"clips": [{"start_time": 0.0, "end_time": 4.0}]},
        }
    )
    _patch(monkeypatch, live)
    copied = drums.duplicate_arrangement_clip(2, 0.0, 4.0, 8.0, confirm=True)
    assert copied["status"] == "UNVERIFIED"
    down = FakeLive(error="unavailable")
    _patch(monkeypatch, down)
    assert drums.get_arrangement_midi_notes(1, 0.0, 4.0)["status"] == "FAILED"
    assert drums.list_drum_pads(1)["status"] == "FAILED"
