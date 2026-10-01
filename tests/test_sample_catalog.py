import sqlite3
import wave
from pathlib import Path

import pytest

from kihachi_mcp.services.reference_analysis import ReferenceError
from kihachi_mcp.services.sample_catalog import SampleCatalog


def _silent_wav(path: Path, seconds: float = 0.25) -> None:
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(8_000)
        audio.writeframes(b"\0\0" * int(8_000 * seconds))


def test_indexes_filenames_without_copying_audio(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "Techno Kicks").mkdir()
    kick = source / "Techno Kicks" / "909 kick.wav"
    kick.write_bytes(b"not decoded by catalog")
    (source / "hi_hat.wav").write_bytes(b"hat")
    (source / "notes.txt").write_text("ignored")
    (source / "linked.wav").symlink_to(kick)
    catalog = SampleCatalog(tmp_path / "catalog")

    status = catalog.index(str(source))

    assert status["count"] == 2
    assert status["audio_copied"] is False
    assert catalog.search("kick")["results"][0]["path"] == str(kick)
    assert catalog.search("hats")["count"] == 1
    assert catalog.search("all", "%")["count"] == 0
    assert list((tmp_path / "catalog").iterdir()) == [catalog.path]
    assert catalog.path.stat().st_mode & 0o777 == 0o600
    assert kick.read_bytes() == b"not decoded by catalog"


def test_rejects_invalid_search_and_missing_root(tmp_path: Path) -> None:
    catalog = SampleCatalog(tmp_path / "catalog")
    with pytest.raises(ReferenceError):
        catalog.index("relative/path")
    with pytest.raises(ReferenceError):
        catalog.search("unknown")


def test_indexes_truthful_sample_metadata_and_ranks_song_context(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    dark = source / "Dark Heavy Kick 124 BPM D# minor one-shot.wav"
    bright = source / "Bright Kick 128 BPM.wav"
    _silent_wav(dark)
    _silent_wav(bright)
    catalog = SampleCatalog(tmp_path / "catalog")

    catalog.index(str(source))
    result = catalog.search("kick", "太くて暗いKick", tempo=124, key="D# minor")

    assert result["count"] == 2
    first = result["results"][0]
    assert first["path"] == str(dark)
    assert first["sample_id"]
    assert first["category"] == "kick"
    assert first["bpm"] == 124
    assert first["key"] == "D# minor"
    assert first["sample_kind"] == "one_shot"
    assert first["duration"] == pytest.approx(0.25)
    assert first["loudness"] is None
    assert first["energy"] is None
    assert first["brightness"] is None
    assert first["transient"] is None


def test_indexes_extended_categories_without_inventing_values(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    for name in ("dub perc.wav", "lead vocal.wav", "noise fx.wav", "top loop.wav"):
        _silent_wav(source / name)
    catalog = SampleCatalog(tmp_path / "catalog")

    catalog.index(str(source))

    assert catalog.search("percussion")["count"] == 1
    assert catalog.search("vocal")["count"] == 1
    assert catalog.search("fx")["count"] == 1
    assert catalog.search("loop")["results"][0]["sample_kind"] == "loop"


def test_drum_search_excludes_loops_and_requires_query_match(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _silent_wav(source / "Kick Loop Heavy 124 BPM.wav")
    _silent_wav(source / "Heavy Kick one-shot.wav")
    _silent_wav(source / "Bright Kick 124 BPM.wav")
    catalog = SampleCatalog(tmp_path / "catalog")
    catalog.index(str(source))

    result = catalog.search("kick", "重い", tempo=124)

    assert [item["name"] for item in result["results"]] == [
        "Heavy Kick one-shot.wav"
    ]


def test_existing_filename_catalog_is_migrated_in_place(tmp_path: Path) -> None:
    directory = tmp_path / "catalog"
    directory.mkdir()
    source = tmp_path / "source"
    source.mkdir()
    sample = source / "Dark Kick 124 BPM D# minor one-shot.wav"
    _silent_wav(sample)
    database = directory / "sample_catalog.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.executescript("""
            CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE files (
                relative_path TEXT PRIMARY KEY, name TEXT NOT NULL,
                role TEXT NOT NULL, bytes INTEGER NOT NULL
            );
        """)
        connection.execute("INSERT INTO settings VALUES ('root', ?)", (str(source),))
        connection.execute(
            "INSERT INTO files VALUES (?, ?, 'kick', ?)",
            (sample.name, sample.name, sample.stat().st_size),
        )
    catalog = SampleCatalog(directory)

    status = catalog.status()

    assert status["ok"] is True
    with sqlite3.connect(database) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(files)")}
    assert {"sample_id", "bpm", "musical_key", "duration", "tags"} <= columns
    result = catalog.search("kick", tempo=124, key="D# minor")["results"][0]
    assert result["sample_id"]
    assert result["bpm"] == 124
    assert result["key"] == "D# minor"
    assert result["sample_kind"] == "one_shot"
    assert result["duration"] is None
    assert catalog.audio_path(result["sample_id"]) == sample
