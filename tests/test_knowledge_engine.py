import pytest

from kihachi_mcp.knowledge import KnowledgeEngine, UnknownGenreError


def test_knowledge_engine_loads_dub_techno() -> None:
    template = KnowledgeEngine().genre("dub techno")

    assert template.name == "Dub Techno"
    assert template.default_bpm == 110
    assert template.tracks == ["Kick", "Bass", "Dub Chords", "Pad", "FX"]


def test_knowledge_engine_normalizes_names() -> None:
    engine = KnowledgeEngine()

    assert engine.genre("Dub Techno").name == engine.genre("dub_techno").name


def test_unknown_genre_raises() -> None:
    with pytest.raises(UnknownGenreError, match="Unknown genre: jungle"):
        KnowledgeEngine().genre("jungle")


def test_blank_genre_raises() -> None:
    with pytest.raises(UnknownGenreError):
        KnowledgeEngine().genre("   ")
