from kihachi_mcp.models.live_contract import SUPPORTED_OPS
from kihachi_mcp.services.live_capabilities import discover_capabilities


def test_capability_matrix_covers_the_required_surface() -> None:
    result = discover_capabilities({"connected": True, "protocol_supported": True, "live_version": "12.4.5"})
    rows = {row["id"]: row for row in result["capabilities"]}
    assert set(rows) == {
        "transport", "tracks", "midi", "audio_clips", "arrangement", "devices",
        "browser", "mixer", "sends", "automation", "undo", "redo", "save",
        "sample_loading", "warp",
    }
    assert rows["midi"]["status"] == "conditional"
    assert rows["warp"]["status"] == "unsupported"
    assert rows["undo"]["runtime_ready"] is False


def test_no_contract_operation_is_invented() -> None:
    result = discover_capabilities({"connected": True, "protocol_supported": True})
    actions = {
        action
        for row in result["capabilities"]
        for action in row["actions"]
        if action != "search_and_load_core_library_kit"
    }
    assert actions <= SUPPORTED_OPS
    assert "play" not in actions
    assert "save_live_set" not in actions


def test_disconnected_bridge_exposes_no_executable_capabilities() -> None:
    result = discover_capabilities({"connected": False})
    assert result["bridge_ready"] is False
    assert result["executable_capability_ids"] == []


def test_browser_is_ready_only_with_abletongpt() -> None:
    health = {"connected": True, "protocol_supported": True}
    offline = discover_capabilities(health, "no_reply")
    ready = discover_capabilities(health, "ready")
    offline_browser = next(row for row in offline["capabilities"] if row["id"] == "browser")
    ready_browser = next(row for row in ready["capabilities"] if row["id"] == "browser")
    assert offline_browser["runtime_ready"] is False
    assert ready_browser["runtime_ready"] is True
    assert ready_browser["actions"] == ["search_and_load_core_library_kit"]
