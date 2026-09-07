from kihachi_mcp.knowledge import KnowledgeEngine, UnknownGenreError
from kihachi_mcp.services import KnowledgeService


def test_retrieve_returns_genre_knowledge() -> None:
    context = KnowledgeService().retrieve("dub techno")

    assert len(context.entries) == 1
    entry = context.primary()
    assert entry is not None
    assert entry.id == "dub_techno"
    assert entry.source == "genre_yaml"
    assert entry.template is not None
    assert entry.template.tracks == ["Kick", "Bass", "Dub Chords", "Pad", "FX"]


def test_retrieve_unknown_genre_returns_empty_context() -> None:
    context = KnowledgeService().retrieve("jungle")

    assert context.entries == ()
    assert context.template() is None


def test_retrieve_all_returns_multiple_packaged_genres() -> None:
    context = KnowledgeService().retrieve()

    ids = {entry.id for entry in context.entries}
    assert ids == {"dub_techno", "tech_house", "melodic_techno"}


def test_select_skips_unknown_names() -> None:
    context = KnowledgeService().select(["dub techno", "jungle", "tech house"])

    assert [entry.id for entry in context.entries] == ["dub_techno", "tech_house"]


def test_filter_and_get() -> None:
    service = KnowledgeService()
    selected = service.filter(service.retrieve(), ids=["melodic_techno"], kind="genre")

    assert [entry.id for entry in selected.entries] == ["melodic_techno"]
    assert service.get("Dub Techno").template is not None


def test_get_unknown_genre_raises() -> None:
    try:
        KnowledgeService().get("jungle")
    except UnknownGenreError as exc:
        assert exc.genre == "jungle"
    else:
        raise AssertionError("expected UnknownGenreError")


def test_engine_query_matches_service() -> None:
    engine = KnowledgeEngine()

    assert engine.query("tech house").primary() is not None
    assert engine.query("missing").entries == ()
    assert len(engine.select(["dub techno", "tech house"]).entries) == 2
