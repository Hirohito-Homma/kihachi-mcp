"""Inspect the installed local Ollama without downloading anything."""

import json
from http.client import HTTPConnection
from typing import Any

from kihachi_mcp.services.studio_interpreter import (
    DEFAULT_HOST,
    DEFAULT_MODEL,
    DEFAULT_PORT,
)


def probe_ollama(
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    model: str = DEFAULT_MODEL,
    timeout: float = 3.0,
) -> dict[str, Any]:
    """Distinguish unreachable Ollama from a missing installed model."""
    try:
        connection = HTTPConnection(host, port, timeout=timeout)
        connection.request("GET", "/api/tags")
        response = connection.getresponse()
        raw = response.read(262145)
        connection.close()
    except OSError as exc:
        return {
            "ok": False,
            "state": "not_running",
            "host": host,
            "port": port,
            "model": model,
            "installed_models": [],
            "message": (
                f"Ollamaが {host}:{port} で起動していません。"
                "ターミナルで `ollama serve` を実行してください。"
                f" ({exc})"
            ),
        }
    if response.status != 200 or len(raw) > 262144:
        return {
            "ok": False,
            "state": "error",
            "host": host,
            "port": port,
            "model": model,
            "installed_models": [],
            "message": f"Ollamaの応答に失敗しました (HTTP {response.status})",
        }
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {
            "ok": False,
            "state": "error",
            "host": host,
            "port": port,
            "model": model,
            "installed_models": [],
            "message": "Ollamaのモデル一覧を読めませんでした",
        }
    names = [
        str(item.get("name") or "")
        for item in payload.get("models") or []
        if isinstance(item, dict)
    ]
    if model not in names and f"{model}:latest" not in names:
        return {
            "ok": False,
            "state": "model_missing",
            "host": host,
            "port": port,
            "model": model,
            "installed_models": names,
            "message": (
                f"保存済みモデル '{model}' が見つかりません。"
                "KIHACHIはモデルを自動ダウンロードしません。"
                f"導入済み: {', '.join(names) or 'なし'}"
            ),
        }
    return {
        "ok": True,
        "state": "ready",
        "host": host,
        "port": port,
        "model": model,
        "installed_models": names,
        "message": f"{model} が利用できます",
    }
