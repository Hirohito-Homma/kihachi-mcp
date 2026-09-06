from kihachi_mcp.models import ReviewResult, SongSpec
from kihachi_mcp.services import ReviewService, SongService


def test_create_review_result() -> None:
    result = ReviewService().create_review_result(
        approved=True,
        score=0.9,
        comments=["tight low end", "tight low end", ""],
    )

    assert result == ReviewResult(
        approved=True,
        score=0.9,
        comments=["tight low end"],
    )


def test_aggregate_comments_flattens_and_deduplicates() -> None:
    comments = ReviewService().aggregate_comments(
        ["keep the kick dry", ""],
        ["keep the kick dry", "watch the FX"],
    )

    assert comments == ["keep the kick dry", "watch the FX"]


def test_review_songspec_approves_valid_spec() -> None:
    spec = SongService().generate_songspec(
        genre="dub techno",
        tempo=110,
        key="D#m",
        length_minutes=5,
        mood="hypnotic",
    )

    result = ReviewService().review_songspec(spec)

    assert result.approved is True
    assert result.score == 1.0
    assert result.comments == []


def test_review_songspec_rejects_invalid_spec() -> None:
    spec = SongSpec(
        genre="",
        tempo=0,
        key="",
        length_minutes=0,
        bars=0,
        tracks=[],
    )

    result = ReviewService().review_songspec(spec)

    assert result.approved is False
    assert result.score == 0.0
    assert result.comments
