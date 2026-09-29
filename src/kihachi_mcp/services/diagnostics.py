"""Shared diagnostics for Studio and `kihachi doctor`."""

from __future__ import annotations

import importlib.util
import json
import os
import socket
import sys
from collections.abc import Callable
from http.client import HTTPConnection
from pathlib import Path
from typing import Any

from kihachi_mcp.services.ai_provider import provider_from_settings
from kihachi_mcp.services.live_bridge import DEFAULT_DEVICE_PORT
from kihachi_mcp.services.live_paths import candidate_store_dir
from kihachi_mcp.services.studio_interpreter import (
    DEFAULT_BARS,
    DEFAULT_GENRE,
    DEFAULT_MODEL,
    DEFAULT_TEMPO,
)

STUDIO_HOST = "127.0.0.1"
STUDIO_PORT = 8765
STATIC_INDEX = Path(__file__).resolve().parents[1] / "studio" / "static" / "index.html"


def default_settings() -> dict[str, Any]:
    """Read provider settings from the environment, then the Studio's saved choices."""
    from kihachi_mcp.services.studio_workflow import load_saved_settings

    settings = {
        "ai_provider": os.environ.get("KIHACHI_AI_PROVIDER", "ollama"),
        "ollama_url": os.environ.get("KIHACHI_OLLAMA_URL", "http://127.0.0.1:11434"),
        "ollama_model": os.environ.get("KIHACHI_OLLAMA_MODEL", DEFAULT_MODEL),
        "openai_model": os.environ.get("KIHACHI_OPENAI_MODEL", "gpt-6.1-sol"),
        "monthly_ai_limit_jpy": int(os.environ.get("KIHACHI_MONTHLY_AI_LIMIT_JPY", "500")),
        "ableton_host": "127.0.0.1",
        "ableton_port": int(os.environ.get("KIHACHI_LIVE_BRIDGE_PORT", DEFAULT_DEVICE_PORT)),
        "abletongpt_port": int(os.environ.get("KIHACHI_ABLETONGPT_PORT", "9877")),
        # Used when a brief names none of these; not configurable.
        "default_bpm": DEFAULT_TEMPO,
        "default_bars": DEFAULT_BARS,
        "default_style": DEFAULT_GENRE,
        "project_dir": str(candidate_store_dir()),
    }
    if not os.environ.get("KIHACHI_IGNORE_SAVED_SETTINGS"):
        settings.update(load_saved_settings())
    return settings


class DiagnosticsService:
    """One checklist used by the Studio page and the doctor command.

    ``live_probe`` returns the Studio runtime's Live health. Without it (the
    CLI), the running Studio is asked, because only one process may own the
    Live bridge's reply port.
    """

    def __init__(
        self,
        settings: dict[str, Any] | None = None,
        live_probe: Callable[[], dict[str, Any]] | None = None,
        remote_script_probe: Callable[[], bool] | None = None,
    ) -> None:
        self.settings = dict(default_settings() if settings is None else settings)
        self._live_probe = live_probe
        self._remote_script_probe = remote_script_probe

    def run(self) -> dict[str, Any]:
        """Check local services. Ableton may be absent without failing the rest."""
        checks = [
            self._python(),
            self._dependencies(),
            self._package(),
            self._config(),
            self._storage(),
            self._ollama(),
            self._model(),
            self._mcp(),
            self._abletongpt(),
            self._remote_script(),
            self._ableton_live(),
            self._studio_backend(),
            self._studio_frontend(),
        ]
        failed = [item for item in checks if item["status"] == "FAIL" and item["required"]]
        return {
            "ok": not failed,
            "checks": checks,
            "settings": _public_settings(self.settings),
            "external_verification": [
                item["name"]
                for item in checks
                if item["status"] in {"OFFLINE", "EXTERNAL"}
            ],
        }

    def _python(self) -> dict[str, Any]:
        ok = sys.version_info >= (3, 12)
        return _check(
            "Python",
            "PASS" if ok else "FAIL",
            f"{sys.version.split()[0]}",
            "Python 3.12 以上を入れてください。",
            required=True,
        )

    def _dependencies(self) -> dict[str, Any]:
        missing = [
            name for name in ("fastmcp", "numpy", "yaml") if importlib.util.find_spec(name) is None
        ]
        if missing:
            return _check(
                "Dependencies",
                "FAIL",
                f"見つからない: {', '.join(missing)}",
                "リポジトリで ./scripts/setup.sh（または uv sync --group dev）を実行してください。",
                True,
            )
        return _check("Dependencies", "PASS", "fastmcp, numpy, pyyaml", "", True)

    def _package(self) -> dict[str, Any]:
        ok = importlib.util.find_spec("kihachi_mcp") is not None
        return _check(
            "kihachi-mcp",
            "PASS" if ok else "FAIL",
            "パッケージを読み込めます" if ok else "パッケージがありません",
            "リポジトリで uv sync --group dev を実行してください。",
            required=True,
        )

    def _config(self) -> dict[str, Any]:
        from kihachi_mcp.services.studio_workflow import settings_path

        path = settings_path()
        if not path.exists():
            return _check("Config", "PASS", "既定値と環境変数を使っています", "", True)
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return _check(
                "Config",
                "FAIL",
                f"{path} を読めません ({exc.__class__.__name__})",
                "このファイルを削除すると既定値に戻ります。",
                True,
            )
        return _check("Config", "PASS", str(path), "", True)

    def _storage(self) -> dict[str, Any]:
        path = Path(self.settings["project_dir"])
        try:
            path.mkdir(parents=True, exist_ok=True)
            probe = path / ".kihachi-write-probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
        except OSError as exc:
            return _check("Storage", "FAIL", str(exc), "プロジェクトフォルダの権限を確認してください。", True)
        return _check("Storage", "PASS", str(path), "", True)

    def _ollama(self) -> dict[str, Any]:
        if self.settings.get("ai_provider") == "deterministic":
            return _check("Ollama", "WARNING", "AIなしの既定解釈を使う設定です", "", False)
        try:
            report = provider_from_settings(self.settings).health()
        except ValueError as exc:
            return _check("Ollama", "FAIL", str(exc), "KIHACHI_OLLAMA_URL を手元のURLにしてください。", False)
        if report.get("ok") or report.get("state") == "model_missing":
            return _check("Ollama", "PASS", f"{report.get('url')} が応答しています", "", False)
        if report.get("state") == "not_running":
            return _check(
                "Ollama",
                "OFFLINE",
                "Ollama service is not responding.",
                "ターミナルで ollama serve を実行してください。制作は既定値でも続けられます。",
                False,
            )
        return _check("Ollama", "WARNING", str(report.get("message") or ""), "", False)

    def _model(self) -> dict[str, Any]:
        if self.settings.get("ai_provider") == "deterministic":
            return _check("Model", "WARNING", "deterministic", "", False)
        try:
            report = provider_from_settings(self.settings).health()
        except ValueError as exc:
            return _check("Model", "FAIL", str(exc), "", False)
        if report.get("state") == "ready":
            return _check("Model", "PASS", str(report.get("model")), "", False)
        if report.get("state") == "model_missing":
            installed = ", ".join(report.get("installed_models") or []) or "なし"
            return _check(
                "Model",
                "WARNING",
                f"'{report.get('model')}' が見つかりません（導入済み: {installed}）",
                "自動ダウンロードはしません。設定で導入済みのモデルを選んでください。",
                False,
            )
        return _check("Model", "OFFLINE", "モデルを確認できません", "先に Ollama を起動してください。", False)

    def _mcp(self) -> dict[str, Any]:
        try:
            from kihachi_mcp import server
        except Exception as exc:  # noqa: BLE001 - report any import failure as a check
            return _check("MCP", "FAIL", exc.__class__.__name__, "uv sync --group dev を実行してください。", True)
        return _check("MCP", "PASS", f"{server.mcp.name}", "", True)

    def _abletongpt(self) -> dict[str, Any]:
        root = _abletongpt_root()
        if root is None:
            return _check(
                "AbletonGPT",
                "WARNING",
                "リポジトリが見つかりません",
                "KIHACHI_ABLETONGPT_ROOT に AbletonGPT の場所を設定してください。",
                False,
            )
        return _check("AbletonGPT", "PASS", str(root), "", False)

    def _remote_script(self) -> dict[str, Any]:
        port = int(self.settings.get("abletongpt_port") or 9877)
        state = remote_script_state(port, self._remote_script_probe)
        if state == "ready":
            return _check("Remote Script", "PASS", f"127.0.0.1:{port} が ping に応答しました", "", False)
        if state == "no_reply":
            return _check(
                "Remote Script",
                "WARNING",
                f"127.0.0.1:{port} は開いていますが ping に応答しません",
                "Live でダイアログが開いていないか確認し、閉じてから再確認してください（Remote Script は Live のメインスレッドで動きます）。",
                False,
            )
        return _check(
            "Remote Script",
            "OFFLINE",
            "AbletonGPT の Remote Script が応答しません",
            "Live の設定 → Link, Tempo & MIDI → Control Surface で AbletonGPT_MCP を選んでください（付属キットの読み込みに使います）。",
            False,
        )

    def _ableton_live(self) -> dict[str, Any]:
        live = self._live_probe() if self._live_probe is not None else _studio_live_health()
        if live is None:
            return _check(
                "Ableton Live",
                "WARNING",
                "制作画面が起動していないため確認できません",
                "kihachi start で制作画面を起動してから再確認してください（Liveとの接続は制作画面が持ちます）。",
                False,
            )
        if live.get("state") == "ready":
            return _check("Ableton Live", "PASS", str(live.get("message") or ""), "", False)
        return _check(
            "Ableton Live",
            "OFFLINE",
            str(live.get("message") or "EXTERNAL VERIFICATION REQUIRED"),
            "Ableton Live を開き、KIHACHI Live Device を1つ入れてから再確認してください。",
            False,
        )

    def _studio_backend(self) -> dict[str, Any]:
        if self._live_probe is not None or port_open(STUDIO_HOST, STUDIO_PORT):
            return _check("Studio backend", "PASS", f"http://{STUDIO_HOST}:{STUDIO_PORT}/", "", False)
        return _check(
            "Studio backend",
            "WARNING",
            "制作画面はまだ起動していません",
            "kihachi start を実行してください。",
            False,
        )

    def _studio_frontend(self) -> dict[str, Any]:
        if STATIC_INDEX.is_file():
            return _check("Studio frontend", "PASS", str(STATIC_INDEX.name), "", True)
        return _check("Studio frontend", "FAIL", "index.html がありません", "リポジトリを取得し直してください。", True)


def _check(name: str, status: str, detail: str, fix: str, required: bool) -> dict[str, Any]:
    return {
        "name": name,
        "status": status,
        "detail": detail,
        "fix": fix,
        "required": required,
    }


def _public_settings(settings: dict[str, Any]) -> dict[str, Any]:
    hidden = {"token", "secret", "key", "password"}
    return {
        name: value
        for name, value in settings.items()
        if not any(part in name.lower() for part in hidden)
    }


def port_open(host: str, port: int, timeout: float = 0.3) -> bool:
    probe = socket.socket()
    try:
        probe.settimeout(timeout)
        return probe.connect_ex((host, port)) == 0
    except OSError:
        return False
    finally:
        probe.close()


def remote_script_state(port: int = 9877, probe: Callable[[], bool] | None = None) -> str:
    """"ready" on a real ping reply; an open port alone is only "no_reply"."""
    if not port_open("127.0.0.1", port):
        return "offline"
    if probe is None:
        from kihachi_mcp.services.abletongpt_kits import AbletonGPTKitLoader

        probe = AbletonGPTKitLoader().available
    return "ready" if probe() else "no_reply"


def _studio_live_health() -> dict[str, Any] | None:
    """Ask the running Studio for its Live state; None when it is not running."""
    if not port_open(STUDIO_HOST, STUDIO_PORT):
        return None
    try:
        connection = HTTPConnection(STUDIO_HOST, STUDIO_PORT, timeout=5.0)
        connection.request("GET", "/api/health")
        payload = json.loads(connection.getresponse().read(1_000_000))
        connection.close()
    except (OSError, json.JSONDecodeError):
        return None
    live = payload.get("live") if isinstance(payload, dict) else None
    return live if isinstance(live, dict) else None


def _abletongpt_root() -> Path | None:
    override = os.environ.get("KIHACHI_ABLETONGPT_ROOT")
    candidates = []
    if override:
        candidates.append(Path(override))
    repo = Path(__file__).resolve().parents[3]
    candidates.append(repo.parent / "abletongpt")
    for path in candidates:
        if (path / "pyproject.toml").is_file():
            return path
    return None
