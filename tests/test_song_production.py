"""Song production against a fake Live. These tests do not open Ableton."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from kihachi_mcp.gateway.client import (
    AbletonCommandError,
    AbletonTimeout,
    AbletonUnavailable,
    configure_ableton_client,
)
from kihachi_mcp.gateway.live_tools import (
    ableton_add_notes,
    ableton_create_audio_track,
    ableton_create_midi_clip,
    ableton_create_midi_track,
    ableton_list_devices,
    ableton_mute,
    ableton_play,
    ableton_set_pan,
    ableton_set_volume,
    produce_ableton_song,
)
from kihachi_mcp.gateway.song_patterns import ROLES, generate_role_notes, harmonic_pitch
from kihachi_mcp.gateway.song_plan import build_song_plan
from kihachi_mcp.gateway.song_time import swing_beat


class FakeLive:
    """Enough of AbletonGPT for the song tools. Not imported by production code."""

    def __init__(
        self,
        *,
        tempo: float = 110.0,
        playing: bool = False,
        signature: list[int] | None = None,
        scene_count: int = 8,
        tracks: list[dict[str, Any]] | None = None,
        fail: str | None = None,
        fail_clip_on: int | None = None,
    ) -> None:
        self.tempo = tempo
        self.playing = playing
        self.signature = signature if signature is not None else [4, 4]
        self.scene_count = scene_count
        self.fail = fail
        self.fail_clip_on = fail_clip_on
        self.clip_writes = 0
        self.calls: list[str] = []
        self.params: list[tuple[str, dict[str, Any]]] = []
        self.tracks = tracks if tracks is not None else [_track(0, "1-MIDI")]
        self.session: dict[tuple[int, int], dict[str, Any]] = {}
        self.arrangement: dict[int, list[dict[str, Any]]] = {}

    def call(self, command: str, timeout: float | None = None, **params: Any) -> Any:
        self.calls.append(command)
        self.params.append((command, params))
        if self.fail == "timeout":
            raise AbletonTimeout
        if self.fail == "unavailable":
            raise AbletonUnavailable
        if command == "get_state":
            return {
                "is_playing": self.playing,
                "tempo": self.tempo,
                "signature": list(self.signature),
                "scene_count": self.scene_count,
                "tracks": [
                    {
                        "index": track["index"],
                        "name": track["name"],
                        "volume": track["volume"],
                        "mute": track["mute"],
                        "solo": track["solo"],
                        "arm": False,
                        "clip_slots": self.scene_count,
                    }
                    for track in self.tracks
                ],
            }
        if command == "set_tempo":
            self.tempo = float(params["bpm"])
            return {"tempo": self.tempo}
        if command == "set_transport":
            self.playing = params["action"] == "play"
            return {"is_playing": self.playing}
        if command == "create_track":
            index = len(self.tracks) if int(params.get("index", -1)) == -1 else int(params["index"])
            self.tracks.append(_track(index, str(params.get("name") or ""), params["track_type"]))
            return {"index": index, "name": params.get("name"), "track_type": params["track_type"], "total_tracks": len(self.tracks)}
        if command == "get_midi_clip_notes":
            clip = self.session.get((int(params["track_index"]), int(params["clip_index"])))
            if clip is None:
                raise AbletonCommandError("target clip slot does not contain a MIDI clip")
            return {
                "track_index": int(params["track_index"]),
                "clip_index": int(params["clip_index"]),
                "clip": clip["name"],
                "length_beats": clip["length"],
                "notes": [dict(note) for note in clip["notes"]],
                "note_count": len(clip["notes"]),
                "notes_fingerprint": "v1:" + "0" * 64,
                "truncated": False,
            }
        if command == "create_midi_clip":
            self.clip_writes += 1
            if self.fail_clip_on is not None and self.clip_writes == self.fail_clip_on:
                raise AbletonCommandError("note write failed")
            key = (int(params["track_index"]), int(params["clip_index"]))
            if key in self.session:
                raise AbletonCommandError("target clip slot is not empty")
            notes = [dict(note) for note in params.get("notes") or []]
            self.session[key] = {"name": params.get("name"), "length": float(params["length_beats"]), "notes": notes}
            return {"clip": params.get("name"), "note_count": len(notes), "length_beats": float(params["length_beats"])}
        if command == "apply_expression_to_clip":
            key = (int(params["track_index"]), int(params["clip_index"]))
            clip = self.session[key]
            if params.get("expected_source_note_count") != len(clip["notes"]):
                raise AbletonCommandError("source MIDI clip note count changed before apply")
            if params.get("expected_source_notes_fingerprint") != "v1:" + "0" * 64:
                raise AbletonCommandError("source MIDI clip notes changed before apply")
            clip["notes"] = [dict(note) for note in params["notes"]]
            return {"note_count": len(clip["notes"]), "source_note_count": params["expected_source_note_count"]}
        if command == "get_arrangement_clips":
            clips = self.arrangement.get(int(params["track_index"]), [])
            return {"clips": [dict(clip) for clip in clips], "clip_count": len(clips), "truncated": False}
        if command == "copy_session_clip_to_arrangement":
            index = int(params["track_index"])
            source = self.session[(index, int(params["clip_index"]))]
            start = float(params["destination_time_beats"])
            end = start + float(source["length"])
            existing = self.arrangement.setdefault(index, [])
            for clip in existing:
                if start < clip["end_time"] - 1e-6 and end > clip["start_time"] + 1e-6:
                    raise AbletonCommandError("Arrangement destination overlaps an existing clip")
            created = {
                "name": params.get("name") or source["name"],
                "start_time": start,
                "end_time": end,
                "is_midi_clip": True,
                "is_audio_clip": False,
            }
            existing.append(created)
            return dict(created)
        if command == "set_track_volume":
            track = self.tracks[int(params["track_index"])]
            track["volume"] = float(params["volume"])
            return {"volume": track["volume"]}
        if command == "set_track_pan":
            track = self.tracks[int(params["track_index"])]
            track["pan"] = float(params["pan"])
            return {"pan": track["pan"]}
        if command == "set_track_mute":
            track = self.tracks[int(params["track_index"])]
            track["mute"] = bool(params["muted"])
            return {"muted": track["mute"]}
        if command == "set_track_solo":
            return {"soloed": bool(params["soloed"])}
        if command == "get_track_devices":
            return {
                "devices": [
                    {
                        "index": 0,
                        "name": "Operator",
                        "class_name": "Operator",
                        "parameters": [{"index": 0, "name": "Volume", "min": 0.0, "max": 1.0, "value": 0.7}],
                    }
                ]
            }
        raise AssertionError(command)


def _track(index: int, name: str, track_type: str = "midi") -> dict[str, Any]:
    return {"index": index, "name": name, "volume": 0.85, "pan": 0.0, "mute": False, "solo": False, "type": track_type}


@pytest.fixture(autouse=True)
def _no_live_leak(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("KIHACHI_SAMPLE_LIBRARY_PATHS", "")
    monkeypatch.setenv("KIHACHI_SAMPLE_INDEX_PATH", str(tmp_path / "index.json"))
    monkeypatch.setenv("KIHACHI_SAMPLE_MANIFEST_DIR", str(tmp_path / "manifests"))
    monkeypatch.setenv("KIHACHI_SAMPLE_SELECTION_PATH", str(tmp_path / "selections.json"))
    monkeypatch.setenv("KIHACHI_VERIFIED_INSTRUMENT_PATH", str(tmp_path / "verified-instrument.json"))
    configure_ableton_client(None)
    yield
    configure_ableton_client(None)


def test_patterns_are_deterministic_and_in_key() -> None:
    first = build_song_plan()
    second = build_song_plan()
    assert first["plan_id"] == second["plan_id"]
    assert first["notes_by_role"] == second["notes_by_role"]
    notes = first["notes_by_role"]
    assert isinstance(notes, dict)
    kick = notes["KICK"]
    assert len(kick) == 112
    assert {note["pitch"] for note in kick} == {36}
    assert all(float(note["start"]).is_integer() for note in kick)
    slap = notes["SLAP BASS"]
    sub = notes["SUB BASS"]
    assert {note["pitch"] for note in sub} <= {27, 34}
    assert 39 in {note["pitch"] for note in slap} and 51 in {note["pitch"] for note in slap}
    assert min(note["velocity"] for note in slap) < 80
    assert max(note["velocity"] for note in slap) >= 114
    assert not any(38.4 <= float(note["start"]) <= 39.1 for note in slap)
    assert {(note["start"], note["pitch"]) for note in slap} != {(note["start"], note["pitch"]) for note in sub}
    assert all(float(note["duration"]) <= 0.28 for note in notes["DUB CHORDS"])
    for role in ("SLAP BASS", "SUB BASS", "DUB CHORDS", "SYNTH STABS", "ARP", "VOCODER CARRIER", "VOCAL", "FX"):
        assert all(harmonic_pitch(int(note["pitch"])) for note in notes[role])
    assert all(notes[role] for role in ROLES)
    drums = notes["DRUMS"]
    assert any(abs(float(note["start"]) % 0.25) > 0.01 for note in drums)
    longer = build_song_plan(bars=128)
    assert max(float(note["start"]) for role in ROLES for note in longer["notes_by_role"][role]) < 512


def test_swing_moves_only_the_off_16th() -> None:
    assert swing_beat(0.0, 0.54) == 0.0
    assert swing_beat(0.5, 0.54) == 0.5
    assert swing_beat(0.25, 0.54) == pytest.approx(0.27)
    assert swing_beat(0.25, 0.50) == 0.25
    other = generate_role_notes("SLAP BASS", 32, 0.54, 2)
    base = generate_role_notes("SLAP BASS", 32, 0.54, 1)
    assert [note["pitch"] for note in other] == [note["pitch"] for note in base]
    assert [note["velocity"] for note in other] != [note["velocity"] for note in base]


def test_plan_rejects_unknown_music_before_any_call() -> None:
    fake = FakeLive()
    configure_ableton_client(fake)
    assert produce_ableton_song(key="C minor", confirm=True)["status"] == "rejected"
    assert produce_ableton_song(bars=16, confirm=True)["status"] == "rejected"
    assert produce_ableton_song(style="techno", confirm=True)["status"] == "rejected"
    assert fake.calls == []


def test_dry_run_and_missing_confirm_do_not_touch_live() -> None:
    fake = FakeLive()
    configure_ableton_client(fake)
    preview = produce_ableton_song(dry_run=True, confirm=True)
    assert preview["ok"] is True
    assert preview["status"] == "dry_run"
    assert preview["live_changed"] is False
    assert preview["timebase"]["unit"] == "beats"
    assert preview["songspec"]["key"] == "D# minor"
    assert preview["songspec"]["bars"] == 32
    assert preview["note_counts"]["KICK"] == 112
    assert len(preview["arrangement"]) == 4
    assert preview["arrangement"][-1]["end_bar"] == 32
    assert preview["devices"]["policy"] == "midi_only"
    assert "notes_by_role" not in preview
    held = produce_ableton_song()
    assert held["status"] == "confirmation_required"
    assert held["ok"] is False
    assert fake.calls == []


def test_confirm_builds_the_song_and_a_second_call_does_not_duplicate_it() -> None:
    fake = FakeLive()
    configure_ableton_client(fake)
    result = produce_ableton_song(confirm=True)
    assert result["ok"] is True
    assert result["status"] == "applied"
    assert result["verification"] == {
        "tempo": True,
        "tracks": True,
        "clips": True,
        "notes": True,
        "arrangement": True,
        "mixer_volume": True,
    }
    assert result["read_back"]["note_counts"]["SLAP BASS"] > 0
    assert result["read_back"]["arrangement_beats"]["KICK"] == pytest.approx(128.0)
    assert fake.calls.count("create_track") == 11
    assert all(params.get("index") == -1 for command, params in fake.params if command == "create_track")
    assert "set_transport" not in fake.calls
    assert "load_preset" not in fake.calls
    assert "add_native_device" not in fake.calls
    again = produce_ableton_song(confirm=True)
    assert again["ok"] is True
    assert again["status"] == "already_applied"
    assert again["live_changed"] is False
    assert fake.calls.count("create_track") == 11
    assert fake.calls.count("create_midi_clip") == 11
    assert fake.calls.count("copy_session_clip_to_arrangement") == 11
    assert len([track for track in fake.tracks if track["name"].endswith("KICK")]) == 1


def test_partial_clip_failure_stops_and_does_not_claim_success() -> None:
    fake = FakeLive(fail_clip_on=3)
    configure_ableton_client(fake)
    result = produce_ableton_song(confirm=True)
    assert result["ok"] is False
    assert result["partial"] is True
    assert result["status"] == "failed"
    statuses = [op["status"] for op in result["operations"] if op["op"] == "create_clip"]
    assert statuses.count("SUCCESS") == 2
    assert "FAILED" in statuses
    assert "SKIPPED" in statuses
    assert any(op["op"] == "arrangement" and op["status"] == "SKIPPED" for op in result["operations"])
    assert not any("delete" in command for command in fake.calls)


def test_ableton_down_or_playing_does_not_create_tracks() -> None:
    down = FakeLive(fail="unavailable")
    configure_ableton_client(down)
    failed = produce_ableton_song(confirm=True)
    assert failed["ok"] is False
    assert failed["partial"] is False
    assert "create_track" not in down.calls

    configure_ableton_client(FakeLive(fail="timeout"))
    timed = produce_ableton_song(confirm=True)
    assert timed["operations"][0]["detail"] == "timeout"

    playing = FakeLive(playing=True)
    configure_ableton_client(playing)
    blocked = produce_ableton_song(confirm=True)
    assert blocked["status"] == "blocked"
    assert "create_track" not in playing.calls

    other_tempo = FakeLive(tempo=120.0)
    configure_ableton_client(other_tempo)
    retimed = produce_ableton_song(confirm=True)
    assert retimed["ok"] is True
    assert other_tempo.tempo == 110.0
    assert other_tempo.calls.count("set_tempo") == 1

    empty = FakeLive(tempo=100.0, tracks=[])
    configure_ableton_client(empty)
    retimed = produce_ableton_song(tempo=100, confirm=True)
    assert retimed["ok"] is True
    assert empty.tempo == 100.0


def test_track_clip_and_note_tools_validate_before_calling() -> None:
    fake = FakeLive()
    configure_ableton_client(fake)
    assert ableton_create_midi_track("KICK")["status"] == "confirmation_required"
    assert fake.calls == []
    created = ableton_create_midi_track("KICK", confirm=True)
    assert created == {"ok": True, "status": "SUCCESS", "live_changed": True, "track_index": 1, "name": "KICK", "track_type": "midi"}
    again = ableton_create_midi_track("KICK", confirm=True)
    assert again["status"] == "SKIPPED"
    assert fake.calls.count("create_track") == 1
    audio = ableton_create_audio_track("ROOM", confirm=True)
    assert audio["track_type"] == "audio"
    assert ableton_create_midi_track("a/b", confirm=True)["status"] == "rejected"

    refused = ableton_create_midi_clip(1, "Probe", track_name="KICK", notes=[{"pitch": 128, "start": 0, "duration": 0.25, "velocity": 100}])
    assert refused["status"] == "confirmation_required"
    bad = ableton_create_midi_clip(
        1,
        "Probe",
        track_name="KICK",
        notes=[{"pitch": 128, "start": 0, "duration": 0.25, "velocity": 100}],
        confirm=True,
    )
    assert bad["status"] == "rejected"
    for note in (
        {"pitch": 60, "start": 0, "duration": 0.25, "velocity": 0},
        {"pitch": 60, "start": 0, "duration": 0.25, "velocity": 128},
        {"pitch": 60, "start": -1, "duration": 0.25, "velocity": 100},
        {"pitch": 60, "start": 0, "duration": 0, "velocity": 100},
        {"pitch": 60, "start": 4, "duration": 0.25, "velocity": 100},
    ):
        outcome = ableton_create_midi_clip(1, "Probe", track_name="KICK", notes=[note], confirm=True)
        assert outcome["status"] == "rejected"
    assert "create_midi_clip" not in fake.calls

    clip = ableton_create_midi_clip(
        1,
        "Probe",
        track_name="KICK",
        notes=[
            {"pitch": 36, "start": 0, "duration": 0.25, "velocity": 110},
            {"pitch": 36, "start": 1, "duration": 0.25, "velocity": 100},
            {"pitch": 36, "start": 2, "duration": 0.25, "velocity": 90},
        ],
        arrangement_start_bar=1,
        confirm=True,
    )
    assert clip["ok"] is True
    assert clip["note_count"] == 3
    assert clip["arrangement"]["start_time"] == 0.0
    added = ableton_add_notes(
        [{"pitch": 36, "start": 3, "duration": 0.2, "velocity": 80}, {"pitch": 38, "start": 3.5, "duration": 0.1, "velocity": 70}],
        track_name="KICK",
        confirm=True,
    )
    assert added["note_count"] == 5
    assert added["source_note_count"] == 3
    assert ableton_add_notes([{"pitch": True, "start": 0, "duration": 0.2, "velocity": 80}], track_name="KICK", confirm=True)["status"] == "rejected"
    assert ableton_set_volume(1.5, track_name="KICK", confirm=True)["status"] == "rejected"
    assert ableton_set_pan(1.2, track_name="KICK", confirm=True)["status"] == "rejected"
    assert ableton_mute(True, track_name="KICK")["live_changed"] is False
    assert ableton_play()["status"] == "confirmation_required"
    assert "set_transport" not in fake.calls
    devices = ableton_list_devices(track_name="KICK")
    assert devices["devices"][0]["parameters"][0]["name"] == "Volume"


def test_registry_keeps_studio_create_song() -> None:
    from kihachi_mcp.gateway.tools import ableton_tool_catalogue
    from kihachi_mcp.server import mcp

    names = {tool.name for tool in asyncio.run(mcp.list_tools())}
    assert "create_song" in names
    assert "produce_ableton_song" in names
    assert "get_midi_clip_notes" in names
    exposed = {row["name"] for row in ableton_tool_catalogue()["exposed"]}
    assert "get_midi_clip_notes" in exposed
    missing = {row["name"] for row in ableton_tool_catalogue()["not_implemented"]}
    assert "rename_track" in missing
    assert "delete_track" in missing
    assert "add_notes" not in missing


def test_get_midi_clip_notes_validates_target_completeness_and_note_shape() -> None:
    from kihachi_mcp.gateway.live_tools import get_midi_clip_notes

    fake = FakeLive(tracks=[_track(0, "BASS"), _track(1, "BASS"), _track(2, "SOLO")])
    configure_ableton_client(fake)

    assert get_midi_clip_notes(track_index=2, clip_index=-1)["complete"] is False
    assert get_midi_clip_notes(track_index=2, track_name="SOLO", clip_index=0)["complete"] is False
    assert get_midi_clip_notes(clip_index=0)["complete"] is False
    assert get_midi_clip_notes(track_name="   ", clip_index=0)["complete"] is False
    assert fake.calls == []

    dup = get_midi_clip_notes(track_name="BASS", clip_index=0)
    assert dup["ok"] is False and dup["complete"] is False
    assert "一意" in dup["error"]

    empty_slot = get_midi_clip_notes(track_index=2, clip_index=0)
    assert empty_slot["ok"] is False and empty_slot["complete"] is False

    fake.session[(2, 0)] = {
        "name": "Bass Loop",
        "length": 8.0,
        "notes": [
            {"pitch": 39, "start_time": 0.0, "duration": 0.5, "velocity": 100},
            {"pitch": 51, "start_time": 4.0, "duration": 0.25, "velocity": 90},
        ],
    }
    read = get_midi_clip_notes(track_name="SOLO", clip_index=0)
    assert read["ok"] is True
    assert read["complete"] is True
    assert read["status"] == "SUCCESS"
    assert read["live_changed"] is False
    assert read["track_index"] == 2
    assert read["clip_index"] == 0
    assert read["note_count"] == 2
    assert len(read["notes"]) == 2

    class CustomRemote:
        def __init__(self, payload: dict[str, Any]) -> None:
            self.payload = payload

        def call(self, command: str, timeout: float | None = None, **params: Any) -> Any:
            assert command == "get_midi_clip_notes"
            return dict(self.payload)

    truncated_notes = [
        {"pitch": 36, "start_time": float(i % 16) * 0.25, "duration": 0.1, "velocity": 100}
        for i in range(4096)
    ]
    configure_ableton_client(
        CustomRemote(
            {
                "track_index": 0,
                "clip_index": 0,
                "clip": "Big Clip",
                "length_beats": 16.0,
                "notes": truncated_notes,
                "note_count": 5000,
                "truncated": True,
            }
        )
    )
    partial = get_midi_clip_notes(track_index=0, clip_index=0)
    assert partial["ok"] is False
    assert partial["complete"] is False
    assert partial["status"] == "PARTIAL"
    assert partial["live_changed"] is False

    for bad_note in (
        {"pitch": 128, "start_time": 0.0, "duration": 0.5, "velocity": 100},
        {"pitch": 60, "start_time": 8.0, "duration": 0.5, "velocity": 100},
        {"pitch": 60, "start_time": 0.0, "duration": 0.0, "velocity": 100},
        {"pitch": 60, "start_time": 0.0, "duration": 0.5, "velocity": 0},
        {"pitch": 60, "start_time": 0.0, "duration": 0.5, "velocity": 100, "probability": 1.5},
    ):
        configure_ableton_client(
            CustomRemote(
                {
                    "track_index": 0,
                    "clip_index": 0,
                    "clip": "Broken",
                    "length_beats": 8.0,
                    "notes": [bad_note],
                    "note_count": 1,
                    "truncated": False,
                }
            )
        )
        broken = get_midi_clip_notes(track_index=0, clip_index=0)
        assert broken["ok"] is False
        assert broken["complete"] is False
        assert broken["status"] == "FAILED"


def test_get_midi_clip_notes_rejects_mismatched_or_missing_identity() -> None:
    from kihachi_mcp.gateway.live_tools import get_midi_clip_notes

    base = {
        "track_index": 0,
        "clip_index": 0,
        "clip": "Loop",
        "length_beats": 4.0,
        "notes": [{"pitch": 36, "start_time": 0.0, "duration": 0.5, "velocity": 100}],
        "note_count": 1,
        "truncated": False,
    }

    class CustomRemote:
        def __init__(self, payload: dict[str, Any]) -> None:
            self.payload = payload

        def call(self, command: str, timeout: float | None = None, **params: Any) -> Any:
            if command == "get_state":
                return {"tracks": [{"index": 0, "name": "BASS"}, {"index": 1, "name": "KEYS"}]}
            assert command == "get_midi_clip_notes"
            return dict(self.payload)

    # 1. track_index 欠落 (辞書キーなし、None)
    # 2. clip_index 欠落 (辞書キーなし、None)
    # 3. それぞれの値の不一致
    # 4. bool や文字列など不正な型
    # 既存の clip 識別情報欠落
    bad_payloads = [
        # track_index 欠落
        {key: value for key, value in base.items() if key != "track_index"},
        {**base, "track_index": None},
        # clip_index 欠落
        {key: value for key, value in base.items() if key != "clip_index"},
        {**base, "clip_index": None},
        # 不一致
        {**base, "track_index": 1},
        {**base, "clip_index": 3},
        # bool 型
        {**base, "track_index": True},
        {**base, "track_index": False},
        {**base, "clip_index": True},
        {**base, "clip_index": False},
        # 文字列型
        {**base, "track_index": "0"},
        {**base, "clip_index": "0"},
        # float 型
        {**base, "track_index": 0.0},
        {**base, "clip_index": 0.0},
        # clip 識別情報欠落
        {key: value for key, value in base.items() if key != "clip"},
    ]
    for payload in bad_payloads:
        configure_ableton_client(CustomRemote(payload))
        result = get_midi_clip_notes(track_index=0, clip_index=0)
        assert result["ok"] is False, payload
        assert result["complete"] is False, payload
        assert result["status"] == "FAILED", payload
        assert result["live_changed"] is False

    configure_ableton_client(CustomRemote({**base, "track": "KEYS"}))
    wrong_name = get_midi_clip_notes(track_name="BASS", clip_index=0)
    assert wrong_name["ok"] is False and wrong_name["complete"] is False
    assert wrong_name["status"] == "FAILED"

    # 5. 両方が一致する正常応答 (track_index 指定および track_name 指定)
    configure_ableton_client(CustomRemote(base))
    good_by_index = get_midi_clip_notes(track_index=0, clip_index=0)
    assert good_by_index["ok"] is True and good_by_index["complete"] is True
    assert good_by_index["status"] == "SUCCESS"
    assert good_by_index["track_index"] == 0 and good_by_index["clip_index"] == 0

    good_by_name = get_midi_clip_notes(track_name="BASS", clip_index=0)
    assert good_by_name["ok"] is True and good_by_name["complete"] is True
    assert good_by_name["status"] == "SUCCESS"
    assert good_by_name["track_index"] == 0 and good_by_name["clip_index"] == 0 and good_by_name["track"] == "BASS"
