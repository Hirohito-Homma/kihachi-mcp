"""AI providers. Music services talk to this interface, not vendor URLs."""

from __future__ import annotations

import json
import os
from http.client import HTTPSConnection
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


class OllamaCloudProvider:
    """Ollama Cloud hosted models. The API key stays in the environment.

    Mirrors OllamaProvider's contract against https://ollama.com, so the
    part-wise MIDI Composer and brief interpretation work unchanged while
    generation runs on hosted models such as gemma4:31b (seconds instead of
    the local timeout-bound minutes). Usage is billed to the user's Ollama
    plan credits, not the Studio JPY cost guard, so ``paid_api`` is False.
    The key is read from OLLAMA_API_KEY only — never from settings or files.
    """

    HOST = "ollama.com"
    DEFAULT_CLOUD_MODEL = "gemma4:31b"

    def __init__(
        self,
        model: str = DEFAULT_CLOUD_MODEL,
        timeout: float = 180.0,
        api_key: str | None = None,
    ) -> None:
        self.model = model
        self._timeout = timeout
        self._api_key = api_key

    def _key(self) -> str | None:
        key = self._api_key if self._api_key is not None else os.environ.get("OLLAMA_API_KEY")
        return key or None

    def health(self) -> dict[str, Any]:
        if not self._key():
            return {
                "ok": False,
                "state": "api_key_missing",
                "provider": "ollama_cloud",
                "model": self.model,
                "installed_models": [],
                "message": "OLLAMA_API_KEYが設定されていません",
            }
        try:
            models = self.list_models()
        except InterpretationError as exc:
            return {
                "ok": False,
                "state": "unreachable",
                "provider": "ollama_cloud",
                "model": self.model,
                "installed_models": [],
                "message": str(exc),
            }
        return {
            "ok": True,
            "state": "ready",
            "provider": "ollama_cloud",
            "model": self.model,
            "installed_models": models,
            "message": "Ollama Cloudを利用できます",
        }

    def list_models(self) -> list[str]:
        data = self._request("GET", "/api/tags")
        return [str(item.get("name")) for item in data.get("models") or [] if item.get("name")]

    def generate(self, prompt: str) -> str:
        result = self._chat({
            "model": self.model,
            "stream": False,
            "think": False,
            "messages": [{"role": "user", "content": prompt}],
        })
        return str(result["message"]["content"])

    def generate_structured(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        result = self._chat({
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
        })
        try:
            parsed = parse_model_json(result["message"]["content"])
        except (KeyError, TypeError) as exc:
            raise InterpretationError("AI応答をJSONとして読めません") from exc
        if not isinstance(parsed, dict):
            raise InterpretationError("AI応答のJSONがオブジェクトではありません")
        return parsed

    def capabilities(self) -> dict[str, Any]:
        return {
            "provider": "ollama_cloud",
            "structured_output": True,
            "downloads_models": False,
            "paid_api": False,
        }

    def interpret(self, brief: str) -> ProductionBrief:
        """Interpret a brief via the cloud model, falling back when unusable."""
        if not self._key():
            return interpret_brief_offline(brief)
        from kihachi_mcp.services.studio_interpreter import (
            assemble_brief,
            extract_explicit,
        )

        prompt = (
            "Translate this Japanese music brief into the supplied schema. Preserve explicit tempo, key, bars and drop bar. "
            "Defaults: tech_house, 125, Dm, 96. Do not invent Ableton operations.\n\n"
            + brief
        )
        try:
            intent = self.generate_structured(prompt, STUDIO_SCHEMA)
        except InterpretationError:
            return interpret_brief_offline(brief)
        return assemble_brief(extract_explicit(brief), intent, model=self.model)

    def _chat(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/api/chat", payload)

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        key = self._key()
        if not key:
            raise InterpretationError("OLLAMA_API_KEYが設定されていません")
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        connection = HTTPSConnection(self.HOST, timeout=self._timeout)
        try:
            connection.request(method, path, body, {
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            })
            response = connection.getresponse()
            raw = response.read()
            status = response.status
        except OSError as exc:
            raise InterpretationError("Ollama Cloudに接続できません") from exc
        finally:
            connection.close()
        if status == 401:
            raise InterpretationError("OLLAMA_API_KEYが無効です")
        if status == 402:
            raise InterpretationError("Ollama Cloudの利用枠（クレジット）が不足しています")
        if status == 404:
            raise InterpretationError(f"Ollama Cloudにモデル {self.model} が見つかりません")
        if status == 429:
            raise InterpretationError("Ollama Cloudの利用上限に達しました")
        if status >= 400:
            raise InterpretationError(f"Ollama Cloudが応答を拒否しました (HTTP {status})")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise InterpretationError("Ollama Cloudの応答を読めません") from exc
        if not isinstance(data, dict):
            raise InterpretationError("Ollama Cloudの応答を読めません")
        return data


# Keys of the JSON Schema subset Gemini's responseSchema accepts.
_GEMINI_SCHEMA_KEYS = frozenset({
    "type", "format", "title", "description", "nullable", "enum",
    "items", "properties", "required", "minimum", "maximum",
    "minItems", "maxItems", "minLength", "maxLength",
})


def _gemini_schema(schema: Any) -> Any:
    """Reduce a JSON Schema to the subset Gemini's responseSchema takes.

    Unsupported constraints (additionalProperties, exclusiveMinimum, ...)
    are dropped, not enforced: the composer's server-side validation
    remains the real gate, the schema only guides generation. ``type``
    values are upper-cased to the native API's canonical enum form.
    """
    if isinstance(schema, dict):
        reduced: dict[str, Any] = {}
        for key, value in schema.items():
            if key not in _GEMINI_SCHEMA_KEYS:
                continue
            if key == "type" and isinstance(value, str):
                reduced[key] = value.upper()
            elif key == "properties" and isinstance(value, dict):
                # A property map: names are user keys, keep them all and
                # reduce only each property's own schema.
                reduced[key] = {
                    name: _gemini_schema(sub) for name, sub in value.items()
                }
            else:
                reduced[key] = _gemini_schema(value)
        return reduced
    if isinstance(schema, list):
        return [_gemini_schema(item) for item in schema]
    return schema


class GeminiProvider:
    """Google Gemini models via the native generateContent API.

    Mirrors OllamaCloudProvider's contract so the part-wise MIDI Composer
    and brief interpretation work unchanged while generation runs on
    hosted Gemini models (seconds per part instead of local minutes).
    The API key stays in the environment — GEMINI_API_KEY, falling back
    to GOOGLE_API_KEY — never in settings or files. Billing (or the free
    tier) belongs to the user's Google account, not the Studio JPY cost
    guard, so ``paid_api`` is False, as with Ollama Cloud.
    """

    HOST = "generativelanguage.googleapis.com"
    DEFAULT_MODEL = "gemini-2.5-flash"

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        timeout: float = 180.0,
        api_key: str | None = None,
    ) -> None:
        self.model = model
        self._timeout = timeout
        self._api_key = api_key
        self.last_usage: dict[str, int] = {}

    def _key(self) -> str | None:
        key = (
            self._api_key
            if self._api_key is not None
            else os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        )
        return key or None

    def health(self) -> dict[str, Any]:
        if not self._key():
            return {
                "ok": False,
                "state": "api_key_missing",
                "provider": "gemini",
                "model": self.model,
                "installed_models": [],
                "message": "GEMINI_API_KEYが設定されていません",
            }
        try:
            models = self.list_models()
        except InterpretationError as exc:
            return {
                "ok": False,
                "state": "unreachable",
                "provider": "gemini",
                "model": self.model,
                "installed_models": [],
                "message": str(exc),
            }
        return {
            "ok": True,
            "state": "ready",
            "provider": "gemini",
            "model": self.model,
            "installed_models": models,
            "message": "Geminiを利用できます",
        }

    def list_models(self) -> list[str]:
        data = self._request("GET", "/v1beta/models")
        names: list[str] = []
        for item in data.get("models") or []:
            methods = item.get("supportedGenerationMethods") or []
            if methods and "generateContent" not in methods:
                continue
            name = str(item.get("name") or "")
            if name:
                names.append(name.removeprefix("models/"))
        return names

    def generate(self, prompt: str) -> str:
        data = self._generate_content({
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"maxOutputTokens": 8192},
        })
        return self._text_of(data)

    def generate_structured(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        base: dict[str, Any] = {
            "systemInstruction": {
                "parts": [{"text": "Reply with JSON matching the schema. Do not add keys."}]
            },
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "maxOutputTokens": 32768,
            },
        }
        with_schema = json.loads(json.dumps(base))
        with_schema["generationConfig"]["responseSchema"] = _gemini_schema(schema)
        try:
            data = self._generate_content(with_schema)
        except InterpretationError as exc:
            if "HTTP 400" not in str(exc):
                raise
            # The endpoint rejected the (reduced) schema: retry once in
            # plain JSON mode. Server-side validation still gates notes.
            data = self._generate_content(base)
        parsed = parse_model_json(self._text_of(data))
        if not isinstance(parsed, dict):
            raise InterpretationError("AI応答のJSONがオブジェクトではありません")
        return parsed

    def capabilities(self) -> dict[str, Any]:
        return {
            "provider": "gemini",
            "structured_output": True,
            "downloads_models": False,
            "paid_api": False,
        }

    def interpret(self, brief: str) -> ProductionBrief:
        """Interpret a brief via Gemini, falling back when unusable."""
        if not self._key():
            return interpret_brief_offline(brief)
        from kihachi_mcp.services.studio_interpreter import (
            assemble_brief,
            extract_explicit,
        )

        prompt = (
            "Translate this Japanese music brief into the supplied schema. Preserve explicit tempo, key, bars and drop bar. "
            "Defaults: tech_house, 125, Dm, 96. Do not invent Ableton operations.\n\n"
            + brief
        )
        try:
            intent = self.generate_structured(prompt, STUDIO_SCHEMA)
        except InterpretationError:
            return interpret_brief_offline(brief)
        return assemble_brief(extract_explicit(brief), intent, model=self.model)

    def _generate_content(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request(
            "POST", f"/v1beta/models/{self.model}:generateContent", payload
        )

    def _text_of(self, data: dict[str, Any]) -> str:
        usage = data.get("usageMetadata")
        if isinstance(usage, dict):
            self.last_usage = {
                "input_tokens": int(usage.get("promptTokenCount") or 0),
                "output_tokens": int(usage.get("candidatesTokenCount") or 0),
            }
        for candidate in data.get("candidates") or []:
            content = candidate.get("content") or {}
            chunks = [
                str(part["text"])
                for part in content.get("parts") or []
                if isinstance(part, dict) and part.get("text")
            ]
            if chunks:
                return "\n".join(chunks)
        raise InterpretationError("Geminiの応答にテキストがありません")

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        key = self._key()
        if not key:
            raise InterpretationError("GEMINI_API_KEYが設定されていません")
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        connection = HTTPSConnection(self.HOST, timeout=self._timeout)
        try:
            connection.request(method, path, body, {
                "x-goog-api-key": key,
                "Content-Type": "application/json",
            })
            response = connection.getresponse()
            raw = response.read()
            status = response.status
        except OSError as exc:
            raise InterpretationError("Geminiに接続できません") from exc
        finally:
            connection.close()
        if status in (401, 403):
            raise InterpretationError("GEMINI_API_KEYが無効です")
        if status == 404:
            raise InterpretationError(f"Geminiにモデル {self.model} が見つかりません")
        if status == 429:
            raise InterpretationError("Geminiの利用上限に達しました")
        if status >= 400:
            raise InterpretationError(f"Geminiが応答を拒否しました (HTTP {status})")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise InterpretationError("Geminiの応答を読めません") from exc
        if not isinstance(data, dict):
            raise InterpretationError("Geminiの応答を読めません")
        return data


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


class OpenAIProvider:
    """Explicitly selected paid provider. The API key stays in the environment."""

    ALLOWED_MODELS = ("gpt-6.1-sol", "gpt-6-astra")

    def __init__(self, model: str = "gpt-6.1-sol", timeout: float = 180.0) -> None:
        if model not in self.ALLOWED_MODELS:
            raise ValueError("OpenAIモデルは Sol または Astra を選んでください")
        self.model = model
        self._timeout = timeout
        self.last_usage: dict[str, int] = {}

    def health(self) -> dict[str, Any]:
        configured = bool(os.environ.get("OPENAI_API_KEY"))
        return {
            "ok": configured,
            "state": "ready" if configured else "api_key_missing",
            "provider": "openai",
            "model": self.model,
            "installed_models": list(self.ALLOWED_MODELS),
            "message": "OpenAI APIを利用できます" if configured else "OPENAI_API_KEYが設定されていません",
        }

    def list_models(self) -> list[str]:
        return list(self.ALLOWED_MODELS)

    def test_connection(self) -> dict[str, Any]:
        """Check credentials and model access without creating a paid response."""
        key = os.environ.get("OPENAI_API_KEY")
        if not key:
            return {"ok": False, "model": self.model, "message": "OPENAI_API_KEYが設定されていません。GPT-6用ランチャーからStudioを起動してください。"}
        connection = HTTPSConnection("api.openai.com", timeout=10)
        try:
            connection.request("GET", f"/v1/models/{self.model}", headers={"Authorization": f"Bearer {key}"})
            response = connection.getresponse()
            raw = response.read()
        except OSError:
            return {"ok": False, "model": self.model, "message": "OpenAI APIに接続できません。ネットワークを確認してください。"}
        finally:
            connection.close()
        try:
            error_data = json.loads(raw)
        except json.JSONDecodeError:
            error_data = {}
        error = error_data.get("error") if isinstance(error_data, dict) else None
        error_code = error.get("code") if isinstance(error, dict) else None
        if response.status == 401 and error_code == "ip_not_authorized":
            message = "接続元IPがOpenAIプロジェクトの許可リストにありません（HTTP 401）。"
        elif response.status == 401:
            message = "APIキーが認証されませんでした（HTTP 401）。キー・プロジェクト・権限を確認してください。"
        elif response.status == 403:
            message = "このキーではモデルにアクセスできません（HTTP 403）。プロジェクトの権限を確認してください。"
        elif response.status == 404:
            message = "選択モデルが見つからないか、利用できません（HTTP 404）。"
        elif response.status >= 400:
            message = f"OpenAI APIが応答を拒否しました（HTTP {response.status}）。"
        else:
            if isinstance(error_data, dict) and error_data.get("id") == self.model:
                return {"ok": True, "model": self.model, "message": "APIキーとモデルへのアクセスを確認しました。生成は実行していません。"}
            message = "OpenAI APIのモデル情報を確認できませんでした。"
        return {"ok": False, "model": self.model, "message": message}

    def generate(self, prompt: str) -> str:
        data = self._request({
            "model": self.model,
            "input": prompt,
            "max_output_tokens": 1_000,
            "store": False,
        })
        return _response_text(data)

    def generate_structured(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        data = self._request({
            "model": self.model,
            "input": prompt,
            "max_output_tokens": 1_000,
            "store": False,
            "text": {"format": {"type": "json_schema", "name": "kihachi_result", "strict": True, "schema": schema}},
        })
        parsed = _structured_payload(data)
        if not isinstance(parsed, dict):
            raise InterpretationError("AI応答のJSONがオブジェクトではありません")
        return parsed

    def capabilities(self) -> dict[str, Any]:
        return {
            "provider": "openai",
            "structured_output": True,
            "downloads_models": False,
            "paid_api": True,
            "explicit_selection_required": True,
        }

    def interpret(self, brief: str) -> ProductionBrief:
        from kihachi_mcp.services.studio_interpreter import (
            assemble_brief,
            extract_explicit,
        )

        prompt = (
            "Translate this Japanese music brief into the supplied schema. Preserve explicit tempo, key, bars and drop bar. "
            "Defaults: tech_house, 125, Dm, 96. Do not invent Ableton operations.\n\n"
            + brief
        )
        intent = self.generate_structured(prompt, STUDIO_SCHEMA)
        return assemble_brief(extract_explicit(brief), intent, model=self.model)

    def _request(self, payload: dict[str, Any]) -> dict[str, Any]:
        key = os.environ.get("OPENAI_API_KEY")
        if not key:
            raise InterpretationError("OPENAI_API_KEYが設定されていません")
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        connection = HTTPSConnection("api.openai.com", timeout=self._timeout)
        try:
            connection.request("POST", "/v1/responses", body, {
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            })
            response = connection.getresponse()
            raw = response.read()
        except OSError as exc:
            raise InterpretationError("OpenAI APIに接続できません") from exc
        finally:
            connection.close()
        if response.status >= 400:
            raise InterpretationError(f"OpenAI APIが応答を拒否しました (HTTP {response.status})")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise InterpretationError("OpenAI APIの応答を読めません") from exc
        usage = data.get("usage") if isinstance(data, dict) else None
        if isinstance(usage, dict):
            self.last_usage = {
                "input_tokens": int(usage.get("input_tokens") or 0),
                "output_tokens": int(usage.get("output_tokens") or 0),
            }
        return data


def _structured_payload(data: dict[str, Any]) -> Any:
    """Read JSON from a Responses API payload. Reasoning-only replies are not treated as JSON."""
    if data.get("status") == "incomplete":
        reason = (data.get("incomplete_details") or {}).get("reason") or "unknown"
        raise InterpretationError(f"OpenAI APIの応答が途切れました（{reason}）")
    for item in data.get("output") or []:
        if item.get("type") in {"refusal", "refused"} or item.get("status") == "refused":
            raise InterpretationError("OpenAI APIが応答を拒否しました")
        for content in item.get("content") or []:
            if content.get("type") == "refusal" or content.get("refusal"):
                raise InterpretationError("OpenAI APIが応答を拒否しました")
            parsed = content.get("parsed")
            if isinstance(parsed, (dict, list)):
                return parsed
            if content.get("type") in {"output_json", "json"} and isinstance(content.get("json"), (dict, list)):
                return content["json"]
    return parse_model_json(_response_text(data))


def _response_text(data: dict[str, Any]) -> str:
    direct = data.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct
    chunks: list[str] = []
    for item in data.get("output") or []:
        if item.get("type") == "reasoning":
            continue
        for content in item.get("content") or []:
            text = content.get("text")
            if isinstance(text, str) and text.strip():
                chunks.append(text)
    if chunks:
        return "\n".join(chunks)
    raise InterpretationError("OpenAI APIの応答にテキストがありません")


def provider_from_settings(
    settings: dict[str, Any],
) -> OllamaProvider | OllamaCloudProvider | GeminiProvider | DeterministicProvider | OpenAIProvider:
    """Build only the provider explicitly selected in Studio settings."""
    name = str(settings.get("ai_provider") or "ollama")
    if name == "deterministic":
        return DeterministicProvider()
    if name == "openai":
        return OpenAIProvider(model=str(settings.get("openai_model") or "gpt-6.1-sol"))
    if name == "ollama_cloud":
        return OllamaCloudProvider(
            model=str(settings.get("ollama_cloud_model") or OllamaCloudProvider.DEFAULT_CLOUD_MODEL)
        )
    if name == "gemini":
        return GeminiProvider(
            model=str(settings.get("gemini_model") or GeminiProvider.DEFAULT_MODEL)
        )
    return OllamaProvider(
        url=str(settings.get("ollama_url") or f"http://{DEFAULT_HOST}:{DEFAULT_PORT}"),
        model=str(settings.get("ollama_model") or DEFAULT_MODEL),
    )


def studio_schema() -> dict[str, Any]:
    """Return the SongSpec-side schema the local model must fill."""
    return STUDIO_SCHEMA
