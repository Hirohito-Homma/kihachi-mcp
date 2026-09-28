"""Transport boundary between KIHACHI and the Max for Live device.

Nothing above this module knows how bytes reach Ableton Live. The executor
depends on the ``LiveTransport`` protocol only, so the automated test suite can
run the full planning and verification path without Live or Max installed.
"""

from typing import Any, Protocol, runtime_checkable

PROTOCOL_NAME = "kihachi.live"
PROTOCOL_VERSION = 1

METHOD_PING = "ping"
METHOD_GET_STATE = "get_state"
METHOD_GET_DRUM_RACK_SUMMARY = "get_drum_rack_summary"
METHOD_GET_DEVICE_PARAMETERS = "get_device_parameters"
METHOD_APPLY_OPERATION = "apply_operation"

SUPPORTED_METHODS = frozenset(
    {
        METHOD_PING,
        METHOD_GET_STATE,
        METHOD_GET_DRUM_RACK_SUMMARY,
        METHOD_GET_DEVICE_PARAMETERS,
        METHOD_APPLY_OPERATION,
    }
)

ERROR_UNAVAILABLE = "unavailable"
ERROR_TIMEOUT = "timeout"
ERROR_DISCONNECTED = "disconnected"
ERROR_PROTOCOL = "protocol"
ERROR_UNAUTHORIZED = "unauthorized"
ERROR_TOO_LARGE = "too_large"
ERROR_DUPLICATE = "duplicate_request"
ERROR_OPERATION_FAILED = "operation_failed"


class LiveTransportError(Exception):
    """A transport-level failure that must never be retried automatically."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@runtime_checkable
class LiveTransport(Protocol):
    """One request/response channel to a Max for Live device."""

    def request(self, message: dict[str, Any]) -> dict[str, Any]:
        """Send one protocol message and return the decoded response.

        Implementations raise ``LiveTransportError`` instead of returning a
        partial result, and must not retry on their own.
        """
        ...


def build_message(
    method: str, request_id: str, payload: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Build one outbound protocol message without any credential material."""
    if method not in SUPPORTED_METHODS:
        raise LiveTransportError(ERROR_PROTOCOL, f"unsupported method '{method}'")
    return {
        "protocol": PROTOCOL_NAME,
        "version": PROTOCOL_VERSION,
        "request_id": request_id,
        "method": method,
        "payload": payload or {},
    }


def decode_response(message: dict[str, Any], request_id: str) -> dict[str, Any]:
    """Validate one inbound protocol message and return its result payload."""
    if message.get("protocol") != PROTOCOL_NAME:
        raise LiveTransportError(ERROR_PROTOCOL, "response is not a KIHACHI message")
    if int(message.get("version") or 0) != PROTOCOL_VERSION:
        raise LiveTransportError(
            ERROR_PROTOCOL,
            f"response protocol version must be {PROTOCOL_VERSION}",
        )
    if str(message.get("request_id") or "") != request_id:
        raise LiveTransportError(ERROR_PROTOCOL, "response request_id does not match")
    if not message.get("ok"):
        error = message.get("error")
        error = error if isinstance(error, dict) else {}
        raise LiveTransportError(
            str(error.get("code") or ERROR_PROTOCOL),
            str(error.get("message") or "Live reported an unspecified failure"),
        )
    result = message.get("result")
    return dict(result) if isinstance(result, dict) else {}


class NullLiveTransport:
    """A transport that reports Live as unreachable.

    This is the default, so an unconfigured server can never mutate a Set.
    """

    def request(self, message: dict[str, Any]) -> dict[str, Any]:
        """Always refuse, naming the missing configuration."""
        raise LiveTransportError(
            ERROR_UNAVAILABLE,
            "no Ableton Live transport is configured; "
            "start the KIHACHI Max for Live device and set KIHACHI_LIVE_BRIDGE_PORT",
        )
