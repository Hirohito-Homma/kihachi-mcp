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


def test_aggregate_scores_returns_mean() -> None:
    assert ReviewService().aggregate_scores([0.5, 1.0, 0.0]) == 0.5


def test_aggregate_scores_empty_is_zero() -> None:
    assert ReviewService().aggregate_scores([]) == 0.0


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


def test_comments_match_validation() -> None:
    spec = SongService().generate(
        genre="dub techno",
        tempo=110,
        key="D#m",
        length_minutes=5,
        mood="hypnotic",
    )

    assert ReviewService().comments(spec) == []


def test_warnings_for_extreme_tempo() -> None:
    spec = SongSpec(
        genre="dub techno",
        tempo=200,
        key="D#m",
        length_minutes=5,
        bars=160,
        tracks=["Kick", "Bass", "Dub Chords", "Lead", "FX"],
    )

    assert "tempo is outside the common 60-180 BPM range" in ReviewService().warnings(
        spec
    )


def test_suggestions_for_missing_fx() -> None:
    spec = SongSpec(
        genre="dub techno",
        tempo=110,
        key="D#m",
        length_minutes=5,
        bars=160,
        tracks=["Kick", "Bass"],
    )

    assert "add an FX track for transitions" in ReviewService().suggestions(spec)


def test_warnings_for_short_and_long_length() -> None:
    short = SongSpec(
        genre="dub techno",
        tempo=110,
        key="D#m",
        length_minutes=1,
        bars=32,
        tracks=["Kick", "Bass", "Dub Chords", "Lead", "FX"],
    )
    long = SongSpec(
        genre="dub techno",
        tempo=110,
        key="D#m",
        length_minutes=15,
        bars=480,
        tracks=["Kick", "Bass", "Dub Chords", "Lead", "FX"],
    )

    assert "length is under 2 minutes" in ReviewService().warnings(short)
    assert "length is over 12 minutes" in ReviewService().warnings(long)


def test_suggestions_for_missing_kick_and_off_grid_bars() -> None:
    spec = SongSpec(
        genre="dub techno",
        tempo=110,
        key="D#m",
        length_minutes=5,
        bars=161,
        tracks=["Bass", "FX"],
    )
    suggestions = ReviewService().suggestions(spec)

    assert "add a Kick track to anchor the groove" in suggestions
    assert "round bars to a 16-bar grid for easier arrangement" in suggestions


def test_score_is_zero_when_invalid() -> None:
    spec = SongSpec(
        genre="",
        tempo=0,
        key="",
        length_minutes=0,
        bars=0,
        tracks=[],
    )

    assert ReviewService().score(spec) == 0.0


def test_score_is_one_for_standard_spec() -> None:
    spec = SongService().generate(
        genre="dub techno",
        tempo=110,
        key="D#m",
        length_minutes=5,
        mood="hypnotic",
    )

    assert ReviewService().score(spec) == 1.0


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
