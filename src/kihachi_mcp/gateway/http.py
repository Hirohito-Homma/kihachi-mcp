"""Streamable HTTP front for the existing FastMCP server.

The Ableton socket is not mounted here. ``/health`` is plain HTTP and is not
an MCP method.
"""

from __future__ import annotations

import hashlib
import logging
import secrets

from starlette.middleware import Middleware
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from kihachi_mcp.gateway.settings import GatewaySettings

logger = logging.getLogger("kihachi.mcp.gateway")


def tokens_match(presented: str, expected: str) -> bool:
    """Compare bearer tokens without branching on their length."""
    if not presented or not expected:
        return False
    return secrets.compare_digest(
        hashlib.sha256(presented.encode()).digest(),
        hashlib.sha256(expected.encode()).digest(),
    )


class GatewayGuard:
    """Bearer auth and a body-size cap in front of the MCP app.

    ``/health`` stays unauthenticated and returns no Ableton detail.
    """

    def __init__(self, app: ASGIApp, settings: GatewaySettings) -> None:
        self.app = app
        self.settings = settings

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path") or ""
        if path == "/health":
            await self.app(scope, receive, send)
            return
        headers = _headers(scope)
        length = headers.get("content-length")
        if length is not None:
            try:
                size = int(length)
            except ValueError:
                await _json(send, 400, {"error": "bad_request"})
                return
            if size > self.settings.max_body_bytes:
                logger.info(
                    "http_rejected",
                    extra={"event": "http_rejected", "reason": "request_too_large"},
                )
                await _json(send, 413, {"error": "request_too_large"})
                return
        if self.settings.auth_enabled and not _authorized(headers, self.settings.api_token):
            logger.info(
                "http_rejected",
                extra={"event": "http_rejected", "reason": "unauthorized"},
            )
            await _json(
                send,
                401,
                {"error": "unauthorized"},
                extra=[(b"www-authenticate", b'Bearer realm="kihachi-mcp"')],
            )
            return
        await self.app(scope, receive, send)


def http_middleware(settings: GatewaySettings) -> list[Middleware]:
    """Middleware list passed to FastMCP's HTTP app."""
    return [Middleware(GatewayGuard, settings=settings)]


def register_health_route(mcp: object) -> None:
    """Mount ``GET /health`` beside the MCP path."""
    if getattr(mcp, "_kihachi_health_route", False):
        return

    @mcp.custom_route("/health", methods=["GET"], include_in_schema=False)  # type: ignore[attr-defined]
    async def health(_request: object) -> JSONResponse:
        return JSONResponse({"status": "ok", "service": "kihachi-mcp"})

    mcp._kihachi_health_route = True  # type: ignore[attr-defined]


def _authorized(headers: dict[str, str], expected: str) -> bool:
    header = headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer":
        return False
    return tokens_match(token.strip(), expected)


def _headers(scope: Scope) -> dict[str, str]:
    found: dict[str, str] = {}
    for key, value in scope.get("headers") or ():
        found[key.decode("latin1").lower()] = value.decode("latin1")
    return found


async def _json(
    send: Send,
    status: int,
    payload: dict[str, str],
    extra: list[tuple[bytes, bytes]] | None = None,
) -> None:
    import json

    body = json.dumps(payload).encode("utf-8")
    headers: list[tuple[bytes, bytes]] = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body)).encode("ascii")),
        (b"cache-control", b"no-store"),
    ]
    if extra:
        headers.extend(extra)
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})


def install_gateway_logging() -> None:
    """One JSON handler on the gateway logger. It does not print credentials."""
    if any(getattr(handler, "_kihachi_gateway", False) for handler in logger.handlers):
        return
    handler = logging.StreamHandler()
    handler.setFormatter(_JsonFormatter())
    handler._kihachi_gateway = True  # type: ignore[attr-defined]
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    logger.propagate = False


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        import json

        payload: dict[str, object] = {
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key in ("event", "reason", "command", "outcome"):
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        rendered = json.dumps(payload, ensure_ascii=False)
        lowered = rendered.lower()
        if "bearer " in lowered or "api_token" in lowered:
            return json.dumps({"level": "ERROR", "message": "redacted"})
        return rendered
