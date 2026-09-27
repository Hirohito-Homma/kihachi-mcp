"""Loopback-only bridge between KIHACHI and the Max for Live device.

Security posture:

* Sockets bind and send to ``127.0.0.1`` only. There is no configuration that
  exposes the bridge to another host.
* The session token is generated fresh on every start, lives only in memory and
  in a ``0600`` handshake file under a per-user directory, and is never logged,
  never returned from an MCP tool, and never committed.
* Requests are size-capped and time-capped, duplicate ``request_id`` values are
  refused, and nothing is ever retried automatically.

The handshake file is how the Max device learns the port and token. Python owns
the token lifecycle so the secret never has to be typed into Max.
"""

import json
import logging
import os
import secrets
import socket
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol

from kihachi_mcp.services.live_paths import bridge_handshake_path
from kihachi_mcp.services.live_transport import (
    ERROR_DISCONNECTED,
    ERROR_DUPLICATE,
    ERROR_PROTOCOL,
    ERROR_TIMEOUT,
    ERROR_TOO_LARGE,
    ERROR_UNAVAILABLE,
    PROTOCOL_NAME,
    PROTOCOL_VERSION,
    LiveTransportError,
)

LOOPBACK_HOST = "127.0.0.1"
DEFAULT_DEVICE_PORT = 17771
DEFAULT_REPLY_PORT = 17772
MAX_REQUEST_BYTES = 60_000
MAX_RESPONSE_BYTES = 262_144
DEFAULT_TIMEOUT_SECONDS = 5.0
MAX_PLAN_OPERATIONS = 512

_logger = logging.getLogger("kihachi.live.bridge")


class DatagramChannel(Protocol):
    """A loopback datagram channel, injected so the bridge is testable."""

    def send(self, payload: bytes) -> None:
        """Send one datagram to the Max device."""
        ...

    def receive(self, timeout: float) -> bytes:
        """Block for one reply datagram or raise ``LiveTransportError``."""
        ...

    def close(self) -> None:
        """Release the channel."""
        ...


class LoopbackUdpChannel:
    """A UDP channel pinned to the loopback interface."""

    def __init__(
        self,
        device_port: int = DEFAULT_DEVICE_PORT,
        reply_port: int = DEFAULT_REPLY_PORT,
    ) -> None:
        self._device_address = (LOOPBACK_HOST, device_port)
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self._socket.bind((LOOPBACK_HOST, reply_port))
        except OSError as exc:
            self._socket.close()
            raise LiveTransportError(
                ERROR_UNAVAILABLE,
                f"cannot bind the KIHACHI reply port {reply_port} on "
                f"{LOOPBACK_HOST}: {exc.strerror or exc}",
            ) from exc

    @property
    def reply_port(self) -> int:
        """Return the loopback port this channel listens on."""
        return int(self._socket.getsockname()[1])

    def send(self, payload: bytes) -> None:
        """Send one datagram to the Max device on loopback."""
        try:
            self._socket.sendto(payload, self._device_address)
        except OSError as exc:
            raise LiveTransportError(
                ERROR_DISCONNECTED, f"loopback send failed: {exc.strerror or exc}"
            ) from exc

    def receive(self, timeout: float) -> bytes:
        """Wait for one reply datagram from the Max device."""
        self._socket.settimeout(timeout)
        try:
            data, address = self._socket.recvfrom(MAX_RESPONSE_BYTES)
        except TimeoutError as exc:
            raise LiveTransportError(
                ERROR_TIMEOUT,
                f"the Max for Live device did not reply within {timeout:g}s",
            ) from exc
        except OSError as exc:
            raise LiveTransportError(
                ERROR_DISCONNECTED, f"loopback receive failed: {exc.strerror or exc}"
            ) from exc
        if address[0] != LOOPBACK_HOST:
            raise LiveTransportError(
                ERROR_PROTOCOL, "discarded a reply that did not come from loopback"
            )
        return data

    def close(self) -> None:
        """Close the loopback socket."""
        self._socket.close()


class LiveBridgeSession:
    """Own the short-lived session token and the handshake file."""

    def __init__(
        self,
        device_port: int = DEFAULT_DEVICE_PORT,
        reply_port: int = DEFAULT_REPLY_PORT,
        handshake_path: str | Path | None = None,
        environ: Mapping[str, str] | None = None,
        system: str | None = None,
    ) -> None:
        env = environ if environ is not None else os.environ
        self.device_port = int(env.get("KIHACHI_LIVE_BRIDGE_PORT") or device_port)
        self.reply_port = int(env.get("KIHACHI_LIVE_REPLY_PORT") or reply_port)
        self.token = secrets.token_urlsafe(32)
        self._handshake_path = Path(
            handshake_path
            if handshake_path is not None
            else bridge_handshake_path(system, env)
        ).expanduser()

    @property
    def handshake_path(self) -> Path:
        """Return where the Max device reads the port and token."""
        return self._handshake_path

    def write_handshake(self) -> Path:
        """Write the handshake file with owner-only permissions.

        The token is the only secret in the system and it never leaves this
        machine: the file sits in a per-user directory and is replaced on every
        start.
        """
        self._handshake_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._handshake_path.with_suffix(".tmp")
        payload = {
            "protocol": PROTOCOL_NAME,
            "protocol_version": PROTOCOL_VERSION,
            "host": LOOPBACK_HOST,
            "device_port": self.device_port,
            "reply_port": self.reply_port,
            "token": self.token,
        }
        temporary.write_text(
            json.dumps(payload, indent=2) + "\n", encoding="utf-8"
        )
        os.chmod(temporary, 0o600)
        temporary.replace(self._handshake_path)
        _logger.info(
            "live.bridge.handshake_written",
            extra={
                "handshake_path": str(self._handshake_path),
                "device_port": self.device_port,
                "reply_port": self.reply_port,
            },
        )
        return self._handshake_path

    def remove_handshake(self) -> None:
        """Delete the handshake file so a stale token cannot be reused."""
        self._handshake_path.unlink(missing_ok=True)


class LocalhostBridgeTransport:
    """A ``LiveTransport`` that speaks to the Max device over loopback UDP."""

    def __init__(
        self,
        session: LiveBridgeSession | None = None,
        channel: DatagramChannel | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_request_bytes: int = MAX_REQUEST_BYTES,
    ) -> None:
        self._session = session or LiveBridgeSession()
        self._channel = channel
        self._timeout = timeout_seconds
        self._max_request_bytes = max_request_bytes
        self._seen_request_ids: set[str] = set()

    def _ensure_channel(self) -> DatagramChannel:
        if self._channel is None:
            self._channel = LoopbackUdpChannel(
                device_port=self._session.device_port,
                reply_port=self._session.reply_port,
            )
        return self._channel

    def request(self, message: dict[str, Any]) -> dict[str, Any]:
        """Send one message to the Max device and return its raw reply.

        The session token is attached here, so no caller above the transport
        ever handles it.
        """
        request_id = str(message.get("request_id") or "")
        if not request_id:
            raise LiveTransportError(ERROR_PROTOCOL, "request_id is required")
        if request_id in self._seen_request_ids:
            raise LiveTransportError(
                ERROR_DUPLICATE,
                f"request_id '{request_id}' was already sent; "
                "KIHACHI will not resend it",
            )
        envelope = dict(message)
        envelope["token"] = self._session.token
        payload = json.dumps(envelope, separators=(",", ":")).encode("utf-8")
        if len(payload) > self._max_request_bytes:
            raise LiveTransportError(
                ERROR_TOO_LARGE,
                f"request is {len(payload)} bytes, over the "
                f"{self._max_request_bytes} byte loopback limit; "
                "split the plan into smaller operations",
            )
        channel = self._ensure_channel()
        self._seen_request_ids.add(request_id)
        _logger.info(
            "live.bridge.request",
            extra={
                "request_id": request_id,
                "method": str(message.get("method") or ""),
                "request_bytes": len(payload),
            },
        )
        channel.send(payload)
        raw = channel.receive(self._timeout)
        try:
            decoded = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise LiveTransportError(
                ERROR_PROTOCOL, "the Max device sent a reply that is not JSON"
            ) from exc
        if not isinstance(decoded, dict):
            raise LiveTransportError(
                ERROR_PROTOCOL, "the Max device sent a reply that is not an object"
            )
        _logger.info(
            "live.bridge.response",
            extra={
                "request_id": request_id,
                "ok": bool(decoded.get("ok")),
            },
        )
        return decoded

    def close(self) -> None:
        """Release the loopback channel and remove the handshake file."""
        if self._channel is not None:
            self._channel.close()
            self._channel = None
        self._session.remove_handshake()
