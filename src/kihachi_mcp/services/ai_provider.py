"""AI providers. Music services talk to this interface, not vendor URLs."""

from __future__ import annotations

import base64
import json
import os
import ssl
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

    DEFAULT_MAX_OUTPUT_TOKENS = 1_000
    MAX_STRUCTURED_OUTPUT_TOKENS = 32_768
    TOKENS_PER_SCHEMA_ITEM = 30
    STRUCTURED_OVERHEAD_TOKENS = 256

    def generate(self, prompt: str) -> str:
        data = self._request({
            "model": self.model,
            "input": prompt,
            "max_output_tokens": self.DEFAULT_MAX_OUTPUT_TOKENS,
            "store": False,
        })
        return _response_text(data)

    def generate_structured(
        self,
        prompt: str,
        schema: dict[str, Any],
        max_output_tokens: int | None = None,
    ) -> dict[str, Any]:
        limit = (
            _clamp_structured_output_tokens(max_output_tokens)
            if max_output_tokens is not None
            else _structured_max_output_tokens(schema)
        )
        data = self._request({
            "model": self.model,
            "input": prompt,
            "max_output_tokens": limit,
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


def _clamp_structured_output_tokens(tokens: int) -> int:
    if tokens < OpenAIProvider.DEFAULT_MAX_OUTPUT_TOKENS:
        return OpenAIProvider.DEFAULT_MAX_OUTPUT_TOKENS
    if tokens > OpenAIProvider.MAX_STRUCTURED_OUTPUT_TOKENS:
        raise InterpretationError(
            f"要求された構造化出力上限（{tokens}トークン）が安全上限（{OpenAIProvider.MAX_STRUCTURED_OUTPUT_TOKENS}トークン）を超えています"
        )
    return tokens


def _structured_max_output_tokens(schema: dict[str, Any]) -> int:
    """Compute a bounded output token budget aligned with the schema's maxItems."""
    properties = schema.get("properties") if isinstance(schema, dict) else None
    max_items = 0
    if isinstance(properties, dict):
        for prop in properties.values():
            if isinstance(prop, dict) and prop.get("type") == "array":
                item_limit = prop.get("maxItems")
                if isinstance(item_limit, int) and item_limit > max_items:
                    max_items = item_limit
    if max_items <= 0:
        return OpenAIProvider.DEFAULT_MAX_OUTPUT_TOKENS
    required_tokens = (
        max_items * OpenAIProvider.TOKENS_PER_SCHEMA_ITEM
        + OpenAIProvider.STRUCTURED_OVERHEAD_TOKENS
    )
    return _clamp_structured_output_tokens(required_tokens)


def _structured_payload(data: dict[str, Any]) -> Any:
    """Read JSON from a Responses API payload. Reasoning-only or incomplete replies are rejected."""
    incomplete_details = data.get("incomplete_details")
    if data.get("status") == "incomplete" or (isinstance(incomplete_details, dict) and incomplete_details.get("reason")):
        reason = (incomplete_details.get("reason") if isinstance(incomplete_details, dict) else None) or "unknown"
        raise InterpretationError(f"OpenAI APIの応答が途切れました（{reason}）")
    for item in data.get("output") or []:
        if item.get("status") == "incomplete":
            reason = (item.get("incomplete_details") or {}).get("reason") or "output_incomplete"
            raise InterpretationError(f"OpenAI APIの応答が途切れました（{reason}）")
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
    raw_text = _response_text(data).strip()
    if not (raw_text.startswith("{") and raw_text.endswith("}")):
        raise InterpretationError("AI応答をJSONとして読めません")
    try:
        return json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise InterpretationError("AI応答をJSONとして読めません") from exc


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


def _clean_schema_for_gemini(schema: Any) -> Any:
    """Strip unsupported OpenAPI keywords and uppercase types for Gemini responseSchema."""
    if isinstance(schema, dict):
        cleaned = {}
        unsupported = (
            "additionalProperties",
            "$schema",
            "exclusiveMinimum",
            "exclusiveMaximum",
            "minimum",
            "maximum",
            "maxItems",
            "minItems",
        )
        for k, v in schema.items():
            if k in unsupported:
                continue
            if k == "type" and isinstance(v, str):
                cleaned[k] = v.upper()
            else:
                cleaned[k] = _clean_schema_for_gemini(v)
        return cleaned
    elif isinstance(schema, list):
        return [_clean_schema_for_gemini(item) for item in schema]
    return schema


def _gemini_ssl_context() -> ssl.SSLContext:
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()



class GeminiAIProvider:
    """Explicitly selected Google Gemini provider. The API key stays in the environment."""

    ALLOWED_MODELS = (
        "gemini-3.8-flash",
        "gemini-3.7-flash",
        "gemini-3.5-flash",
        "gemini-3.5-flash-lite",
        "gemini-flash-lite-latest",
        "gemini-2.5-flash",
        "gemini-2.5-pro",
        "gemini-1.5-flash",
        "gemini-1.5-pro",
    )
    DEFAULT_MODEL = "gemini-3.8-flash"
    HOST = "generativelanguage.googleapis.com"

    def __init__(self, model: str = DEFAULT_MODEL, timeout: float = 60.0) -> None:
        if model not in self.ALLOWED_MODELS:
            raise ValueError(f"Geminiモデルは次のいずれかを選んでください: {', '.join(self.ALLOWED_MODELS)}")
        self.model = model
        self._timeout = timeout
        self.last_usage: dict[str, int] = {}

    @staticmethod
    def get_api_key() -> str | None:
        """Fetch API key from environment variables without exposing it."""
        return os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")

    def health(self) -> dict[str, Any]:
        key = self.get_api_key()
        configured = bool(key)
        return {
            "ok": configured,
            "state": "ready" if configured else "api_key_missing",
            "provider": "gemini",
            "model": self.model,
            "installed_models": list(self.ALLOWED_MODELS),
            "message": "Gemini APIを利用できます" if configured else "GEMINI_API_KEY または GOOGLE_API_KEY が設定されていません",
        }

    def list_models(self) -> list[str]:
        return list(self.ALLOWED_MODELS)

    def test_connection(self) -> dict[str, Any]:
        key = self.get_api_key()
        if not key:
            return {"ok": False, "model": self.model, "message": "GEMINI_API_KEY または GOOGLE_API_KEY が設定されていません。"}
        connection = HTTPSConnection(self.HOST, timeout=10, context=_gemini_ssl_context())
        try:
            connection.request("GET", f"/v1beta/models/{self.model}", headers={"x-goog-api-key": key})
            response = connection.getresponse()
            _ = response.read()
        except OSError:
            return {"ok": False, "model": self.model, "message": "Gemini APIに接続できません。ネットワークを確認してください。"}
        finally:
            connection.close()

        if response.status == 400:
            return {"ok": False, "model": self.model, "message": "不正なリクエストです（HTTP 400）。"}
        if response.status == 404:
            return {"ok": False, "model": self.model, "message": f"指定されたモデル（{self.model}）が見つかりません（HTTP 404）。"}
        if response.status in (401, 403):
            return {"ok": False, "model": self.model, "message": f"APIキーが無効であるか、権限がありません（HTTP {response.status}）。"}
        if response.status == 429:
            return {"ok": False, "model": self.model, "message": "Gemini APIのレート制限・割当上限（429: RESOURCE_EXHAUSTED）に達しています。"}
        if response.status >= 500:
            return {"ok": False, "model": self.model, "message": f"Gemini APIサーバー側の一時エラーです（HTTP {response.status}）。"}
        if response.status == 200:
            return {"ok": True, "model": self.model, "message": "Gemini APIとの接続とモデル利用権限を確認しました。"}
        return {"ok": False, "model": self.model, "message": f"Gemini APIが応答を拒否しました（HTTP {response.status}）。"}

    def generate(self, prompt: str) -> str:
        payload = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.4},
        }
        res = self._request(payload)
        return _extract_gemini_text(res)

    def generate_structured(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        cleaned_schema = _clean_schema_for_gemini(schema)
        payload = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.2,
                "maxOutputTokens": max(8192, _structured_max_output_tokens(schema)),
                "responseMimeType": "application/json",
                "responseSchema": cleaned_schema,
            },
        }
        res = self._request(payload)
        text = _extract_gemini_text(res)
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise InterpretationError(f"Geminiの応答をJSONとして解析できません: {exc}") from exc
        if not isinstance(parsed, dict):
            raise InterpretationError("Geminiの応答がオブジェクト形式ではありません")
        return parsed

    MAX_INLINE_AUDIO_BYTES = 20 * 1024 * 1024  # 20MB limit for inlineData

    def generate_with_audio(
        self,
        prompt: str,
        audio_bytes: bytes,
        mime_type: str = "audio/wav",
        schema: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Send audio clip directly to Gemini multimodal API (Mode B)."""
        if len(audio_bytes) > self.MAX_INLINE_AUDIO_BYTES:
            raise InterpretationError(
                f"音声データのサイズ（{len(audio_bytes) / 1024 / 1024:.1f}MB）がGemini inlineDataの上限（20MB）を超えています。区間を短くしてください。"
            )

        b64_audio = base64.b64encode(audio_bytes).decode("ascii")
        parts: list[dict[str, Any]] = [
            {"inlineData": {"mimeType": mime_type, "data": b64_audio}},
            {"text": prompt},
        ]
        config: dict[str, Any] = {"temperature": 0.2}
        if schema:
            config["responseMimeType"] = "application/json"
            config["responseSchema"] = _clean_schema_for_gemini(schema)

        payload = {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": config,
        }
        res = self._request(payload)
        text = _extract_gemini_text(res)
        if schema:
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError as exc:
                raise InterpretationError(f"Geminiの応答をJSONとして解析できません: {exc}") from exc
            if not isinstance(parsed, dict):
                raise InterpretationError("Geminiの応答がオブジェクト形式ではありません")
            return parsed
        return {"text": text}

    def capabilities(self) -> dict[str, Any]:
        return {
            "provider": "gemini",
            "structured_output": True,
            "downloads_models": False,
            "paid_api": True,
            "explicit_selection_required": True,
        }

    def _request(self, payload: dict[str, Any]) -> dict[str, Any]:
        key = self.get_api_key()
        if not key:
            raise InterpretationError("GEMINI_API_KEY または GOOGLE_API_KEY が設定されていません")
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        path = f"/v1beta/models/{self.model}:generateContent"
        connection = HTTPSConnection(self.HOST, timeout=self._timeout, context=_gemini_ssl_context())
        try:
            connection.request(
                "POST",
                path,
                body=body,
                headers={
                    "x-goog-api-key": key,
                    "Content-Type": "application/json",
                },
            )
            response = connection.getresponse()
            raw = response.read()
        except OSError as exc:
            raise InterpretationError("Gemini APIに接続できません。ネットワークを確認してください。") from exc
        finally:
            connection.close()

        if response.status == 429:
            raise InterpretationError("Gemini APIのレート制限・割当上限（HTTP 429: RESOURCE_EXHAUSTED）に達しました。時間を置いてお試しください。")
        if response.status in (401, 403):
            raise InterpretationError(f"Gemini APIキーが無効であるか、権限がありません（HTTP {response.status}）")
        if response.status >= 400:
            err_detail = ""
            try:
                err_data = json.loads(raw)
                if isinstance(err_data, dict) and "error" in err_data:
                    raw_msg = str(err_data["error"].get("message") or "").strip()
                    if raw_msg:
                        # 機密情報（APIキーやURLクエリ等）を除外し、安全なエラー概要のみ付与
                        safe_msg = raw_msg.splitlines()[0][:160]
                        err_detail = f": {safe_msg}"
            except Exception:
                pass
            raise InterpretationError(f"Gemini APIがエラーを返しました (HTTP {response.status}{err_detail})")

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise InterpretationError("Gemini APIの応答を読めません") from exc

        usage = data.get("usageMetadata") if isinstance(data, dict) else None
        if isinstance(usage, dict):
            self.last_usage = {
                "input_tokens": int(usage.get("promptTokenCount") or 0),
                "output_tokens": int(usage.get("candidatesTokenCount") or 0),
            }
        return data


def _extract_gemini_text(data: dict[str, Any]) -> str:
    candidates = data.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        prompt_feedback = data.get("promptFeedback")
        if isinstance(prompt_feedback, dict) and prompt_feedback.get("blockReason"):
            raise InterpretationError(f"Geminiがプロンプトをブロックしました（{prompt_feedback['blockReason']}）")
        raise InterpretationError("Geminiの応答に候補（candidates）が含まれていません")

    first = candidates[0]
    finish_reason = first.get("finishReason")
    if finish_reason in ("SAFETY", "RECITATION"):
        raise InterpretationError(f"Geminiの応答が安全制限により停止しました（{finish_reason}）")

    content = first.get("content", {})
    parts = content.get("parts", [])
    chunks = [p.get("text", "") for p in parts if isinstance(p, dict) and "text" in p]
    if chunks:
        return "".join(chunks).strip()
    raise InterpretationError("Geminiの応答パーツにテキストがありません")


def provider_from_settings(settings: dict[str, Any]) -> OllamaProvider | DeterministicProvider | OpenAIProvider | GeminiAIProvider:
    """Build only the provider explicitly selected in Studio settings."""
    name = str(settings.get("ai_provider") or "ollama")
    if name == "deterministic":
        return DeterministicProvider()
    if name == "openai":
        return OpenAIProvider(model=str(settings.get("openai_model") or "gpt-6.1-sol"))
    if name == "gemini":
        return GeminiAIProvider(model=str(settings.get("gemini_model") or GeminiAIProvider.DEFAULT_MODEL))
    return OllamaProvider(
        url=str(settings.get("ollama_url") or f"http://{DEFAULT_HOST}:{DEFAULT_PORT}"),
        model=str(settings.get("ollama_model") or DEFAULT_MODEL),
    )


def studio_schema() -> dict[str, Any]:
    """Return the SongSpec-side schema the local model must fill."""
    return STUDIO_SCHEMA
