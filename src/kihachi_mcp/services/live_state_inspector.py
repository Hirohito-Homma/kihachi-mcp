"""Read-only inspection of the running Ableton Live Set.

This is the only place that turns transport bytes into a
:class:`~kihachi_mcp.models.live_state.LiveStateSnapshot`. It performs no
mutation and no retry: a transport failure is reported as-is so the caller can
decide, rather than silently re-asking Live.
"""

from collections.abc import Callable
from typing import Any

from kihachi_mcp.models.live_contract import SCHEMA_VERSION
from kihachi_mcp.models.live_state import LiveStateSnapshot
from kihachi_mcp.services.live_transport import (
    METHOD_GET_STATE,
    METHOD_PING,
    PROTOCOL_VERSION,
    LiveTransport,
    LiveTransportError,
    NullLiveTransport,
    build_message,
    decode_response,
)

MIN_SUPPORTED_LIVE_MAJOR = 11


class LiveVersionUnsupportedError(Exception):
    """Raised when the connected Live version is older than the tested range."""


class LiveStateInspector:
    """Fetch health and state from the Max for Live device."""

    def __init__(
        self,
        transport: LiveTransport | None = None,
        request_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._transport: LiveTransport = transport or NullLiveTransport()
        self._next_request_id = request_id_factory or _incrementing_request_ids()

    def health(self) -> dict[str, Any]:
        """Return a connection health report without touching the Set.

        This is the first thing to run after installing the device. It proves
        the bridge works and performs no production operation whatsoever.
        """
        request_id = self._next_request_id()
        try:
            result = self._exchange(METHOD_PING, request_id)
        except LiveTransportError as exc:
            return {
                "schema_version": SCHEMA_VERSION,
                "connected": False,
                "error_code": exc.code,
                "error": exc.message,
            }
        live_version = str(result.get("live_version") or "")
        device_protocol = int(result.get("protocol_version") or 0)
        supported = device_protocol == PROTOCOL_VERSION
        report = {
            "schema_version": SCHEMA_VERSION,
            "connected": True,
            "live_version": live_version,
            "device_version": str(result.get("device_version") or ""),
            "protocol_version": device_protocol,
            "protocol_supported": supported,
            "error_code": "",
            "error": "",
        }
        if not supported:
            report["error_code"] = "protocol"
            report["error"] = (
                f"Max device speaks protocol {device_protocol}, "
                f"this server speaks {PROTOCOL_VERSION}"
            )
        return report

    def snapshot(self) -> LiveStateSnapshot:
        """Return one observation of the current Live Set.

        Raises ``LiveTransportError`` if Live is unreachable and
        ``LiveVersionUnsupportedError`` if the Live major version is outside the
        range this adapter has been written against.
        """
        request_id = self._next_request_id()
        payload = self._exchange(METHOD_GET_STATE, request_id)
        snapshot = LiveStateSnapshot.from_dict(payload)
        major = _major_version(snapshot.live_version)
        if major is not None and major < MIN_SUPPORTED_LIVE_MAJOR:
            raise LiveVersionUnsupportedError(
                f"Ableton Live {snapshot.live_version} is older than the tested "
                f"minimum {MIN_SUPPORTED_LIVE_MAJOR}; KIHACHI will not mutate it"
            )
        return snapshot

    def _exchange(self, method: str, request_id: str) -> dict[str, Any]:
        message = build_message(method, request_id)
        response = self._transport.request(message)
        return decode_response(response, request_id)


def _major_version(live_version: str) -> int | None:
    """Return the major version number of a Live version string, if parsable."""
    head = live_version.split(".", 1)[0].strip()
    try:
        return int(head)
    except ValueError:
        return None


def _incrementing_request_ids() -> Callable[[], str]:
    """Return a deterministic request id factory for inspection calls."""
    counter = {"value": 0}

    def next_id() -> str:
        counter["value"] += 1
        return f"inspect-{counter['value']:06d}"

    return next_id
