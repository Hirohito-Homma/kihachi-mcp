"""Startup wiring for the MCP server's Live tools.

The server must start unwired unless the operator opts in, wiring must
never disturb another process that already owns the bridge, and shutdown
must release everything wiring acquired.
"""

import json
import socket
import stat

import pytest

from kihachi_mcp import server as server_module
from kihachi_mcp.services.live_bridge import LocalhostBridgeTransport
from kihachi_mcp.tools import live as live_tools
from kihachi_mcp.tools.live import (
    configure_live_transport_from_environment,
    shutdown_live_transport,
)


@pytest.fixture
def fresh_session(monkeypatch):
    """Isolate the process-global Live session for each wiring test."""
    monkeypatch.setattr(live_tools, "_session", live_tools._LiveSession())
    monkeypatch.setattr(live_tools, "_wired_transport", None)
    yield
    shutdown_live_transport()


def _env(tmp_path, **overrides):
    env = {
        "KIHACHI_LIVE_STATE_DIR": str(tmp_path),
        "KIHACHI_LIVE_BRIDGE_PORT": "17771",
        "KIHACHI_LIVE_REPLY_PORT": "0",
    }
    env.update(overrides)
    return env


def test_no_opt_in_means_no_wiring(fresh_session, tmp_path) -> None:
    assert configure_live_transport_from_environment(_env(tmp_path)) is False
    assert live_tools._session.transport is None
    assert not (tmp_path / "live-bridge.json").exists()


def test_off_and_unknown_values_stay_unwired(fresh_session, tmp_path) -> None:
    for value in ("off", "bogus"):
        env = _env(tmp_path, KIHACHI_MCP_LIVE_TRANSPORT=value)
        assert configure_live_transport_from_environment(env) is False
        assert live_tools._session.transport is None


def test_localhost_value_is_case_and_space_insensitive(
    fresh_session, tmp_path
) -> None:
    env = _env(tmp_path, KIHACHI_MCP_LIVE_TRANSPORT=" LOCALHOST ")
    assert configure_live_transport_from_environment(env) is True
    assert live_tools._session.transport is not None


def test_localhost_wires_the_bridge_and_shutdown_releases_it(
    fresh_session, tmp_path
) -> None:
    env = _env(tmp_path, KIHACHI_MCP_LIVE_TRANSPORT="localhost")

    assert configure_live_transport_from_environment(env) is True

    handshake = tmp_path / "live-bridge.json"
    assert handshake.exists()
    assert stat.S_IMODE(handshake.stat().st_mode) == 0o600
    payload = json.loads(handshake.read_text(encoding="utf-8"))
    assert payload["token"]
    # The handshake reports the port the channel actually bound (the
    # environment asked for port 0, i.e. an OS-assigned port).
    assert payload["reply_port"] > 0
    assert isinstance(live_tools._session.transport, LocalhostBridgeTransport)

    shutdown_live_transport()

    assert live_tools._session.transport is None
    assert live_tools._wired_transport is None
    assert not handshake.exists()


def test_wiring_is_refused_when_the_reply_port_is_taken(
    fresh_session, tmp_path
) -> None:
    blocker = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    blocker.bind(("127.0.0.1", 0))
    taken_port = blocker.getsockname()[1]
    try:
        handshake = tmp_path / "live-bridge.json"
        sentinel = '{"owner": "another process"}\n'
        handshake.write_text(sentinel, encoding="utf-8")
        env = _env(
            tmp_path,
            KIHACHI_MCP_LIVE_TRANSPORT="localhost",
            KIHACHI_LIVE_REPLY_PORT=str(taken_port),
        )

        assert configure_live_transport_from_environment(env) is False

        assert live_tools._session.transport is None
        # The other process's handshake is left exactly as it was.
        assert handshake.read_text(encoding="utf-8") == sentinel
    finally:
        blocker.close()


def test_main_wires_before_running_and_shuts_down_after(monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        server_module,
        "configure_live_transport_from_environment",
        lambda: calls.append("wire") or True,
    )
    monkeypatch.setattr(
        "kihachi_mcp.gateway.serve.serve_stdio", lambda: calls.append("run")
    )
    monkeypatch.setattr(
        server_module, "shutdown_live_transport", lambda: calls.append("shutdown")
    )

    server_module.main()

    assert calls == ["wire", "run", "shutdown"]


def test_main_skips_shutdown_when_nothing_was_wired(monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        server_module,
        "configure_live_transport_from_environment",
        lambda: calls.append("wire") or False,
    )
    monkeypatch.setattr(
        "kihachi_mcp.gateway.serve.serve_stdio", lambda: calls.append("run")
    )
    monkeypatch.setattr(
        server_module, "shutdown_live_transport", lambda: calls.append("shutdown")
    )

    server_module.main()

    assert calls == ["wire", "run"]
