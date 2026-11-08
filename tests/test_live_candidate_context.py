"""Live overview facts must not override explicit musical instructions."""

import pytest

from kihachi_mcp.services.live_candidate_context import brief_from_live
from kihachi_mcp.services.live_state_inspector import LiveStateInspector
from kihachi_mcp.services.live_transport_fake import FakeLiveSet, FakeLiveTransport


def _snapshot(*, tempo: float = 124, numerator: int = 4):
    live = FakeLiveSet(tempo=tempo, numerator=numerator)
    return LiveStateInspector(FakeLiveTransport(live)).snapshot()


def test_live_tempo_fills_only_missing_tempo() -> None:
    generated = brief_from_live(_snapshot(), "D minor、8小節のテクノ")
    assert generated["brief"] == "124 BPM。D minor、8小節のテクノ"
    assert generated["sources"] == {"tempo": "live"}
    assert generated["note_content_used"] is False

    explicit = brief_from_live(_snapshot(), "128 BPM、D minor、8小節")
    assert explicit["brief"] == "128 BPM、D minor、8小節"
    assert explicit["sources"] == {"tempo": "user"}


def test_unsupported_meter_is_not_silently_changed() -> None:
    with pytest.raises(ValueError, match="4/4"):
        brief_from_live(_snapshot(numerator=3), "D minor、8小節")
