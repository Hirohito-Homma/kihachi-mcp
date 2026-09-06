from pathlib import Path

import pytest

from kihachi_mcp.knowledge.errors import InvalidGenreTemplateError
from kihachi_mcp.knowledge.genre_loader import load_genre_template
from kihachi_mcp.models import Arrangement, GenreTemplate


def test_load_dub_techno_yaml() -> None:
    path = (
        Path(__file__).resolve().parents[1]
        / "src/kihachi_mcp/resources/genres/dub_techno.yaml"
    )
    template = load_genre_template(path)

    assert template == GenreTemplate(
        name="Dub Techno",
        default_bpm=110,
        default_key="D#m",
        tracks=["Kick", "Bass", "Dub Chords", "Pad", "FX"],
        arrangement=[
            Arrangement(name="Intro", start_bar=1, length_bars=32),
            Arrangement(name="Build", start_bar=33, length_bars=32),
            Arrangement(name="Drop", start_bar=65, length_bars=64),
            Arrangement(name="Breakdown", start_bar=129, length_bars=16),
            Arrangement(name="Outro", start_bar=145, length_bars=16),
        ],
        mood="Deep",
    )


def test_invalid_yaml_not_a_mapping(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text("- just a list\n", encoding="utf-8")

    with pytest.raises(InvalidGenreTemplateError, match="mapping"):
        load_genre_template(path)


def test_invalid_yaml_missing_fields(tmp_path: Path) -> None:
    path = tmp_path / "incomplete.yaml"
    path.write_text("name: Incomplete\n", encoding="utf-8")

    with pytest.raises(InvalidGenreTemplateError, match="missing required fields"):
        load_genre_template(path)
