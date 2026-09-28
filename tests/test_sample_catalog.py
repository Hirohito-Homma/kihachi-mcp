from pathlib import Path

import pytest

from kihachi_mcp.services.reference_analysis import ReferenceError
from kihachi_mcp.services.sample_catalog import SampleCatalog


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
