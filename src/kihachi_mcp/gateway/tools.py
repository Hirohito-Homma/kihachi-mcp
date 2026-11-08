"""Gateway tools. They call the loopback Ableton client and do not plan music."""

from __future__ import annotations

import logging
from typing import Any

from kihachi_mcp.gateway.catalog import RISK_CONFIRM, RISK_SAFE, catalogue
from kihachi_mcp.gateway.client import (
    AbletonCommandError,
    AbletonTimeout,
    AbletonUnavailable,
    get_ableton_client,
)
from kihachi_mcp.gateway.runtime import current_transport
from kihachi_mcp.gateway.settings import GatewayConfigError, load_settings

logger = logging.getLogger("kihachi.mcp.gateway")

_LIVE_TEMPO_MIN = 20.0
_LIVE_TEMPO_MAX = 999.0


def ableton_ping() -> dict[str, Any]:
    """Ask the local AbletonGPT Remote Script whether Live is answering.

    live_version and project are null: ping does not return them.
    """
    endpoint = _endpoint()
    try:
        result = get_ableton_client().call("ping", timeout=_timeout())
    except (AbletonTimeout, AbletonUnavailable, AbletonCommandError, GatewayConfigError) as exc:
        logger.info(
            "ableton_call",
            extra={"event": "ableton_call", "command": "ping", "outcome": _outcome(exc)},
        )
        return {
            "connected": False,
            "host": endpoint[0],
            "port": endpoint[1],
            "live_version": None,
            "project": None,
            "error": _outcome(exc),
        }
    connected = bool(result.get("connected")) if isinstance(result, dict) else False
    logger.info(
        "ableton_call",
        extra={"event": "ableton_call", "command": "ping", "outcome": "ok" if connected else "unavailable"},
    )
    return {
        "connected": connected,
        "host": endpoint[0],
        "port": endpoint[1],
        "live_version": None,
        "project": None,
        "app": result.get("app") if isinstance(result, dict) else None,
        "error": None if connected else "unavailable",
    }


def ableton_status() -> dict[str, Any]:
    """Read tempo and track count. Does not change the Set.

    live_version and project stay null because get_state does not include them.
    """
    ping = ableton_ping()
    body: dict[str, Any] = {
        "connected": ping["connected"],
        "host": ping["host"],
        "port": ping["port"],
        "live_version": None,
        "project": None,
        "tempo": None,
        "is_playing": None,
        "track_count": None,
        "signature": None,
        "error": ping["error"],
    }
    if not ping["connected"]:
        return body
    try:
        state = get_ableton_client().call("get_state", timeout=_timeout())
    except (AbletonTimeout, AbletonUnavailable, AbletonCommandError, GatewayConfigError) as exc:
        body["error"] = _outcome(exc)
        return body
    if not isinstance(state, dict):
        body["error"] = "ableton_error"
        return body
    tracks = state.get("tracks")
    body["tempo"] = _optional_float(state.get("tempo"))
    playing = state.get("is_playing")
    body["is_playing"] = playing if isinstance(playing, bool) else None
    body["track_count"] = len(tracks) if isinstance(tracks, list) else None
    signature = state.get("signature")
    body["signature"] = signature if isinstance(signature, list) else None
    body["error"] = None
    return body


def server_status() -> dict[str, Any]:
    """Report this MCP process. Ableton being down does not fail the gateway."""
    ping = ableton_ping()
    return {
        "status": "ok",
        "transport": current_transport(),
        "ableton_connected": bool(ping["connected"]),
    }


def set_tempo(bpm: float, confirm: bool = False) -> dict[str, Any]:
    """Change Live's tempo through the local Remote Script.

    Without confirm=true this returns a confirmation request and sends nothing.
    A person has to choose the second call. There is no automatic retry.
    """
    try:
        tempo = float(bpm)
    except (TypeError, ValueError):
        return _rejected("bpm が数値ではありません")
    if tempo < _LIVE_TEMPO_MIN or tempo > _LIVE_TEMPO_MAX:
        return _rejected("bpm は 20 から 999 の範囲です")
    if not confirm:
        logger.info(
            "confirmation_required",
            extra={"event": "confirmation_required", "command": "set_tempo"},
        )
        return {
            "ok": False,
            "status": "confirmation_required",
            "risk": RISK_CONFIRM,
            "command": "set_tempo",
            "bpm": tempo,
            "message": "Live のテンポを変えます。人が確認したあと confirm=true で1回だけ呼んでください",
        }
    try:
        result = get_ableton_client().call("set_tempo", timeout=_timeout(), bpm=tempo)
    except (AbletonTimeout, AbletonUnavailable, AbletonCommandError, GatewayConfigError) as exc:
        outcome = _outcome(exc)
        logger.info(
            "ableton_call",
            extra={"event": "ableton_call", "command": "set_tempo", "outcome": outcome},
        )
        return {"ok": False, "risk": RISK_CONFIRM, "error": outcome, "tempo": None}
    observed = result.get("tempo") if isinstance(result, dict) else None
    if observed is None:
        return {"ok": False, "risk": RISK_CONFIRM, "error": "unconfirmed", "tempo": None}
    logger.info(
        "ableton_call",
        extra={"event": "ableton_call", "command": "set_tempo", "outcome": "ok"},
    )
    return {"ok": True, "risk": RISK_CONFIRM, "confirmed": True, "tempo": float(observed)}


def ableton_tool_catalogue() -> dict[str, Any]:
    """Return the supported, partial, and missing Ableton gateway tools."""
    return catalogue()


def register_gateway_tools(mcp: Any) -> None:
    """Add the gateway tools once. Existing stdio tools stay registered."""
    if getattr(mcp, "_kihachi_gateway_tools", False):
        return
    read_only = {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": True}
    mcp.tool(
        ableton_ping,
        annotations=read_only,
        meta={"kihachiRisk": RISK_SAFE},
    )
    mcp.tool(
        ableton_status,
        annotations=read_only,
        meta={"kihachiRisk": RISK_SAFE},
    )
    mcp.tool(
        server_status,
        annotations={**read_only, "openWorldHint": False},
        meta={"kihachiRisk": RISK_SAFE},
    )
    mcp.tool(
        set_tempo,
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
        meta={"kihachiRisk": RISK_CONFIRM},
    )
    mcp.tool(
        ableton_tool_catalogue,
        annotations={**read_only, "openWorldHint": False},
        meta={"kihachiRisk": RISK_SAFE},
    )
    from kihachi_mcp.gateway.live_tools import register_live_tools

    register_live_tools(mcp)
    mcp._kihachi_gateway_tools = True


def _endpoint() -> tuple[str, int]:
    try:
        client = get_ableton_client()
        host = getattr(client, "host", None)
        port = getattr(client, "port", None)
        if isinstance(host, str) and isinstance(port, int):
            return host, port
    except GatewayConfigError:
        pass
    try:
        settings = load_settings()
    except GatewayConfigError:
        return "127.0.0.1", 9877
    return settings.ableton_host, settings.ableton_port


def _timeout() -> float:
    try:
        return load_settings().ableton_timeout_seconds
    except GatewayConfigError:
        return 5.0


def _outcome(exc: Exception) -> str:
    if isinstance(exc, AbletonTimeout):
        return "timeout"
    if isinstance(exc, AbletonUnavailable):
        return "unavailable"
    if isinstance(exc, GatewayConfigError):
        return "rejected"
    if isinstance(exc, AbletonCommandError):
        return str(exc) or "ableton_error"
    return "ableton_error"


def _optional_float(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _rejected(message: str) -> dict[str, Any]:
    return {"ok": False, "risk": RISK_CONFIRM, "error": "rejected", "message": message, "tempo": None}
