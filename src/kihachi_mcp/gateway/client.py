"""Localhost client for the AbletonGPT Remote Script.

The production client speaks the existing newline-JSON socket. Tests inject
another object with the same ``call`` method; this module does not import them.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Protocol

from kihachi_mcp.gateway.settings import (
    GatewayConfigError,
    GatewaySettings,
    load_settings,
)
from kihachi_mcp.services.abletongpt_kits import KitLoadError, RemoteScriptSocket


class AbletonTimeout(Exception):
    """The Remote Script accepted the TCP connection but did not answer in time."""


class AbletonUnavailable(Exception):
    """Nothing on the loopback port answered."""


class AbletonCommandError(Exception):
    """The Remote Script answered, and the text is already safe to return."""


class AbletonBridge(Protocol):
    """One Remote Script command. Implementations must not retry."""

    def call(self, command: str, timeout: float | None = None, **params: Any) -> Any:
        """Send one command and return the JSON result."""
        ...


@dataclass
class RealAbletonClient:
    """RemoteScriptSocket restricted to a loopback address."""

    socket: RemoteScriptSocket

    def __repr__(self) -> str:
        return f"RealAbletonClient(host={self.socket.host!r}, port={self.socket.port})"

    @property
    def host(self) -> str:
        return self.socket.host

    @property
    def port(self) -> int:
        return self.socket.port

    @classmethod
    def from_environment(cls, settings: GatewaySettings | None = None) -> RealAbletonClient:
        """Use the AbletonGPT config for the session token, never the MCP token."""
        chosen = settings or load_settings()
        try:
            configured = RemoteScriptSocket.from_config()
        except KitLoadError as exc:
            raise GatewayConfigError("AbletonGPT の接続先がローカルではないため使いません") from exc
        host = os.environ.get("ABLETONGPT_HOST") or configured.host
        if host not in {"127.0.0.1", "localhost", "::1"}:
            raise GatewayConfigError("AbletonGPT の接続先がローカルではないため使いません")
        port_raw = os.environ.get("ABLETONGPT_PORT") or os.environ.get("KIHACHI_ABLETONGPT_PORT")
        port = int(port_raw) if port_raw else configured.port
        return cls(
            RemoteScriptSocket(
                host=host,
                port=port,
                token=configured.token,
                timeout=chosen.ableton_timeout_seconds,
            )
        )

    def call(self, command: str, timeout: float | None = None, **params: Any) -> Any:
        """One command. Timeout and disconnect stay distinct and carry no secret."""
        try:
            return self.socket.call(command, timeout=timeout, **params)
        except KitLoadError as exc:
            cause = exc.__cause__
            if isinstance(cause, TimeoutError):
                raise AbletonTimeout from None
            if isinstance(cause, OSError):
                raise AbletonUnavailable from None
            message = _public_text(str(exc))
            raise AbletonCommandError(message) from None


def _public_text(text: str) -> str:
    lowered = text.lower()
    if (
        not text
        or len(text) > 200
        or "/" in text
        or "\\" in text
        or "token" in lowered
        or "bearer" in lowered
        or "authorization" in lowered
    ):
        return "ableton_error"
    return text


_override: AbletonBridge | None = None


def configure_ableton_client(client: AbletonBridge | None) -> None:
    """Install a test double, or ``None`` to use the real loopback client."""
    global _override
    _override = client


def get_ableton_client() -> AbletonBridge:
    """Return the injected client, otherwise a real loopback client."""
    if _override is not None:
        return _override
    return RealAbletonClient.from_environment()
