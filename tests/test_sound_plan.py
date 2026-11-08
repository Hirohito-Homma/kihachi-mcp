"""Sound plan and instrument loading against a fake Live. No Ableton process."""

from __future__ import annotations

from typing import Any

import pytest

from kihachi_mcp.gateway.client import configure_ableton_client
from kihachi_mcp.gateway.live_tools import produce_ableton_song
from kihachi_mcp.gateway.sound import (
    BASS_PROBE_PITCHES,
    ableton_device_probe,
    ableton_load_stock_instrument,
    audition_clip,
    meter_rose,
    modest_level_parameter,
)
from kihachi_mcp.models.songspec import SongSpec


class BrowserLive:
    def __init__(self, items: list[dict[str, Any]], devices: list[str] | None = None) -> None:
        self.items = items
        self.devices = list(devices or [])
        self.calls: list[str] = []
        self.notes = 0

    def call(self, command: str, timeout: float, **params: Any) -> dict[str, Any]:
        self.calls.append(command)
        if command == "browse_presets":
            return {"category": params["category"], "path": params["path"], "items": self.items}
        if command == "get_state":
            return {"is_playing": False, "tracks": [], "tempo": 110.0, "signature": [4, 4]}
        if command == "create_track":
            return {"index": 3, "name": params["name"]}
        if command == "insert_first_available_instrument":
            self.devices = [params["candidates"][0]]
            return {"name": params["candidates"][0], "device_count": 1}
        if command == "load_preset":
            return {"loaded": params["name"], "verified_single_add": False}
        if command == "get_track_devices":
            return {
                "devices": [{"name": name, "class_name": name} for name in self.devices],
            }
        if command == "create_midi_clip":
            self.notes = len(params["notes"])
            return {"note_count": self.notes, "clip": params["name"]}
        if command == "get_midi_clip_notes":
            return {"note_count": self.notes}
        raise AssertionError(command)


def _operator() -> dict[str, Any]:
    return {
        "name": "Operator",
        "uri": "query:Operator",
        "is_device": True,
        "is_loadable": False,
        "is_folder": False,
    }


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("KIHACHI_VERIFIED_INSTRUMENT_PATH", str(tmp_path / "verified.json"))
    monkeypatch.setenv("KIHACHI_SAMPLE_LIBRARY_PATHS", "")
    monkeypatch.setenv("KIHACHI_SAMPLE_INDEX_PATH", str(tmp_path / "index.json"))
    monkeypatch.setenv("KIHACHI_SAMPLE_MANIFEST_DIR", str(tmp_path / "manifests"))
    monkeypatch.setenv("KIHACHI_SAMPLE_SELECTION_PATH", str(tmp_path / "selections.json"))
    configure_ableton_client(None)
    yield
    configure_ableton_client(None)


def test_songspec_keys_stay_unchanged() -> None:
    spec = SongSpec.from_dict(
        {
            "genre": "mutation-funk-dub-tech-house",
            "tempo": 110,
            "key": "D# minor",
            "bars": 32,
            "length_minutes": 1.0,
            "tracks": ["KICK"],
        }
    )
    assert set(spec.to_dict()) == {"genre", "tempo", "key", "length_minutes", "bars", "tracks"}


def test_dry_run_includes_sound_plan_and_does_not_call_live() -> None:
    fake = BrowserLive([_operator()])
    configure_ableton_client(fake)
    preview = produce_ableton_song(dry_run=True)
    assert fake.calls == []
    assert preview["live_changed"] is False
    roles = {row["role"]: row for row in preview["sound_plan"]["assignments"]}
    assert roles["KICK"]["preferred_source_type"] == "sample_instrument"
    assert roles["KICK"]["actual_source_type"] == "audio"
    assert roles["KICK"]["device"] is None
    assert roles["KICK"]["sample"] == "pending"
    assert roles["KICK"]["verified"] is False
    assert roles["DRUMS"]["source"] == "sample"
    assert roles["PERCUSSION"]["source"] == "audio"
    assert roles["VOCAL"]["source"] == "audio"
    assert roles["FX"]["source"] == "audio"
    assert roles["SLAP BASS"]["source"] == "instrument"
    assert roles["SLAP BASS"]["device"] == "Operator"
    assert roles["SLAP BASS"]["verified"] is True
    assert roles["SLAP BASS"]["musical_role"] == "funky_slap_bass"
    assert roles["SLAP BASS"]["preset"] is None
    assert roles["SUB BASS"]["device"] == "Operator"
    assert roles["DUB CHORDS"]["musical_role"] == "dub_chord"
    assert "STOCK_INSTRUMENTS" in preview["sound_plan"]["simpler_insert"]
    assert preview["sound_plan"]["architecture"]["oneshot"].startswith("Simpler")
    assert "Simpler" in preview["sound_plan"]["splice_seam"]["oneshot"]


def test_confirm_false_and_ambiguous_name_do_not_load() -> None:
    fake = BrowserLive([_operator(), _operator()])
    configure_ableton_client(fake)
    held = ableton_load_stock_instrument("Operator", track_index=1, confirm=False)
    assert held["status"] == "confirmation_required"
    assert fake.calls == []
    refused = ableton_load_stock_instrument("Operator", track_index=1, confirm=True)
    assert refused["ok"] is False
    assert "insert_first_available_instrument" not in fake.calls
    assert "load_preset" not in fake.calls


def test_read_back_mismatch_is_not_success() -> None:
    fake = BrowserLive([_operator()], devices=["Analog"])
    configure_ableton_client(fake)

    def call(command: str, timeout: float, **params: Any) -> dict[str, Any]:
        fake.calls.append(command)
        if command == "browse_presets":
            return {"category": "instruments", "path": [], "items": fake.items}
        if command == "insert_first_available_instrument":
            return {"name": "Wavetable", "device_count": 1}
        if command == "get_track_devices":
            return {"devices": [{"name": "Analog"}]}
        raise AssertionError(command)

    fake.call = call  # type: ignore[method-assign]
    result = ableton_load_stock_instrument("Operator", track_index=1, confirm=True)
    assert result["ok"] is False
    assert result["status"] == "FAILED"
    assert not (tmp_verified())


def tmp_verified() -> bool:
    from kihachi_mcp.gateway.sound import _verified_path

    return _verified_path().is_file()


def test_level_parameter_and_meter_rules() -> None:
    assert modest_level_parameter(
        [{"index": 3, "name": "Volume", "min": 0, "max": 1, "is_quantized": False}]
    ) == {"parameter_index": 3, "value": 0.70}
    assert modest_level_parameter([{"index": 3, "name": "Volume", "min": 0, "max": 2, "is_quantized": False}]) is None
    assert meter_rose(0.0, 0.0) is False
    assert meter_rose(0.0, 0.2) is True
    assert all(pitch % 12 in {1, 3, 5, 6, 8, 10, 11} for pitch in BASS_PROBE_PITCHES)


def test_audition_stops_once_and_records_raw_meters() -> None:
    class MeterLive:
        def __init__(self) -> None:
            self.calls: list[str] = []
            self.level = 0.0
            self.playing = False

        def call(self, command: str, timeout: float, **params: Any) -> dict[str, Any]:
            self.calls.append(command)
            if command == "get_state":
                return {"is_playing": self.playing}
            if command == "get_mix_snapshot":
                return {"tracks": [{"index": 4, "name": "probe", "output_meter_level": self.level}]}
            if command == "fire_clip":
                self.playing = True
                self.level = 0.42
                return {"fired": True}
            if command == "set_transport":
                self.playing = False
                return {"is_playing": False}
            raise AssertionError(command)

    fake = MeterLive()
    configure_ableton_client(fake)
    result = audition_clip(4, 0)
    assert result["before"] == 0.0
    assert result["after"] == 0.42
    assert result["meter_activity"] is True
    assert result["stopped"] is True
    assert result["human"] == "HUMAN_NOT_VERIFIED"
    assert fake.calls.count("fire_clip") == 1
    assert fake.calls.count("set_transport") == 1


def test_audition_does_not_launch_when_stop_fails() -> None:
    class Stuck:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def call(self, command: str, timeout: float, **params: Any) -> dict[str, Any]:
            self.calls.append(command)
            if command == "get_state":
                return {"is_playing": True}
            if command == "set_transport":
                return {"is_playing": True}
            raise AssertionError(command)

    fake = Stuck()
    configure_ableton_client(fake)
    result = audition_clip(4, 0)
    assert result["playback_started"] is False
    assert result["stopped"] is False
    assert "fire_clip" not in fake.calls
    assert fake.calls.count("set_transport") == 1


def test_probe_stops_when_no_unique_synth_is_listed() -> None:
    fake = BrowserLive([])
    configure_ableton_client(fake)
    result = ableton_device_probe(confirm=True)
    assert result["status"] == "FAILED"
    assert "create_midi_clip" not in fake.calls
