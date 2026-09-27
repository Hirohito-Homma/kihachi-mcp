"""Message contract parity between the Python side and the Max for Live device.

Max is not installed in CI, so this suite cannot prove the device works inside
Ableton Live. What it can prove is that the JavaScript source and the Python
services agree on the protocol: the same name, version, methods, operations,
precondition kinds, and ownership markers.

Real-device behaviour is covered by the manual checklist in
``docs/MANUAL_LIVE_TESTS.md``.
"""

import re
from pathlib import Path

import pytest

from kihachi_mcp.models.live_contract import (
    MANAGED_CLIP_PREFIX,
    MANAGED_MARKER,
    SCHEMA_VERSION,
    SUPPORTED_OPS,
)
from kihachi_mcp.services.live_device_catalog import STOCK_DEVICE_NAMES
from kihachi_mcp.services.live_transport import (
    PROTOCOL_NAME,
    PROTOCOL_VERSION,
    SUPPORTED_METHODS,
)

DEVICE_SOURCE = (
    Path(__file__).resolve().parents[1] / "maxforlive" / "kihachi.device.js"
)

PRECONDITION_KINDS = {
    "not_recording",
    "transport_stopped",
    "track_name_at_index",
    "track_missing",
    "scene_name_at_index",
    "session_slot_empty",
    "clip_is_managed",
    "device_available",
    "arrangement_range_free",
}


@pytest.fixture(scope="module")
def source() -> str:
    """Return the Max device JavaScript source."""
    return DEVICE_SOURCE.read_text(encoding="utf-8")


def _js_constant(source: str, name: str) -> str:
    match = re.search(rf'var {name} = "([^"]+)";', source)
    assert match is not None, f"{name} is not declared in the device source"
    return match.group(1)


def _js_number(source: str, name: str) -> int:
    match = re.search(rf"var {name} = (\d+);", source)
    assert match is not None, f"{name} is not declared in the device source"
    return int(match.group(1))


def test_the_device_source_exists() -> None:
    assert DEVICE_SOURCE.is_file()


def test_protocol_name_and_versions_match(source: str) -> None:
    assert _js_constant(source, "PROTOCOL_NAME") == PROTOCOL_NAME
    assert _js_number(source, "PROTOCOL_VERSION") == PROTOCOL_VERSION
    assert _js_number(source, "SCHEMA_VERSION") == SCHEMA_VERSION


def test_every_python_method_is_handled_by_the_device(source: str) -> None:
    for method in SUPPORTED_METHODS:
        assert f'request.method === "{method}"' in source, method


def test_every_python_operation_is_handled_by_the_device(source: str) -> None:
    for op in SUPPORTED_OPS:
        assert f'op === "{op}"' in source, op


def test_every_precondition_kind_is_checked_by_the_device(source: str) -> None:
    for kind in PRECONDITION_KINDS:
        assert f'kind === "{kind}"' in source, kind


def test_the_planner_only_emits_precondition_kinds_the_device_knows() -> None:
    planner_source = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "kihachi_mcp"
        / "services"
    )
    emitted: set[str] = set()
    for module in ("live_mutation_planner.py", "arrangement_expander.py"):
        text = (planner_source / module).read_text(encoding="utf-8")
        emitted.update(re.findall(r'LivePrecondition\(\s*"([a-z_]+)"', text))

    assert emitted
    assert emitted <= PRECONDITION_KINDS


def test_ownership_markers_match(source: str) -> None:
    assert _js_constant(source, "MANAGED_MARKER") == MANAGED_MARKER
    assert _js_constant(source, "MANAGED_CLIP_PREFIX") == f"[{MANAGED_CLIP_PREFIX}"


def test_the_device_reports_only_catalogued_stock_devices(source: str) -> None:
    block = re.search(
        r"var AVAILABLE_DEVICES = \[(.*?)\];", source, re.DOTALL
    )
    assert block is not None
    declared = set(re.findall(r'"([^"]+)"', block.group(1)))

    assert declared == set(STOCK_DEVICE_NAMES)


def test_the_device_refuses_requests_without_a_valid_token(source: str) -> None:
    assert "tokenIsValid(request.token)" in source
    assert '"unauthorized", "session token rejected"' in source


def test_the_device_never_echoes_the_supplied_token(source: str) -> None:
    unauthorized = re.search(
        r'respondError\(requestId, "unauthorized"[^)]*\)', source
    )
    assert unauthorized is not None
    assert "request.token" not in unauthorized.group(0)


def test_the_device_rejects_duplicate_request_ids(source: str) -> None:
    assert "seenRequestIds[requestId]" in source
    assert '"duplicate_request"' in source


def test_the_device_never_saves_the_set(source: str) -> None:
    assert 'call("save' not in source
    assert "save_set" not in source


def test_the_device_does_not_delete_tracks_scenes_or_clips(source: str) -> None:
    for forbidden in (
        "delete_track",
        "delete_scene",
        "delete_clip",
        "delete_device",
    ):
        assert forbidden not in source, forbidden


def test_the_device_source_avoids_es6_syntax(source: str) -> None:
    """The Max [js] engine is ES5; ES6 syntax fails to load at runtime."""
    code = "\n".join(
        line
        for line in source.splitlines()
        if not line.strip().startswith(("*", "/*", "//"))
    )

    assert not re.search(r"\bconst\s+\w+\s*=", code)
    assert not re.search(r"\blet\s+\w+\s*=", code)
    assert "=>" not in code
    assert "`" not in code
