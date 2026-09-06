import pytest

from kihachi_mcp.models import ReviewResult


def test_review_result_fields() -> None:
    result = ReviewResult(approved=True, score=0.92, comments=["tight low end"])

    assert result.approved is True
    assert result.score == 0.92
    assert result.comments == ["tight low end"]


def test_review_result_to_dict() -> None:
    result = ReviewResult(approved=False, score=0.4, comments=["muddy kick"])

    assert result.to_dict() == {
        "approved": False,
        "score": 0.4,
        "comments": ["muddy kick"],
    }


def test_review_result_from_dict_roundtrip() -> None:
    data = {
        "approved": True,
        "score": 0.8,
        "comments": ["good arrangement", "watch the FX"],
    }

    assert ReviewResult.from_dict(data).to_dict() == data


def test_review_result_from_dict_defaults() -> None:
    result = ReviewResult.from_dict({})

    assert result.approved is False
    assert result.score == 0.0
    assert result.comments == []


def test_review_result_is_frozen() -> None:
    result = ReviewResult(approved=True, score=1.0, comments=[])

    with pytest.raises(AttributeError):
        result.approved = False  # type: ignore[misc]
