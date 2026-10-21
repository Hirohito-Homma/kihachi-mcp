"""Jarvis reads through existing gateway tools. Writes are not called."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

SAFE_GATEWAY_READS = ("ableton_ping", "ableton_status", "ableton_list_tracks")

Tool = Callable[[], dict[str, Any]]


def read_gateway(client: Mapping[str, Tool]) -> dict[str, Any]:
    """Call the safe read tools present on ``client``. ``confirm`` is never passed."""
    results: dict[str, dict[str, Any]] = {}
    for name in SAFE_GATEWAY_READS:
        tool = client.get(name)
        if tool is None:
            continue
        try:
            payload = tool()
        except Exception:  # noqa: BLE001 - a failed read is not success
            return _failed(f"{name} を読めませんでした。成功にはしていません。")
        if not isinstance(payload, dict) or payload.get("ok") is False or payload.get("error"):
            return _failed(f"{name} の読み取りに失敗しました。成功にはしていません。")
        results[name] = payload
    if not results:
        return _failed("読み取り関数がありません。成功にはしていません。")
    return {
        "ok": True,
        "results": results,
        "executed": False,
        "live_changed": False,
        "paid_api": False,
    }


def _failed(message: str) -> dict[str, Any]:
    return {
        "ok": False,
        "results": {},
        "error": message,
        "executed": False,
        "live_changed": False,
        "paid_api": False,
    }
