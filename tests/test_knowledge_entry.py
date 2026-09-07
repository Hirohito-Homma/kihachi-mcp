import pytest

from kihachi_mcp.models import (
    Arrangement,
    GenreTemplate,
    KnowledgeContext,
    KnowledgeEntry,
)


def _template() -> GenreTemplate:
    return GenreTemplate(
        name="Dub Techno",
        default_bpm=110,
        default_key="D#m",
        tracks=["Kick", "Bass"],
        arrangement=[Arrangement(name="Intro", start_bar=1, length_bars=32)],
        mood="Deep",
    )


def _entry() -> KnowledgeEntry:
    return KnowledgeEntry(
        id="dub_techno",
        version="1",
        source="genre_yaml",
        kind="genre",
        template=_template(),
    )


def test_valid_knowledge_entry_keeps_structured_template() -> None:
    entry = _entry()

    assert entry.id == "dub_techno"
    assert entry.template is not None
    assert entry.template.default_bpm == 110
    assert entry.to_provenance() == {
        "id": "dub_techno",
        "version": "1",
        "source": "genre_yaml",
        "kind": "genre",
    }


def test_invalid_knowledge_entry_requires_identity_fields() -> None:
    with pytest.raises(ValueError, match="id is required"):
        KnowledgeEntry(id=" ", version="1", source="genre_yaml", kind="genre")
    with pytest.raises(ValueError, match="version is required"):
        KnowledgeEntry(id="dub_techno", version="", source="genre_yaml", kind="genre")
    with pytest.raises(ValueError, match="source is required"):
        KnowledgeEntry(id="dub_techno", version="1", source="", kind="genre")
    with pytest.raises(ValueError, match="kind is required"):
        KnowledgeEntry(id="dub_techno", version="1", source="genre_yaml", kind="")


def test_knowledge_entry_roundtrip() -> None:
    entry = _entry()

    assert KnowledgeEntry.from_dict(entry.to_dict()) == entry


def test_knowledge_entry_and_context_are_frozen() -> None:
    entry = _entry()
    context = KnowledgeContext.from_entries([entry])

    with pytest.raises(AttributeError):
        entry.id = "other"  # type: ignore[misc]
    with pytest.raises(AttributeError):
        context.entries = ()  # type: ignore[misc]


def test_knowledge_context_filters_and_serializes() -> None:
    first = _entry()
    second = KnowledgeEntry(
        id="tech_house",
        version="1",
        source="genre_yaml",
        kind="genre",
        template=_template(),
    )
    other = KnowledgeEntry(
        id="note",
        version="1",
        source="memory",
        kind="memory",
    )
    context = KnowledgeContext.from_entries([first, second, other])

    assert context.select(kind="genre", ids=["dub_techno"]).entries == (first,)
    assert KnowledgeContext.from_dict(context.to_dict()).entries[0].id == "dub_techno"
    assert context.to_provenance()[0]["source"] == "genre_yaml"
