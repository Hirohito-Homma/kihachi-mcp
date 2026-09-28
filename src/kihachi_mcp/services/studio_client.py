"""Talk to the running Studio, which owns the Live bridge for this Mac."""

from __future__ import annotations

import json
from http.client import HTTPConnection
from typing import Any

from kihachi_mcp.services.diagnostics import STUDIO_HOST, STUDIO_PORT, port_open

NOT_RUNNING = "Liveとの接続は制作画面が持ちます。先に kihachi start で制作画面を起動してください。"


def studio_running() -> bool:
    return port_open(STUDIO_HOST, STUDIO_PORT)


def studio_post(path: str, body: dict[str, Any], timeout: float = 180.0) -> dict[str, Any]:
    """POST JSON to the local Studio. Errors come back as ok=False, never raised."""
    if not studio_running():
        return {"ok": False, "error": NOT_RUNNING, "studio_running": False}
    connection = HTTPConnection(STUDIO_HOST, STUDIO_PORT, timeout=timeout)
    try:
        connection.request("POST", path, json.dumps(body), {"Content-Type": "application/json"})
        data = json.loads(connection.getresponse().read())
    except (OSError, json.JSONDecodeError) as exc:
        return {"ok": False, "error": f"制作画面に接続できません ({exc.__class__.__name__})"}
    finally:
        connection.close()
    return data if isinstance(data, dict) else {"ok": False, "error": "応答を読めませんでした"}
