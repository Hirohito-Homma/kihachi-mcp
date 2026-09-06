from kihachi_mcp.api import Brain
from kihachi_mcp.models import Arrangement, ProjectPlan, ReviewResult, SongSpec


def _brain() -> Brain:
    return Brain()


def test_generate_song() -> None:
    spec = _brain().generate_song(
        genre="dub techno",
        tempo=110,
        key="D#m",
        length_minutes=5,
        mood="hypnotic",
    )

    assert spec == SongSpec(
        genre="dub techno",
        tempo=110,
        key="D#m",
        length_minutes=5,
        bars=160,
        tracks=["Kick", "Bass", "Dub Chords", "Pad", "FX"],
    )


def test_create_project_from_song_and_dict() -> None:
    brain = _brain()
    spec = brain.generate_song(
        genre="dub techno",
        tempo=110,
        key="D#m",
        length_minutes=5,
        mood="hypnotic",
    )
    plan = brain.create_project(spec)

    assert isinstance(plan, ProjectPlan)
    assert plan.project_name == "Untitled"
    assert brain.create_project(spec.to_dict()).to_dict() == plan.to_dict()


def test_review_song_approves_generated_spec() -> None:
    brain = _brain()
    spec = brain.generate_song(
        genre="dub techno",
        tempo=110,
        key="D#m",
        length_minutes=5,
        mood="hypnotic",
    )
    result = brain.review_song(spec)

    assert isinstance(result, ReviewResult)
    assert result.approved is True
    assert result.score == 1.0


def test_review_song_accepts_dict() -> None:
    spec = _brain().generate_song(
        genre="dub techno",
        tempo=110,
        key="D#m",
        length_minutes=5,
        mood="hypnotic",
    )

    assert _brain().review_song(spec.to_dict()).approved is True


def test_default_tracks() -> None:
    assert _brain().default_tracks("dub techno") == [
        "Kick",
        "Bass",
        "Dub Chords",
        "Pad",
        "FX",
    ]


def test_default_arrangement() -> None:
    sections = _brain().default_arrangement("dub techno", 160)

    assert [section.name for section in sections] == [
        "Intro",
        "Build",
        "Drop",
        "Breakdown",
        "Outro",
    ]
    assert isinstance(sections[0], Arrangement)
    assert sum(section.length_bars for section in sections) == 160


def test_generate_song_falls_back_to_defaults() -> None:
    spec = _brain().generate_song(
        genre="dub techno",
        tempo=0,
        key="",
        length_minutes=5,
        mood="hypnotic",
    )

    assert spec.tempo == 110
    assert spec.key == "D#m"


def test_generate_song_can_omit_knowledge_backed_inputs() -> None:
    spec = _brain().generate_song(genre="dub techno", length_minutes=5)

    assert spec.tempo == 110
    assert spec.key == "D#m"


def test_create_project_adds_knowledge_arrangement() -> None:
    brain = _brain()
    spec = brain.generate_song(genre="dub techno", length_minutes=5)

    plan = brain.create_project(spec)

    assert [section.name for section in plan.arrangement] == [
        "Intro",
        "Build",
        "Drop",
        "Breakdown",
        "Outro",
    ]
    assert sum(section.length_bars for section in plan.arrangement) == plan.bars


def test_create_project_preserves_unknown_genre_compatibility() -> None:
    spec = SongSpec(
        genre="custom",
        tempo=120,
        key="Am",
        length_minutes=1,
        bars=32,
        tracks=["Kick"],
    )

    plan = _brain().create_project(spec)

    assert plan.genre == "custom"
    assert plan.arrangement == []


def test_memory_remembers_and_searches_by_genre() -> None:
    brain = _brain()
    spec = brain.generate_song(genre="dub techno", length_minutes=5)

    entry = brain.remember_song(spec, brain.review_song(spec))

    assert entry.genre == "dub techno"
    assert brain.search_memory("DUB TECHNO")[0].to_dict()["review"]["approved"] is True


def test_memory_isolated_between_brains() -> None:
    spec = _brain().generate_song(genre="dub techno", length_minutes=5)
    first = _brain()
    first.remember_song(spec)

    assert _brain().search_memory() == []


def test_orchestrate_song_runs_complete_workflow() -> None:
    result = _brain().orchestrate_song("dub techno", length_minutes=5)

    assert result.songspec.tempo == 110
    assert result.review.approved is True
    assert result.memory.review == result.review.to_dict()
    assert len(result.project.arrangement) == 5


def test_memory_persists_entries_to_json(tmp_path) -> None:
    from kihachi_mcp.services import MemoryService

    path = tmp_path / "memory.json"
    spec = _brain().generate_song(genre="dub techno", length_minutes=5)

    MemoryService(path).remember(spec.to_dict())
    restored = MemoryService(path)

    assert restored.search("dub techno")[0].songspec == spec.to_dict()


def test_orchestrator_can_stop_after_failed_review() -> None:
    from kihachi_mcp.services import ReviewService

    class FailingReviewService(ReviewService):
        def review_songspec(self, songspec):
            return ReviewResult(False, 0.0, ["needs revision"])

    brain = Brain(review_service=FailingReviewService())
    result = brain.orchestrate_song(
        "dub techno", length_minutes=5, stop_on_review_failure=True
    )

    assert result.review.approved is False
    assert result.project is None
    assert result.memory.review == result.review.to_dict()
