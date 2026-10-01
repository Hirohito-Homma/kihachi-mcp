"""Process-local transport name reported by server_status."""

from __future__ import annotations

_transport = "stdio"


def set_transport(transport: str) -> None:
    """Record how this process was started."""
    global _transport
    _transport = transport


def current_transport() -> str:
    """``stdio`` until ``kihachi serve --transport streamable-http``."""
    return _transport
