"""Local sample index, ranking, and placement against a fake Live."""

from __future__ import annotations

import asyncio
import json
import wave
from pathlib import Path
from typing import Any

import pytest

from kihachi_mcp.gateway.client import configure_ableton_client
from kihachi_mcp.gateway.live_tools import produce_ableton_song
from kihachi_mcp.gateway.samples.index import index_library, library_roots, manifest_dir
from kihachi_mcp.gateway.samples.meta import filename_metadata
from kihachi_mcp.gateway.samples.place import place_indexed_sample
from kihachi_mcp.gateway.samples.query import sample_plan, search_indexed
from kihachi_mcp.gateway.samples.rank import sample_requirements, score_sample
from kihachi_mcp.gateway.samples.tools import (
    find_samples_for_song,
    get_sample,
    get_sample_metadata,
    select_sample,
)
from kihachi_mcp.gateway.song_plan import build_song_plan


def _wav(path: Path, frames: int = 4000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "w") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(44100)
        handle.writeframes(b"\x00\x00" * frames)


def _record(name: str, duration: float = 2.0) -> dict[str, Any]:
    meta = filename_metadata(name)
    return {
        "filename": name,
        "duration_seconds": duration,
        "filename_metadata": meta,
        "analysis": {"bpm": None, "key": None, "energy": None},
    }


def _isolate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, root: Path | None) -> None:
    monkeypatch.setenv("KIHACHI_SAMPLE_LIBRARY_PATHS", "" if root is None else str(root))
    monkeypatch.setenv("KIHACHI_SAMPLE_INDEX_PATH", str(tmp_path / "index.json"))
    monkeypatch.setenv("KIHACHI_SAMPLE_MANIFEST_DIR", str(tmp_path / "manifests"))
    monkeypatch.setenv("KIHACHI_SAMPLE_SELECTION_PATH", str(tmp_path / "selections.json"))


class AudioLive:
    def __init__(self, *, warping: bool = True, playing: bool = False) -> None:
        self.warping = warping
        self.playing = playing
        self.calls: list[str] = []
        self.tracks: list[dict[str, Any]] = []
        self.session: dict[int, list[dict[str, Any]]] = {}
        self.arrangement: dict[int, list[dict[str, Any]]] = {}

    def call(self, command: str, timeout: float, **params: Any) -> dict[str, Any]:
        self.calls.append(command)
        if command == "get_state":
            return {"is_playing": self.playing, "tracks": list(self.tracks), "tempo": 110.0}
        if command == "create_track":
            track = {"index": len(self.tracks), "name": params["name"], "type": params["track_type"]}
            self.tracks.append(track)
            return track
        if command == "get_audio_clip_paths":
            store = self.arrangement if params["view"] == "arrangement" else self.session
            return {"clips": list(store.get(params["track_index"], []))}
        if command == "import_audio_clip":
            clip = {"location": "session", "index": 0, "warping": self.warping}
            self.session.setdefault(params["track_index"], []).append(clip)
            return {"clip_index": 0, "length": 4.0}
        if command == "copy_session_clip_to_arrangement":
            clip = {"location": "arrangement", "index": 0, "is_audio_clip": True, "warping": True}
            self.arrangement.setdefault(params["track_index"], []).append(clip)
            return {"is_audio_clip": True}
        raise AssertionError(command)


def test_filename_facts_stay_separate_from_analysis() -> None:
    meta = filename_metadata("percussion_loop_110bpm_funky.wav")
    assert meta["role"] == "percussion"
    assert meta["bpm"] == 110
    assert meta["kind"] == "loop"
    assert filename_metadata("loop_110_120.wav")["bpm"] is None
    scored = score_sample(_record("percussion_loop_110bpm_funky.wav"), {"role": "percussion", "type": "loop", "bpm": 110})
    assert scored["energy"] is None


def test_far_loop_bpm_is_not_automatic_and_drums_do_not_need_a_key() -> None:
    far = score_sample(
        _record("percussion_loop_160bpm.wav"),
        {"role": "percussion", "type": "loop", "bpm": 110, "key": None, "tags": []},
    )
    assert far["bpm"] == 0.0
    assert far["automatic"] is False
    assert "bpm:far" in far["match_reasons"]
    kick = score_sample(
        _record("kick_oneshot.wav", 0.2),
        {"role": "kick", "type": "oneshot", "bpm": None, "key": "D# minor", "tags": []},
    )
    assert kick["key"] == 0.2
    assert kick["automatic"] is True
    vocal = {"role": "vocal", "type": "oneshot", "bpm": None, "key": "D# minor", "tags": []}
    matching = score_sample(_record("vocal_oneshot_D#minor.wav", 0.4), vocal)
    clashing = score_sample(_record("vocal_oneshot_Cmajor.wav", 0.4), vocal)
    assert matching["score"] > clashing["score"]


def test_index_reads_only_the_configured_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "splice"
    audio = root / "fx_impact_dub_oneshot.wav"
    _wav(audio)
    outside = tmp_path / "outside"
    _wav(outside / "secret_kick_oneshot.wav")
    (root / "leak.wav").symlink_to(outside / "secret_kick_oneshot.wav")
    before = audio.stat().st_mtime_ns
    _isolate(monkeypatch, tmp_path, root)
    indexed = index_library()
    assert indexed["indexed_count"] == 1
    assert audio.stat().st_mtime_ns == before
    rows = search_indexed(role="fx")
    dumped = json.dumps(rows)
    assert "path" not in rows[0]
    assert str(root) not in dumped
    assert "secret_kick" not in dumped
    assert rows[0]["source"] == "splice"
    assert set(rows[0]) <= {
        "sample_id",
        "filename",
        "role",
        "bpm",
        "key",
        "duration",
        "loop_or_oneshot",
        "score",
        "match_reasons",
        "source",
    }
    assert get_sample(rows[0]["sample_id"])["sample"]["analysis_bpm"] is None
    user_root = tmp_path / "library"
    _wav(user_root / "kick_oneshot.wav")
    _isolate(monkeypatch, tmp_path, user_root)
    assert index_library()["indexed_count"] == 1
    assert search_indexed(role="kick")[0]["source"] == "user"
    _isolate(monkeypatch, tmp_path, tmp_path / "missing")
    missing = index_library()
    assert missing["indexed_count"] == 0
    assert missing["missing_root_count"] == 1
    _isolate(monkeypatch, tmp_path, None)
    assert index_library()["indexed_count"] == 0


def test_dry_run_sample_plan_does_not_call_live(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "splice"
    _wav(root / "fx_impact_dub_oneshot.wav")
    _isolate(monkeypatch, tmp_path, root)
    index_library()
    fake = AudioLive()
    configure_ableton_client(fake)
    preview = produce_ableton_song(dry_run=True, confirm=True)
    assert fake.calls == []
    plan = preview["sample_plan"]
    assert plan["live_changed"] is False
    assert plan["selection"] == "assisted"
    assert plan["adopted"] == []
    manifest = json.dumps(plan["manifest"])
    assert str(root) not in manifest
    assert '"path"' not in manifest
    saved = json.loads((manifest_dir() / f"{preview['plan_id']}.json").read_text(encoding="utf-8"))
    assert saved["sample_selection"] == "assisted"
    configure_ableton_client(None)


def test_assisted_selection_is_in_the_manifest_without_a_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "splice"
    _wav(root / "fx_impact_dub_oneshot.wav")
    _isolate(monkeypatch, tmp_path, root)
    index_library()
    sample_id = search_indexed(role="fx")[0]["sample_id"]
    assert select_sample(sample_id, "fx")["live_changed"] is False
    plan = sample_plan(build_song_plan(), "assisted")
    assert plan["adopted"][0]["sample_id"] == sample_id
    assert plan["manifest"]["samples"][0]["source"] == "splice"
    assert plan["manifest"]["samples"][0]["filename"].endswith(".wav")
    assert plan["manifest"]["samples"][0]["placement"] is None
    automatic = sample_plan(build_song_plan(), "automatic")
    assert automatic["adopted"] == []
    assert "path" not in plan["adopted"][0]
    fetched = get_sample(sample_id)
    assert "path" not in fetched["sample"]
    assert str(root) not in json.dumps(fetched)


def test_warped_audio_is_copied_and_unwarped_audio_is_not(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "splice"
    _wav(root / "fx_impact_dub_oneshot.wav")
    _isolate(monkeypatch, tmp_path, root)
    index_library()
    sample_id = search_indexed(role="fx")[0]["sample_id"]
    held = AudioLive()
    configure_ableton_client(held)
    assert place_indexed_sample(sample_id, title="KIHACHI MANUS TEST 01", role="fx")["status"] == "confirmation_required"
    assert held.calls == []
    warped = AudioLive(warping=True)
    configure_ableton_client(warped)
    placed = place_indexed_sample(sample_id, title="KIHACHI MANUS TEST 01", role="fx", confirm=True)
    assert placed["ok"] is True
    assert placed["arrangement"] == "SUCCESS"
    assert "copy_session_clip_to_arrangement" in warped.calls
    assert str(root) not in json.dumps(placed)
    unwarped = AudioLive(warping=False)
    configure_ableton_client(unwarped)
    blocked = place_indexed_sample(sample_id, title="KIHACHI MANUS TEST 01", role="fx", confirm=True)
    assert blocked["arrangement"] == "FAILED"
    assert blocked["ok"] is False
    assert "copy_session_clip_to_arrangement" not in unwarped.calls
    playing = AudioLive(playing=True)
    configure_ableton_client(playing)
    assert place_indexed_sample(sample_id, title="SONG", role="fx", confirm=True)["status"] == "blocked"
    assert playing.calls == ["get_state"]
    configure_ableton_client(None)


def test_song_confirm_does_not_place_ranked_samples(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "splice"
    _wav(root / "percussion_loop_110bpm.wav")
    _isolate(monkeypatch, tmp_path, root)
    index_library()
    sample_id = search_indexed(role="percussion")[0]["sample_id"]
    select_sample(sample_id, "percussion")
    monkeypatch.setattr(
        "kihachi_mcp.gateway.live_tools.execute_song_plan",
        lambda plan: {"ok": True, "status": "applied", "live_changed": True},
    )
    placed: list[str] = []
    monkeypatch.setattr(
        "kihachi_mcp.gateway.samples.place.place_indexed_sample",
        lambda *args, **kwargs: placed.append("called") or {"ok": True},
    )
    result = produce_ableton_song(confirm=True, sample_selection="automatic")
    assert result["status"] == "applied"
    assert result["sample_plan"]["adopted"] == []
    assert placed == []
    assisted = produce_ableton_song(confirm=True, sample_selection="assisted")
    assert assisted["sample_plan"]["adopted"][0]["sample_id"] == sample_id
    assert placed == []


def test_paths_quotes_reindex_and_unsupported_formats(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "packs"
    audio = root / "snare_oneshot_110bpm.wav"
    _wav(audio)
    before = audio.stat().st_mtime_ns
    (root / "notes.txt").write_text("not audio", encoding="utf-8")
    (root / "loop.mp3").write_bytes(b"not audio")
    (root / "broken.wav").write_bytes(b"not a wav")
    quoted = f'"{root}"; "{tmp_path / "missing"}"'
    monkeypatch.setenv("KIHACHI_SAMPLE_LIBRARY_PATHS", quoted)
    assert library_roots() == [root, tmp_path / "missing"]
    _isolate(monkeypatch, tmp_path, None)
    monkeypatch.setenv("KIHACHI_SAMPLE_LIBRARY_PATHS", quoted)
    first = index_library()
    assert first["indexed_count"] == 1
    assert first["unreadable_count"] == 1
    sample_id = search_indexed(role="snare")[0]["sample_id"]
    second = index_library()
    assert second["indexed_count"] == 1
    assert search_indexed(role="snare")[0]["sample_id"] == sample_id
    assert audio.stat().st_mtime_ns == before
    drums = score_sample(
        _record("kick_oneshot_deep.wav", 0.2),
        sample_requirements(110, "D# minor")[1],
    )
    assert drums["role"] == 0.0
    hat = filename_metadata("closed_hat_oneshot.wav")
    assert hat["role"] == "closed_hat"
    fake = AudioLive()
    configure_ableton_client(fake)
    found = find_samples_for_song(title="Mutant Dub Circuit")
    assert fake.calls == []
    assert found["live_changed"] is False
    roles = [group["role"] for group in found["sample_plan"]["groups"]]
    assert roles == ["kick", "drums", "percussion", "vocal", "fx"]
    assert found["sample_plan"]["adopted"] == []
    meta = get_sample_metadata(sample_id)
    assert meta["sample"]["metadata_source"] == "filename"
    assert str(root) not in json.dumps(meta)
    configure_ableton_client(None)


def test_sample_tools_are_registered() -> None:
    from kihachi_mcp.server import mcp

    names = {tool.name for tool in asyncio.run(mcp.list_tools())}
    assert {
        "sample_library_status",
        "index_sample_library",
        "search_samples",
        "get_sample",
        "get_sample_metadata",
        "find_samples_for_song",
        "select_sample",
        "place_sample",
    } <= names
