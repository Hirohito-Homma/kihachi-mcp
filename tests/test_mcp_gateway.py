"""MCP gateway: HTTP startup, auth, and the Ableton client boundary."""

from __future__ import annotations

import asyncio
import socket
import threading
import time
import urllib.error
import urllib.request
from typing import Any

import pytest
import uvicorn

from kihachi_mcp.gateway.catalog import NOT_IMPLEMENTED
from kihachi_mcp.gateway.client import (
    AbletonCommandError,
    AbletonTimeout,
    AbletonUnavailable,
    RealAbletonClient,
    configure_ableton_client,
)
from kihachi_mcp.gateway.serve import build_http_app
from kihachi_mcp.gateway.settings import GatewayConfigError, load_settings
from kihachi_mcp.gateway.tools import (
    ableton_ping,
    ableton_status,
    server_status,
    set_tempo,
)
from kihachi_mcp.services.abletongpt_kits import KitLoadError, RemoteScriptSocket

TOKEN = "kihachi-gateway-test-token"


class FakeAbleton:
    """In-test stand-in. Production code does not import this class."""

    def __init__(self, fail: str | None = None) -> None:
        self.fail = fail
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.tempo = 120.0
        self.host = "127.0.0.1"
        self.port = 9877

    def call(self, command: str, timeout: float | None = None, **params: Any) -> Any:
        self.calls.append((command, params))
        if self.fail == "timeout":
            raise AbletonTimeout
        if self.fail == "unavailable":
            raise AbletonUnavailable
        if command == "ping":
            return {"connected": True, "app": "Ableton Live"}
        if command == "get_state":
            return {
                "tempo": self.tempo,
                "is_playing": False,
                "signature": [4, 4],
                "tracks": [{"index": 0, "name": "Kick"}],
            }
        if command == "set_tempo":
            self.tempo = float(params["bpm"])
            return {"tempo": self.tempo}
        raise AssertionError(command)


@pytest.fixture(autouse=True)
def _isolate_ableton() -> Any:
    configure_ableton_client(FakeAbleton(fail="unavailable"))
    yield
    configure_ableton_client(None)


def _http_settings(**env: str):
    base = {
        "KIHACHI_MCP_TRANSPORT": "streamable-http",
        "KIHACHI_MCP_HOST": "127.0.0.1",
        "KIHACHI_MCP_API_TOKEN": TOKEN,
        "KIHACHI_MCP_AUTH_ENABLED": "true",
        "KIHACHI_MCP_JSON_RESPONSE": "true",
        "KIHACHI_MCP_STATELESS": "true",
    }
    base.update(env)
    return load_settings(transport="streamable-http", environ=base)


@pytest.fixture
def gateway_url():
    settings = _http_settings()
    app = build_http_app(settings)
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 5
    while time.time() < deadline and not server.started:
        time.sleep(0.02)
    if not server.started:
        raise RuntimeError("MCP gateway did not start")
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def _request(url: str, *, token: str | None = None, body: bytes = b"{}", method: str = "POST"):
    headers = {"content-type": "application/json", "accept": "application/json, text/event-stream"}
    if token is not None:
        headers["authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def test_public_bind_is_rejected() -> None:
    with pytest.raises(GatewayConfigError):
        load_settings(host="0.0.0.0", transport="stdio", environ={})
    with pytest.raises(GatewayConfigError):
        load_settings(environ={"ABLETONGPT_HOST": "0.0.0.0"})


def test_http_without_token_is_rejected() -> None:
    with pytest.raises(GatewayConfigError):
        load_settings(
            transport="streamable-http",
            environ={"KIHACHI_MCP_AUTH_ENABLED": "true", "KIHACHI_MCP_API_TOKEN": ""},
        )


def test_stdio_does_not_require_a_token() -> None:
    settings = load_settings(transport="stdio", environ={})
    assert settings.transport == "stdio"
    assert settings.auth_enabled is False
    assert settings.host == "127.0.0.1"


def test_max_body_setting_accepts_documented_byte_limit_and_rejects_invalid_values() -> None:
    settings = load_settings(environ={"KIHACHI_MCP_MAX_BODY_BYTES": "1048576"})
    assert settings.max_body_bytes == 1_048_576
    assert load_settings(environ={}).max_body_bytes == 1_048_576
    for invalid in ("0", "-1", "not-a-number"):
        with pytest.raises(GatewayConfigError):
            load_settings(environ={"KIHACHI_MCP_MAX_BODY_BYTES": invalid})


def test_production_http_start_forwards_allowed_origins(monkeypatch: pytest.MonkeyPatch) -> None:
    from kihachi_mcp.gateway.serve import serve_http
    from kihachi_mcp.server import mcp

    captured: dict[str, Any] = {}

    def run(**kwargs: Any) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(mcp, "run", run)
    settings = _http_settings(
        KIHACHI_MCP_ALLOWED_ORIGINS="https://studio.example,https://admin.example"
    )
    serve_http(settings)
    assert captured["allowed_origins"] == ["https://studio.example", "https://admin.example"]


def test_cli_rejects_a_public_mcp_host(capsys: pytest.CaptureFixture[str]) -> None:
    from kihachi_mcp.cli import main

    code = main(["serve", "--transport", "streamable-http", "--host", "0.0.0.0"])
    captured = capsys.readouterr()
    assert code == 2
    assert "127.0.0.1" in captured.err
    assert TOKEN not in captured.err


def test_health_is_open_and_mcp_requires_the_bearer_token(gateway_url: str) -> None:
    status, body = _request(f"{gateway_url}/health", method="GET", body=None)
    assert status == 200
    assert b"kihachi-mcp" in body
    assert TOKEN.encode() not in body

    status, body = _request(f"{gateway_url}/mcp")
    assert status == 401
    assert b"unauthorized" in body
    assert TOKEN.encode() not in body

    status, body = _request(f"{gateway_url}/mcp", token="wrong-token-value-0123456789")
    assert status == 401
    assert b"wrong-token" not in body


def test_oversized_request_is_rejected() -> None:
    from kihachi_mcp.gateway.http import GatewayGuard

    settings = _http_settings(KIHACHI_MCP_MAX_BODY_BYTES="32")

    async def app(scope: dict[str, Any], receive: Any, send: Any) -> None:
        raise AssertionError(scope["path"])

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/mcp",
        "raw_path": b"/mcp",
        "query_string": b"",
        "headers": [
            (b"content-length", b"999999"),
            (b"authorization", f"Bearer {TOKEN}".encode()),
        ],
        "client": ("127.0.0.1", 1),
        "server": ("127.0.0.1", 8787),
    }

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"{}", "more_body": False}

    messages: list[dict[str, Any]] = []

    async def send(message: dict[str, Any]) -> None:
        messages.append(message)

    asyncio.run(GatewayGuard(app, settings)(scope, receive, send))
    assert messages[0]["status"] == 413


def test_tools_list_and_ping_over_streamable_http(gateway_url: str) -> None:
    fake = FakeAbleton()
    configure_ableton_client(fake)

    async def run() -> None:
        from fastmcp import Client

        async with Client(f"{gateway_url}/mcp", auth=TOKEN) as client:
            names = {tool.name for tool in await client.list_tools()}
            assert {"ableton_ping", "ableton_status", "server_status", "set_tempo", "hello"} <= names
            ping = await client.call_tool("ableton_ping", {})
            status = await client.call_tool("server_status", {})
            blocked = await client.call_tool("set_tempo", {"bpm": 126})
            changed = await client.call_tool("set_tempo", {"bpm": 126, "confirm": True})
        return names, ping, status, blocked, changed

    _names, ping, status, blocked, changed = asyncio.run(run())
    ping_data = _payload(ping)
    assert ping_data["connected"] is True
    assert ping_data["live_version"] is None
    assert ping_data["project"] is None
    assert ping_data["port"] == 9877
    assert _payload(status) == {
        "status": "ok",
        "transport": "streamable-http",
        "ableton_connected": True,
    }
    assert _payload(blocked)["status"] == "confirmation_required"
    assert _payload(changed) == {
        "ok": True,
        "risk": "requires_confirmation",
        "confirmed": True,
        "tempo": 126.0,
    }
    assert ("set_tempo", {"bpm": 126.0}) in fake.calls
    assert [command for command, _params in fake.calls].count("set_tempo") == 1


def test_ableton_down_and_timeout_do_not_crash() -> None:
    configure_ableton_client(FakeAbleton(fail="unavailable"))
    down = ableton_ping()
    assert down["connected"] is False
    assert down["error"] == "unavailable"
    assert down["live_version"] is None
    assert server_status()["status"] == "ok"
    assert server_status()["ableton_connected"] is False

    configure_ableton_client(FakeAbleton(fail="timeout"))
    timed_out = ableton_status()
    assert timed_out["connected"] is False
    assert timed_out["error"] == "timeout"
    assert timed_out["tempo"] is None
    refused = set_tempo(110, confirm=True)
    assert refused["ok"] is False
    assert refused["error"] == "timeout"


def test_set_tempo_without_confirmation_does_not_call_live() -> None:
    fake = FakeAbleton()
    configure_ableton_client(fake)
    result = set_tempo(128)
    assert result["status"] == "confirmation_required"
    assert fake.calls == []
    observed = ableton_status()
    assert observed["tempo"] == 120.0
    assert observed["project"] is None


def test_catalogue_marks_destructive_actions_as_not_implemented() -> None:
    from kihachi_mcp.gateway.tools import ableton_tool_catalogue

    listed = {row["name"]: row["status"] for row in ableton_tool_catalogue()["not_implemented"]}
    for row in NOT_IMPLEMENTED:
        assert listed[row["name"]] == "NOT IMPLEMENTED"
    assert listed["delete_track"] == "NOT IMPLEMENTED"
    assert listed["save_over_existing_project"] == "NOT IMPLEMENTED"


def test_real_client_timeout_and_unavailable() -> None:
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]

    def _hold() -> None:
        connection, _address = listener.accept()
        time.sleep(1)
        connection.close()

    thread = threading.Thread(target=_hold, daemon=True)
    thread.start()
    client = RealAbletonClient(
        RemoteScriptSocket(host="127.0.0.1", port=port, token="socket-secret", timeout=0.2)
    )
    with pytest.raises(AbletonTimeout):
        client.call("ping", timeout=0.2)
    listener.close()

    closed = socket.socket()
    closed.bind(("127.0.0.1", 0))
    closed_port = closed.getsockname()[1]
    closed.close()
    missing = RealAbletonClient(
        RemoteScriptSocket(host="127.0.0.1", port=closed_port, token="socket-secret", timeout=0.3)
    )
    with pytest.raises(AbletonUnavailable):
        missing.call("ping")


def test_command_errors_drop_paths_and_secrets() -> None:
    class _Leaky(RemoteScriptSocket):
        def call(self, command: str, timeout: float | None = None, **params: Any) -> Any:
            raise KitLoadError("failed /Users/hidden token=abc")

    client = RealAbletonClient(_Leaky(host="127.0.0.1", port=9, token="socket-secret"))
    with pytest.raises(AbletonCommandError) as caught:
        client.call("ping")
    assert str(caught.value) == "ableton_error"
    assert "hidden" not in str(caught.value)
    assert "socket-secret" not in repr(client)


def test_existing_stdio_registry_keeps_hello() -> None:
    from kihachi_mcp.server import mcp

    names = {tool.name for tool in asyncio.run(mcp.list_tools())}
    assert "hello" in names
    assert "ableton_ping" in names


def _payload(result: Any) -> dict[str, Any]:
    data = getattr(result, "data", None)
    if isinstance(data, dict):
        return data
    structured = getattr(result, "structured_content", None)
    if isinstance(structured, dict):
        return structured
    raise AssertionError(repr(result))
