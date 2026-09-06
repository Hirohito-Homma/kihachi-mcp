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
