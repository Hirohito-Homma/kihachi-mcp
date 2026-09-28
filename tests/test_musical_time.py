from kihachi_mcp.services.musical_time import duration_minutes
from kihachi_mcp.services.song_service import SongService


def test_duration_uses_bars_meter_and_tempo() -> None:
    assert duration_minutes(96, 125, 4, 4) == 96 * 4 / 125


def test_song_service_exposes_meter_duration_without_breaking_legacy_grid() -> None:
    service = SongService()
    assert service.duration_from_meter(96, 125) == 96 * 4 / 125
    assert service.bars_from_minutes(5) == 160
