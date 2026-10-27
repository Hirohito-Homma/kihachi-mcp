"""One Remote Script call. No retry, and the log line has no payload."""

from __future__ import annotations

import logging
from typing import Any

from kihachi_mcp.gateway.client import (
    AbletonCommandError,
    AbletonTimeout,
    AbletonUnavailable,
    get_ableton_client,
)
from kihachi_mcp.gateway.settings import GatewayConfigError

logger = logging.getLogger("kihachi.mcp.gateway")


def invoke(command: str, *, timeout: float, **params: Any) -> tuple[dict[str, Any] | None, str | None]:
    """Return ``(result, None)`` or ``(None, public error)``."""
    try:
        result = get_ableton_client().call(command, timeout=timeout, **params)
    except AbletonTimeout:
        outcome = "timeout"
        result = None
    except AbletonUnavailable:
        outcome = "unavailable"
        result = None
    except GatewayConfigError:
        outcome = "rejected"
        result = None
    except AbletonCommandError as exc:
        outcome = str(exc).strip() or "ableton_error"
        result = None
    else:
        if not isinstance(result, dict):
            outcome = "ableton_error"
            result = None
        else:
            outcome = "ok"
    logger.info(
        "ableton_call",
        extra={"event": "ableton_call", "command": command, "outcome": outcome},
    )
    if outcome == "ok":
        return result, None
    return None, outcome
