"""Load Core Library drum kits through AbletonGPT's Remote Script.

The Max for Live device cannot reach Live's browser; a Python Remote Script
can. AbletonGPT ships one (selected in Live as the ``AbletonGPT_MCP`` control
surface), so the Studio talks to it directly over its localhost socket instead
of importing the AbletonGPT package: the two repositories stay independent and
nothing here costs a model call.

Only three of its commands are used -- ``browse_presets``, ``load_preset`` and
``get_track_devices`` -- with the safety shape AbletonGPT's own kit loader has:

* a track that already holds an instrument is refused, never replaced;
* the browser is walked breadth first and bounded, stopping at the first choice;
* success is an exact readback of one instrument with the kit's name;
* nothing is retried. A load that times out is judged by that readback alone.

The session token is read from AbletonGPT's config file and never logged.
"""

from __future__ import annotations

import json
import os
import socket
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MAX_BROWSER_CALLS = 64
MAX_BROWSER_DEPTH = 3


class KitLoadError(RuntimeError):
    """A kit could not be loaded; the message is safe to show."""


def config_path() -> Path:
    """Where AbletonGPT keeps host, port and token (its own convention)."""
    override = os.getenv("ABLETONGPT_CONFIG")
    if override:
        return Path(override).expanduser()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "AbletonGPT" / "config.json"
    base = Path(os.getenv("APPDATA", str(Path.home())))
    return base / "AbletonGPT" / "config.json"


@dataclass(frozen=True)
class RemoteScriptSocket:
    """One newline-delimited JSON request per localhost TCP connection."""

    host: str = "127.0.0.1"
    port: int = 9877
    token: str = ""
    timeout: float = 3.0

    @classmethod
    def from_config(cls, path: Path | None = None) -> RemoteScriptSocket:
        source = path or config_path()
        data: dict[str, Any] = {}
        if source.is_file():
            loaded = json.loads(source.read_text(encoding="utf-8"))
            data = loaded if isinstance(loaded, dict) else {}
        host = str(data.get("host", "127.0.0.1"))
        if host not in {"127.0.0.1", "localhost", "::1"}:
            raise KitLoadError("AbletonGPT の接続先がローカルではないため使いません")
        return cls(
            host=host,
            port=int(data.get("port", 9877)),
            token=str(data.get("token", "")),
            timeout=float(data.get("timeout", 3.0)),
        )

    def call(self, command: str, timeout: float | None = None, **params: Any) -> Any:
        payload = json.dumps(
            {"command": command, "params": params, "token": self.token},
            separators=(",", ":"),
        ).encode("utf-8") + b"\n"
        wait = self.timeout if timeout is None else timeout
        try:
            with socket.create_connection((self.host, self.port), wait) as connection:
                connection.settimeout(wait)
                connection.sendall(payload)
                chunks: list[bytes] = []
                while True:
                    chunk = connection.recv(65536)
                    if not chunk:
                        break
                    chunks.append(chunk)
                    if b"\n" in chunk or sum(map(len, chunks)) > 2_000_000:
                        break
        except OSError as exc:
            raise KitLoadError(
                "AbletonGPT に接続できません。Live の Control Surface で AbletonGPT_MCP を選んでください"
            ) from exc
        try:
            decoded = json.loads(b"".join(chunks).split(b"\n", 1)[0].decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise KitLoadError("AbletonGPT から読めない応答が返りました") from exc
        if not isinstance(decoded, dict) or not decoded.get("ok"):
            error = decoded.get("error") if isinstance(decoded, dict) else ""
            raise KitLoadError(f"AbletonGPT: {error or 'コマンドが失敗しました'}")
        return decoded.get("result")


def kit_key(name: str) -> str:
    """Browser names carry the file extension: "909 Core Kit.adg"."""
    return name[:-4] if name.lower().endswith(".adg") else name


class AbletonGPTKitLoader:
    """Resolve and load one kit per empty drum track."""

    def __init__(self, remote: Callable[..., Any] | None = None) -> None:
        self._remote = remote

    def _call(self, command: str, timeout: float | None = None, **params: Any) -> Any:
        if self._remote is not None:
            return self._remote(command, timeout=timeout, **params)
        return RemoteScriptSocket.from_config().call(command, timeout=timeout, **params)

    def available(self) -> bool:
        """Whether the Remote Script answers. Reads nothing from the Set."""
        try:
            self._call("ping", timeout=1.5)
        except (KitLoadError, OSError, ValueError):
            return False
        return True

    def load(self, track_index: int, candidates: Sequence[str]) -> str:
        """Load the first candidate kit found; return its name or raise."""
        if not candidates:
            raise KitLoadError("キットの候補がありません")
        if self._instruments(track_index):
            raise KitLoadError("このトラックには既に楽器があるため、キットは入れません")
        locations = self._locate(candidates[0])
        chosen = next((name for name in candidates if name in locations), None)
        if chosen is None:
            raise KitLoadError(f"Live のブラウザに候補のキットがありません（{'、'.join(candidates)}）")
        path, browser_name = locations[chosen]
        try:
            self._call(
                "load_preset",
                timeout=30.0,
                track_index=track_index,
                category="drums",
                path=list(path),
                name=browser_name,
            )
        except KitLoadError:
            # A rack can finish loading after the socket gives up. Only the
            # readback below decides, and nothing is sent a second time.
            pass
        instruments = self._instruments(track_index)
        if len(instruments) != 1 or not _names(instruments[0]) & {chosen, browser_name}:
            raise KitLoadError(f"{chosen} を読み込んだことを Live から確認できませんでした")
        return chosen

    def _instruments(self, track_index: int) -> list[dict[str, Any]]:
        observed = self._call("get_track_devices", track_index=track_index) or {}
        devices = observed.get("devices", []) if isinstance(observed, dict) else []
        return [
            device
            for device in devices
            if isinstance(device, dict) and int(device.get("type", -1)) == 1
        ]

    def _locate(self, first_choice: str) -> dict[str, tuple[list[str], str]]:
        found: dict[str, tuple[list[str], str]] = {}
        queue: list[list[str]] = [[]]
        calls = 0
        while queue and calls < MAX_BROWSER_CALLS and first_choice not in found:
            path = queue.pop(0)
            calls += 1
            listing = self._call(
                "browse_presets", category="drums", path=list(path), max_items=1000
            )
            items = listing.get("items", []) if isinstance(listing, dict) else []
            for item in items:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name", "")).strip()
                if not name:
                    continue
                if item.get("is_folder"):
                    if len(path) < MAX_BROWSER_DEPTH:
                        queue.append([*path, name])
                    continue
                if item.get("is_loadable"):
                    found.setdefault(kit_key(name), (list(path), name))
        return found


def _names(device: dict[str, Any]) -> set[str]:
    return {
        str(device.get(field, "")).strip()
        for field in ("name", "class_name", "class_display_name")
    }
