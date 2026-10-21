"""Start the existing FastMCP server on stdio or Streamable HTTP."""

from __future__ import annotations

import argparse
import logging
import sys

from kihachi_mcp.gateway.http import http_middleware, install_gateway_logging
from kihachi_mcp.gateway.runtime import set_transport
from kihachi_mcp.gateway.settings import (
    GatewayConfigError,
    GatewaySettings,
    load_settings,
)

logger = logging.getLogger("kihachi.mcp.gateway")


def serve_stdio() -> None:
    """The historical entrypoint. Cursor and other local clients keep using this."""
    from kihachi_mcp.server import mcp

    set_transport("stdio")
    mcp.run(transport="stdio", show_banner=False)


def serve_http(settings: GatewaySettings) -> None:
    """Bind the MCP endpoint on loopback. The Ableton port is not opened here."""
    from kihachi_mcp.server import mcp

    if settings.transport != "streamable-http":
        raise GatewayConfigError("HTTP 起動には streamable-http を指定してください")
    set_transport("streamable-http")
    install_gateway_logging()
    logger.info(
        "gateway_start host=%s port=%s path=%s auth=%s ableton_port=%s",
        settings.host,
        settings.port,
        settings.path,
        "on" if settings.auth_enabled else "off",
        settings.ableton_port,
        extra={"event": "gateway_start"},
    )
    mcp.run(
        transport="streamable-http",
        host=settings.host,
        port=settings.port,
        path=settings.path,
        show_banner=False,
        log_level="warning",
        middleware=http_middleware(settings),
        json_response=settings.json_response,
        stateless_http=settings.stateless_http,
        host_origin_protection="auto",
        allowed_hosts=list(settings.allowed_hosts) or None,
        allowed_origins=list(settings.allowed_origins) or None,
    )


def build_http_app(settings: GatewaySettings):
    """ASGI app for tests. Same transport and guard as ``serve_http``."""
    from kihachi_mcp.server import mcp

    set_transport("streamable-http")
    return mcp.http_app(
        path=settings.path,
        transport="streamable-http",
        middleware=http_middleware(settings),
        json_response=settings.json_response,
        stateless_http=settings.stateless_http,
        host_origin_protection="auto",
        allowed_hosts=list(settings.allowed_hosts) or None,
        allowed_origins=list(settings.allowed_origins) or None,
    )


def serve_from_args(args: argparse.Namespace) -> int:
    """CLI entry. Configuration errors are printed without secrets."""
    try:
        settings = load_settings(
            transport=args.transport,
            host=args.host,
            port=args.port,
            path=args.path,
        )
    except GatewayConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if settings.transport == "stdio":
        serve_stdio()
        return 0
    try:
        serve_http(settings)
    except GatewayConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0
