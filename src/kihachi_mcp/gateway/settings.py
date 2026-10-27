"""Process environment for the MCP gateway.

Secrets stay in the environment. This module never writes them and never
includes them in error text.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from ipaddress import ip_address

LOOPBACK_NAMES = frozenset({"localhost"})
MIN_TOKEN_LENGTH = 16
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8787
DEFAULT_PATH = "/mcp"
DEFAULT_ABLETON_PORT = 9877
DEFAULT_MAX_BODY_BYTES = 1_048_576
DEFAULT_ABLETON_TIMEOUT_SECONDS = 5.0


class GatewayConfigError(ValueError):
    """The gateway refused to start. The message is safe to print."""


@dataclass(frozen=True)
class GatewaySettings:
    """Listener and Ableton-bridge settings for one process."""

    transport: str
    host: str
    port: int
    path: str
    auth_enabled: bool
    api_token: str
    ableton_host: str
    ableton_port: int
    ableton_timeout_seconds: float
    max_body_bytes: int
    json_response: bool
    stateless_http: bool
    allowed_hosts: tuple[str, ...]
    allowed_origins: tuple[str, ...]

    def public_url(self) -> str:
        """Loopback URL of the MCP endpoint. No credentials."""
        return f"http://{self.host}:{self.port}{self.path}"


def load_settings(
    *,
    transport: str | None = None,
    host: str | None = None,
    port: int | None = None,
    path: str | None = None,
    environ: dict[str, str] | None = None,
) -> GatewaySettings:
    """Build settings from the process environment, then explicit overrides."""
    env = os.environ if environ is None else environ
    chosen_transport = transport or env.get("KIHACHI_MCP_TRANSPORT") or "stdio"
    if chosen_transport not in {"stdio", "streamable-http"}:
        raise GatewayConfigError(
            "KIHACHI_MCP_TRANSPORT は stdio か streamable-http です"
        )
    chosen_host = _require_loopback(
        host or env.get("KIHACHI_MCP_HOST") or DEFAULT_HOST,
        "MCP の待受",
    )
    chosen_port = _port(
        port if port is not None else env.get("KIHACHI_MCP_PORT"),
        DEFAULT_PORT,
        "MCP のポート",
    )
    chosen_path = path or env.get("KIHACHI_MCP_PATH") or DEFAULT_PATH
    if not chosen_path.startswith("/") or chosen_path == "/health":
        raise GatewayConfigError("MCP のパスは /health 以外の絶対パスにしてください")
    auth_enabled = _bool_env(env, "KIHACHI_MCP_AUTH_ENABLED", default=True)
    api_token = env.get("KIHACHI_MCP_API_TOKEN", "")
    if chosen_transport == "streamable-http" and auth_enabled and len(api_token) < MIN_TOKEN_LENGTH:
        raise GatewayConfigError(
            "HTTP では KIHACHI_MCP_API_TOKEN が必要です（16文字以上）。"
            "値は環境変数に置き、Git やログに書かないでください"
        )
    ableton_host = _require_loopback(
        env.get("ABLETONGPT_HOST") or DEFAULT_HOST,
        "AbletonGPT",
    )
    ableton_port = _port(
        env.get("ABLETONGPT_PORT") or env.get("KIHACHI_ABLETONGPT_PORT"),
        DEFAULT_ABLETON_PORT,
        "AbletonGPT のポート",
    )
    timeout = _positive_float(
        env.get("KIHACHI_MCP_ABLETON_TIMEOUT_SECONDS"),
        DEFAULT_ABLETON_TIMEOUT_SECONDS,
        "Ableton のタイムアウト",
    )
    max_body = _positive_int(
        env.get("KIHACHI_MCP_MAX_BODY_BYTES"),
        DEFAULT_MAX_BODY_BYTES,
        "リクエストサイズ上限",
    )
    return GatewaySettings(
        transport=chosen_transport,
        host=chosen_host,
        port=chosen_port,
        path=chosen_path,
        auth_enabled=auth_enabled if chosen_transport == "streamable-http" else False,
        api_token=api_token,
        ableton_host=ableton_host,
        ableton_port=ableton_port,
        ableton_timeout_seconds=timeout,
        max_body_bytes=max_body,
        json_response=_bool_env(env, "KIHACHI_MCP_JSON_RESPONSE", default=True),
        stateless_http=_bool_env(env, "KIHACHI_MCP_STATELESS", default=True),
        allowed_hosts=_csv(env.get("KIHACHI_MCP_ALLOWED_HOSTS", "")),
        allowed_origins=_csv(env.get("KIHACHI_MCP_ALLOWED_ORIGINS", "")),
    )


def _require_loopback(host: str, label: str) -> str:
    normalized = host.strip().lower()
    if normalized in LOOPBACK_NAMES:
        return normalized
    try:
        address = ip_address(normalized)
    except ValueError as exc:
        raise GatewayConfigError(f"{label} は 127.0.0.1 だけにできます") from exc
    if not address.is_loopback:
        raise GatewayConfigError(f"{label} は 127.0.0.1 だけにできます")
    return normalized


def _port(raw: object, default: int, label: str) -> int:
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise GatewayConfigError(f"{label} が数値ではありません") from exc
    if value < 1 or value > 65535:
        raise GatewayConfigError(f"{label} が範囲外です")
    return value


def _positive_float(raw: str | None, default: float, label: str) -> float:
    if raw is None or raw == "":
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise GatewayConfigError(f"{label} が数値ではありません") from exc
    if value <= 0:
        raise GatewayConfigError(f"{label} は正の数にしてください")
    return value


def _positive_int(raw: str | None, default: int, label: str) -> int:
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise GatewayConfigError(f"{label} が数値ではありません") from exc
    if value <= 0:
        raise GatewayConfigError(f"{label} は正の数にしてください")
    return value


def _bool_env(env: dict[str, str], name: str, *, default: bool) -> bool:
    raw = env.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _csv(raw: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in raw.split(",") if part.strip())
