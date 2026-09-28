import hashlib
import json
import math
import shutil
import sqlite3
import wave

import numpy as np
import pytest

from kihachi_mcp.services.reference_analysis import VERSION, ReferenceError, analyze
from kihachi_mcp.services.reference_library import ReferenceLibrary


def write_wav(path, frequency=100, seconds=1, amplitude=0.25, stereo=False):
    rate = 22050
    t = np.arange(int(rate * seconds)) / rate
    data = np.sin(2 * math.pi * frequency * t) * amplitude
    if stereo:
        data = np.stack([data, -data], axis=1)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(2 if stereo else 1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes((data * 32767).astype("<i2").tobytes())
    return path


def metadata(**kwargs):
    return {
        "title": "Reference",
        "source": "local",
        "source_url": "",
        "license": "自作",
        "creator": "Test",
        "kind": "track",
        "genres": "dub_techno",
        "rights_confirmed": True,
        **kwargs,
    }


def fake_analysis(_path):
    return {
        "version": VERSION,
        "metrics": {"integrated_lufs": -14, "low_ratio": 0.4},
        "tempo_candidates": [],
        "warnings": [],
    }


def test_three_sources_persist_and_shared_content_is_counted_once(tmp_path):
    original = write_wav(tmp_path / "original.wav")
    before = original.read_bytes()
    calls = []

    def analyzer(path):
        calls.append(path)
        return fake_analysis(path)

    library = ReferenceLibrary(tmp_path / "library", analyzer)
    local = library.import_file(original, metadata())
    fs = library.import_file(
        original,
        metadata(
            source="freesound",
            source_url="https://freesound.org/people/test/sounds/1/",
            license="CC0",
        ),
    )
    jm = library.import_file(
        original,
        metadata(
            source="jamendo",
            source_url="https://www.jamendo.com/track/1",
            license="CC BY",
        ),
    )
    assert len(calls) == 1
    assert len({r["entry"]["id"] for r in (local, fs, jm)}) == 3
    assert original.read_bytes() == before
    assert len(list((tmp_path / "library/audio").iterdir())) == 1
    assert (tmp_path / "library").stat().st_mode & 0o777 == 0o700
    assert (tmp_path / "library/library.sqlite3").stat().st_mode & 0o777 == 0o600
    assert (tmp_path / "library/audio").stat().st_mode & 0o777 == 0o700
    reopened = ReferenceLibrary(tmp_path / "library", analyzer)
    assert len(reopened.entries()) == 3
    assert reopened.compare("dub_techno", "track")["count"] == 1
    duplicate = reopened.import_file(original, metadata(title="Should not overwrite"))
    assert duplicate["duplicate"] is True
    assert duplicate["entry"]["title"] == "Reference"
    assert len(calls) == 1


def test_comparison_excludes_target_and_separates_kind_genre_and_unconfirmed_bpm(
    tmp_path,
):
    library = ReferenceLibrary(tmp_path / "library", fake_analysis)
    target = library.import_file(write_wav(tmp_path / "target.wav", 100), metadata())
    library.import_file(
        write_wav(tmp_path / "peer.wav", 110), metadata(confirmed_bpm=125)
    )
    library.import_file(
        write_wav(tmp_path / "kick.wav", 120),
        metadata(kind="one_shot", confirmed_bpm=80),
    )
    library.import_file(
        write_wav(tmp_path / "other.wav", 130), metadata(genres="tech_house")
    )
    result = library.compare("dub_techno", "track", target["entry"]["id"])
    assert result["count"] == 1
    assert result["confirmed_bpm"] == {"n": 1, "median": 125}
    assert result["metrics"]["integrated_lufs"]["delta"] == 0
    with pytest.raises(ReferenceError, match="種類"):
        library.compare("dub_techno", "loop", target["entry"]["id"])
    with pytest.raises(ReferenceError, match="同じジャンル"):
        library.compare("tech_house", "track", target["entry"]["id"])
    empty = library.compare("dub_techno", "stem")
    assert empty["count"] == 0
    assert empty["metrics"]["integrated_lufs"]["median"] is None


def test_one_shot_role_comparison_keeps_kicks_and_hats_separate(tmp_path):
    library = ReferenceLibrary(tmp_path / "library", fake_analysis)
    kick = library.import_file(
        write_wav(tmp_path / "kick.wav", 100),
        metadata(kind="one_shot", sample_role="kick"),
    )["entry"]
    library.import_file(
        write_wav(tmp_path / "hat.wav", 1000),
        metadata(kind="one_shot", sample_role="hats"),
    )
    assert library.compare("dub_techno", "one_shot", sample_role="kick")["count"] == 1
    assert library.compare("dub_techno", "one_shot", sample_role="hats")["count"] == 1
    assert library.compare("dub_techno", "one_shot")["count"] == 2
    with pytest.raises(ReferenceError, match="役割"):
        library.compare("dub_techno", "one_shot", kick["id"], "hats")
    changed = library.update(kick["id"], {"sample_role": "snare"})["entry"]
    assert changed["sample_role"] == "snare"
    assert library.compare("dub_techno", "one_shot", sample_role="kick")["count"] == 0


def test_sample_role_is_only_valid_for_one_shots(tmp_path):
    library = ReferenceLibrary(tmp_path / "library", fake_analysis)
    with pytest.raises(ReferenceError, match="役割"):
        library.import_file(
            write_wav(tmp_path / "invalid.wav"), metadata(sample_role="kick")
        )
    assert not library.directory.exists()


def test_corrections_do_not_change_audio_or_machine_analysis(tmp_path):
    library = ReferenceLibrary(tmp_path / "library", fake_analysis)
    result = library.import_file(write_wav(tmp_path / "test.wav"), metadata())
    original = result["entry"]
    edited = library.update(
        original["id"],
        {"genres": "Tech House", "confirmed_bpm": 124, "notes": "耳で確認"},
    )["entry"]
    assert edited["genres"] == ["tech_house"]
    assert edited["confirmed_bpm"] == 124
    assert edited["analysis"] == original["analysis"]
    assert edited["sha256"] == original["sha256"]
    assert library.compare("dub_techno", "track")["count"] == 0
    assert library.compare("tech_house", "track")["count"] == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"rights_confirmed": False},
        {"source": "apple"},
        {"kind": "invalid"},
        {"license": ""},
        {"genres": "unknown_genrex"},
        {"confirmed_bpm": "nan"},
        {"source": "jamendo", "source_url": "https://example.com/track/1"},
        {"source_url": "https://user:secret@example.com"},
        {"source_url": "https://freesound.org/?token=secret"},
    ],
)
def test_invalid_metadata_never_stores_audio(tmp_path, changes):
    library = ReferenceLibrary(tmp_path / "library", fake_analysis)
    with pytest.raises(ReferenceError):
        library.import_file(write_wav(tmp_path / "test.wav"), metadata(**changes))
    assert not library.directory.exists()


def test_analysis_failure_leaves_no_asset_or_entry(tmp_path):
    def fail(_):
        raise ReferenceError("bad audio")

    library = ReferenceLibrary(tmp_path / "library", fail)
    with pytest.raises(ReferenceError, match="bad audio"):
        library.import_file(write_wav(tmp_path / "test.wav"), metadata())
    assert library.entries() == []
    assert list((library.directory / "audio").iterdir()) == []


@pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
    reason="FFmpeg not installed",
)
def test_signal_measurements_match_known_tones_and_preserve_antiphase(tmp_path):
    low = analyze(write_wav(tmp_path / "low.wav", 100, seconds=10, stereo=True))
    high = analyze(write_wav(tmp_path / "high.wav", 4000, seconds=10))
    assert low["metrics"]["rms_dbfs"] == pytest.approx(
        20 * math.log10(0.25 / math.sqrt(2)), abs=0.02
    )
    assert low["metrics"]["low_ratio"] > 0.99
    assert low["metrics"]["spectral_centroid_hz"] == pytest.approx(100, abs=2)
    assert low["metrics"]["stereo_correlation"] == pytest.approx(-1)
    assert high["metrics"]["high_ratio"] > 0.99
    assert high["metrics"]["spectral_centroid_hz"] == pytest.approx(4000, abs=2)
    assert low["metrics"]["true_peak_dbtp"] == pytest.approx(-12.04, abs=0.1)
    assert high["metrics"]["stereo_correlation"] is None
    assert low["tempo_candidates"] == []
    assert high["tempo_candidates"] == []


@pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
    reason="FFmpeg not installed",
)
def test_silence_has_no_invented_measurements_or_tempo(tmp_path):
    original = write_wav(tmp_path / "silence.wav", seconds=10, amplitude=0)
    sha = hashlib.sha256(original.read_bytes()).hexdigest()
    result = analyze(original)
    assert result["metrics"]["rms_dbfs"] is None
    assert result["metrics"]["integrated_lufs"] is None
    assert result["metrics"]["low_ratio"] is None
    assert result["tempo_candidates"] == []
    assert hashlib.sha256(original.read_bytes()).hexdigest() == sha


@pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
    reason="FFmpeg not installed",
)
def test_click_track_has_120_bpm_candidate(tmp_path):
    path = tmp_path / "click.wav"
    data = np.zeros(22050 * 12, dtype="<i2")
    pulse = (
        np.sin(np.arange(2205) * 2 * math.pi * 1000 / 22050)
        * np.exp(-np.arange(2205) / 300)
        * 16000
    ).astype("<i2")
    for start in range(0, len(data) - len(pulse), 11025):
        data[start : start + len(pulse)] = pulse
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(22050)
        wav.writeframes(data.tobytes())
    result = analyze(path)
    assert any(abs(item["bpm"] - 120) < 3 for item in result["tempo_candidates"])


def test_mix_findings_report_only_measured_differences_against_other_assets(tmp_path):
    values = {
        "first.wav": (0.2, -15.0),
        "second.wav": (0.4, -13.0),
        "target.wav": (0.6, -11.0),
    }

    def analyzer(path):
        low, loudness = values[path.read_text()]
        return {
            "version": VERSION,
            "metrics": {"low_ratio": low, "integrated_lufs": loudness},
            "warnings": [],
        }

    library = ReferenceLibrary(tmp_path / "db", analyzer)
    ids = {}
    for name in values:
        path = tmp_path / name
        path.write_bytes(name.encode())
        ids[name] = library.import_file(path, metadata(title=name))["entry"]["id"]
    comparison = library.compare("dub_techno", "track", ids["target.wav"])
    assert comparison["count"] == 2
    low = next(row for row in comparison["findings"] if row["metric"] == "low_ratio")
    loudness = next(
        row for row in comparison["findings"] if row["metric"] == "integrated_lufs"
    )
    assert low["difference"] == 30.0
    assert low["sample_count"] == 2
    assert loudness["difference"] == 3.0
    assert not any(row["metric"] == "high_ratio" for row in comparison["findings"])
    assert library.compare("dub_techno", "track")["findings"] == []


def test_mix_findings_suppress_single_reference(tmp_path):
    def analyzer(path):
        return {
            "version": VERSION,
            "metrics": {"high_ratio": 0.8 if path.read_text() == "target.wav" else 0.2},
            "warnings": [],
        }

    library = ReferenceLibrary(tmp_path / "db", analyzer)
    ids = []
    for name in ("reference.wav", "target.wav"):
        path = tmp_path / name
        path.write_bytes(name.encode())
        ids.append(library.import_file(path, metadata(title=name))["entry"]["id"])
    assert library.compare("dub_techno", "track", ids[-1])["findings"] == []


def test_mutashon_alias_joins_mutation_funk_without_rewriting_existing_entries(tmp_path):
    library = ReferenceLibrary(tmp_path / "db", fake_analysis)
    result = library.import_file(
        write_wav(tmp_path / "song.wav"), metadata(genres="Mutashon Funk")
    )
    assert result["entry"]["genres"] == ["mutation_funk"]
    assert library.custom_genres() == []
    assert library.compare("Mutashon Funk", "track")["count"] == 1
    assert library.compare("mutation_funk", "track")["count"] == 1
    assert library.compare("funk", "track")["count"] == 0
    # Older user-owned DB rows keep their original spelling on disk.
    with sqlite3.connect(library.db_path) as db:
        row = db.execute("SELECT metadata FROM entries WHERE id=?", (result["entry"]["id"],)).fetchone()
        old = json.loads(row[0])
        old["genres"] = ["Mutashon Funk"]
        db.execute("UPDATE entries SET metadata=? WHERE id=?", (json.dumps(old), result["entry"]["id"]))
    assert library.compare("mutation_funk", "track")["count"] == 1
    with sqlite3.connect(library.db_path) as db:
        stored = json.loads(db.execute("SELECT metadata FROM entries WHERE id=?", (result["entry"]["id"],)).fetchone()[0])
    assert stored["genres"] == ["Mutashon Funk"]
