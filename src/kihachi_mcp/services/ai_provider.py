"""Local AI providers. Music services talk to this interface, not to Ollama URLs."""

from __future__ import annotations

from typing import Any, Protocol
from urllib.parse import urlparse

from kihachi_mcp.models.production_brief import ProductionBrief
from kihachi_mcp.services.ollama_status import probe_ollama
from kihachi_mcp.services.studio_interpreter import (
    DEFAULT_HOST,
    DEFAULT_MODEL,
    DEFAULT_PORT,
    STUDIO_SCHEMA,
    InterpretationError,
    OllamaClient,
    interpret_brief_offline,
    interpret_with_fallback,
    parse_model_json,
)


def split_ollama_url(url: str) -> tuple[str, int]:
    """Return host and port for a configured Ollama base URL."""
    parsed = urlparse(url if "://" in url else f"http://{url}")
    host = parsed.hostname or DEFAULT_HOST
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("OllamaのURLは手元のループバックだけを使えます")
    return host, parsed.port or DEFAULT_PORT


class AIProvider(Protocol):
    """Reasoning provider used by Studio, the CLI, and MCP."""

    def health(self) -> dict[str, Any]:
        """Return readiness without downloading a model."""

    def list_models(self) -> list[str]:
        """Return installed model names."""

    def generate(self, prompt: str) -> str:
        """Return one text completion."""

    def generate_structured(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        """Return schema-shaped JSON."""

    def capabilities(self) -> dict[str, Any]:
        """Describe what this provider can do offline."""


class OllamaProvider:
    """The installed local Ollama daemon. Never downloads a model."""

    def __init__(
        self,
        url: str = f"http://{DEFAULT_HOST}:{DEFAULT_PORT}",
        model: str = DEFAULT_MODEL,
        timeout: float = 180.0,
    ) -> None:
        self.url = url
        self.model = model
        self._timeout = timeout
        self._host, self._port = split_ollama_url(url)

    def health(self) -> dict[str, Any]:
        report = probe_ollama(self._host, self._port, self.model)
        report["provider"] = "ollama"
        report["url"] = self.url
        return report

    def list_models(self) -> list[str]:
        return list(self.health().get("installed_models") or [])

    def generate(self, prompt: str) -> str:
        client = OllamaClient(self._host, self._port, timeout=self._timeout)
        result = client.chat(
            {
                "model": self.model,
                "stream": False,
                "think": False,
                "messages": [{"role": "user", "content": prompt}],
            }
        )
        return str(result["message"]["content"])

    def generate_structured(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        client = OllamaClient(self._host, self._port, timeout=self._timeout)
        result = client.chat(
            {
                "model": self.model,
                "stream": False,
                "think": False,
                "format": schema,
                "messages": [
                    {
                        "role": "system",
                        "content": "Reply with JSON matching the schema. Do not add keys.",
                    },
                    {"role": "user", "content": prompt},
                ],
            }
        )
        try:
            parsed = parse_model_json(result["message"]["content"])
        except (KeyError, TypeError) as exc:
            raise InterpretationError("AI応答をJSONとして読めません") from exc
        if not isinstance(parsed, dict):
            raise InterpretationError("AI応答のJSONがオブジェクトではありません")
        return parsed

    def capabilities(self) -> dict[str, Any]:
        return {
            "provider": "ollama",
            "structured_output": True,
            "downloads_models": False,
            "paid_api": False,
        }

    def interpret(self, brief: str) -> ProductionBrief:
        """Interpret a production brief, falling back when Ollama is offline."""
        if not self.health()["ok"]:
            return interpret_brief_offline(brief)
        return interpret_with_fallback(
            brief,
            lambda: OllamaClient(self._host, self._port, timeout=self._timeout),
            model=self.model,
        )


class DeterministicProvider:
    """Preset interpretation used when no model is selected."""

    def health(self) -> dict[str, Any]:
        return {
            "ok": True,
            "state": "ready",
            "provider": "deterministic",
            "model": "deterministic",
            "installed_models": [],
            "message": "AIなしの既定解釈が使えます",
        }

    def list_models(self) -> list[str]:
        return []

    def generate(self, prompt: str) -> str:
        raise InterpretationError("決定論プロバイダは自由文を生成しません")

    def generate_structured(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        _ = prompt, schema
        raise InterpretationError("決定論プロバイダはスキーマ生成を行いません")

    def capabilities(self) -> dict[str, Any]:
        return {
            "provider": "deterministic",
            "structured_output": False,
            "downloads_models": False,
            "paid_api": False,
        }

    def interpret(self, brief: str) -> ProductionBrief:
        return interpret_brief_offline(brief)


def provider_from_settings(settings: dict[str, Any]) -> OllamaProvider | DeterministicProvider:
    """Build the configured provider. Cloud providers are not enabled."""
    name = str(settings.get("ai_provider") or "ollama")
    if name == "deterministic":
        return DeterministicProvider()
    return OllamaProvider(
        url=str(settings.get("ollama_url") or f"http://{DEFAULT_HOST}:{DEFAULT_PORT}"),
        model=str(settings.get("ollama_model") or DEFAULT_MODEL),
    )


def studio_schema() -> dict[str, Any]:
    """Return the SongSpec-side schema the local model must fill."""
    return STUDIO_SCHEMA
