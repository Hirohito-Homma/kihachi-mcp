"""Song-time tools against a fake Live. These tests do not open Ableton."""

from __future__ import annotations

from typing import Any

import pytest

from kihachi_mcp.gateway.client import configure_ableton_client
from kihachi_mcp.gateway.live_tools import ableton_play, ableton_stop
from kihachi_mcp.gateway.song_patterns import ROLES, generate_role_notes
from kihachi_mcp.gateway.song_plan import build_song_plan
from kihachi_mcp.gateway.song_time import bar_start_beat
from kihachi_mcp.gateway.transport import (
    ableton_get_playback_state,
    ableton_get_song_time,
    ableton_jump_song_time,
    ableton_set_song_time,
    ableton_set_start_time,
)


class FakeTransport:
    """jump_by moves current_song_time and leaves start_time alone."""

    def __init__(self, *, playing: bool = False, stop_sticks: bool = False) -> None:
        self.current = 224.0
        self.start_time = 224.0
        self.playing = playing
        self.stop_sticks = stop_sticks
        self.back_to_arranger = True
        self.calls: list[str] = []

    def call(self, command: str, timeout: float | None = None, **params: Any) -> Any:
        self.calls.append(command)
        if command == "get_transport_state":
            return {
                "current_song_time": self.current,
                "is_playing": self.playing,
                "start_time": self.start_time,
                "back_to_arranger": self.back_to_arranger,
            }
        if command == "set_transport":
            if params["action"] == "stop" and not self.stop_sticks:
                self.playing = False
            if params["action"] == "play":
                self.playing = True
            return {"is_playing": self.playing}
        if command == "jump_transport":
            self.current = float(params["time"])
            return {"requested": self.current}
        if command == "set_start_time":
            if self.playing:
                raise AssertionError("played")
            self.start_time = float(params["time"])
            return {
                "requested": self.start_time,
                "start_time": self.start_time,
                "current_song_time": self.current,
                "is_playing": False,
                "property": "start_time",
            }
        raise AssertionError(command)


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeTransport:
    client = FakeTransport()
    configure_ableton_client(client)
    monkeypatch.setattr("kihachi_mcp.gateway.calls.get_ableton_client", lambda: client)
    yield client
    configure_ableton_client(None)


def test_song_time_read_does_not_write(fake: FakeTransport) -> None:
    observed = ableton_get_song_time()
    assert observed["ok"] is True
    assert observed["song_time"] == 224.0
    assert observed["start_time"] == 224.0
    assert observed["unit"] == "beats"
    assert observed["live_changed"] is False
    assert fake.calls == ["get_transport_state"]


def test_song_time_write_requires_confirm(fake: FakeTransport) -> None:
    refused = ableton_set_song_time(31.5)
    assert refused["status"] == "confirmation_required"
    assert refused["live_changed"] is False
    assert fake.calls == []


@pytest.mark.parametrize("beat", [-0.1, True, False, float("nan"), float("inf"), "31.5"])
def test_song_time_rejects_invalid_beats(fake: FakeTransport, beat: object) -> None:
    refused = ableton_set_song_time(beat, confirm=True)
    assert refused["status"] == "rejected"
    assert fake.calls == []


def test_absolute_seek_does_not_call_live(fake: FakeTransport) -> None:
    fake.playing = True
    moved = ableton_set_song_time(31.5, confirm=True)
    assert moved["status"] == "not_supported"
    assert moved["live_changed"] is False
    assert moved["requested"] == 31.5
    assert fake.calls == []
    assert fake.current == 224.0
    assert fake.playing is True


def test_start_time_requires_confirm_and_dry_run_does_not_write(fake: FakeTransport) -> None:
    refused = ableton_set_start_time(0.0)
    assert refused["status"] == "confirmation_required"
    assert refused["live_changed"] is False
    assert fake.calls == []
    preview = ableton_set_start_time(0.0, confirm=True, dry_run=True)
    assert preview["status"] == "dry_run"
    assert preview["live_changed"] is False
    assert fake.calls == []
    assert fake.start_time == 224.0
    assert fake.playing is False


@pytest.mark.parametrize("beat", [-0.1, True, False, float("nan"), float("inf"), "0"])
def test_start_time_rejects_invalid_beats(fake: FakeTransport, beat: object) -> None:
    refused = ableton_set_start_time(beat, confirm=True)
    assert refused["status"] == "rejected"
    assert fake.calls == []
    assert fake.start_time == 224.0


def test_start_time_zero_does_not_play(fake: FakeTransport) -> None:
    moved = ableton_set_start_time(0.0, confirm=True)
    assert moved["ok"] is True
    assert moved["property"] == "start_time"
    assert moved["start_time"] == 0.0
    assert moved["song_time"] == 224.0
    assert moved["is_playing"] is False
    assert fake.playing is False
    assert "set_transport" not in fake.calls
    assert fake.calls == ["get_transport_state", "set_start_time", "get_transport_state"]


def test_start_time_refuses_while_playing(fake: FakeTransport) -> None:
    fake.playing = True
    refused = ableton_set_start_time(0.0, confirm=True)
    assert refused["status"] == "rejected"
    assert "set_start_time" not in fake.calls
    assert fake.start_time == 224.0
    assert fake.playing is True


def test_relative_jump_requires_confirm_and_rejects_bad_delta(fake: FakeTransport) -> None:
    refused = ableton_jump_song_time(-1.0)
    assert refused["status"] == "confirmation_required"
    assert fake.calls == []
    rejected = ableton_jump_song_time(True, confirm=True)
    assert rejected["status"] == "rejected"
    assert fake.calls == []
    out = ableton_jump_song_time(-300.0, confirm=True)
    assert out["status"] == "rejected"
    assert "jump_transport" not in fake.calls
    assert fake.current == 224.0


def test_relative_jump_stops_before_move(fake: FakeTransport) -> None:
    fake.playing = True
    moved = ableton_jump_song_time(-192.5, confirm=True)
    assert moved["ok"] is True
    assert moved["seek_method"] == "jump_by"
    assert moved["song_time"] == pytest.approx(31.5)
    assert moved["start_time"] == 224.0
    assert moved["is_playing"] is False
    assert fake.calls == [
        "get_transport_state",
        "set_transport",
        "get_transport_state",
        "jump_transport",
        "get_transport_state",
    ]
    assert fake.calls.index("set_transport") < fake.calls.index("jump_transport")


def test_failed_stop_does_not_jump(fake: FakeTransport) -> None:
    fake.playing = True
    fake.stop_sticks = True
    blocked = ableton_jump_song_time(-1.0, confirm=True)
    assert blocked["ok"] is False
    assert "jump_transport" not in fake.calls
    assert fake.current == 224.0


def test_play_after_relative_jump_then_stop(fake: FakeTransport) -> None:
    moved = ableton_jump_song_time(-192.5, confirm=True)
    assert moved["song_time"] == pytest.approx(31.5)
    assert "set_transport" not in fake.calls
    started = ableton_play(confirm=True)
    assert started["is_playing"] is True
    assert fake.calls[-1] == "set_transport"
    assert fake.calls.index("jump_transport") < fake.calls.index("set_transport")
    stopped = ableton_stop(confirm=True)
    assert stopped["ok"] is True
    assert stopped["is_playing"] is False
    assert fake.playing is False
    assert fake.current == pytest.approx(31.5)


def test_playback_state_leaves_unknown_session_fields_null(fake: FakeTransport) -> None:
    state = ableton_get_playback_state()
    assert state["song_time"] == 224.0
    assert state["is_playing"] is False
    assert state["back_to_arranger"] is True
    assert state["playing_session_clips"] is None
    assert state["fired_clips"] is None
    assert fake.calls == ["get_transport_state"]


def test_note_starts_are_clip_local_inside_one_song_clip() -> None:
    plan = build_song_plan()
    clips = plan["clips"]
    assert isinstance(clips, list)
    assert len(clips) == len(ROLES)
    notes = plan["notes_by_role"]
    assert isinstance(notes, dict)
    for clip in clips:
        assert clip["session_slot"] == 0
        assert clip["arrangement_start_beats"] == 0.0
        assert clip["length_beats"] == 128.0
    assert min(float(note["start"]) for note in notes["SLAP BASS"]) == bar_start_beat(9)
    assert min(float(note["start"]) for note in notes["SUB BASS"]) == bar_start_beat(9)
    assert min(float(note["start"]) for note in notes["SYNTH STABS"]) == bar_start_beat(17) + 0.5
    assert min(float(note["start"]) for note in notes["ARP"]) == bar_start_beat(17)
    assert min(float(note["start"]) for note in notes["VOCODER CARRIER"]) == bar_start_beat(17)
    assert min(float(note["start"]) for note in notes["VOCAL"]) == bar_start_beat(17)
    for role in ROLES:
        rendered = generate_role_notes(role, 32, 0.54, 1)
        assert rendered
        assert min(float(note["start"]) for note in rendered) >= 0
        assert max(float(note["start"]) for note in rendered) < 128


def test_catalogue_exposes_song_time_and_refuses_arrangement_return() -> None:
    from kihachi_mcp.gateway.tools import ableton_tool_catalogue

    listed = ableton_tool_catalogue()
    exposed = {row["name"]: row["status"] for row in listed["exposed"]}
    missing = {row["name"]: row["status"] for row in listed["not_implemented"]}
    partial = {row["name"] for row in listed["partial"]}
    assert exposed["ableton_get_song_time"] == "SUPPORTED"
    assert exposed["ableton_set_song_time"] == "SUPPORTED"
    assert exposed["ableton_jump_song_time"] == "SUPPORTED"
    assert exposed["ableton_set_start_time"] == "SUPPORTED"
    assert exposed["ableton_get_playback_state"] == "SUPPORTED"
    assert "get_transport_state" not in partial
    assert "stop_all_clips" in partial
    assert missing["back_to_arrangement"] == "NOT IMPLEMENTED"
    assert missing["continue_playing"] == "NOT IMPLEMENTED"
    assert missing["play_selection"] == "NOT IMPLEMENTED"
    assert "read_arrangement_midi_notes" not in missing
    assert exposed["get_arrangement_midi_notes"] == "SUPPORTED"
    assert exposed["delete_arrangement_clip"] == "SUPPORTED"
