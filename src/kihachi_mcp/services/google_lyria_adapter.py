import base64
import hashlib
import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from kihachi_mcp.models import GenerationContext
from kihachi_mcp.models.audio_plan import AudioRenderRequest, AudioRenderResult

HttpCall = Callable[
    [str, str, dict[str, str], bytes | None], tuple[int, bytes, dict[str, str]]
]


class GoogleLyriaAdapter:
    """Generate Lyria audio without exposing Google provider details."""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        http_call: HttpCall | None = None,
    ) -> None:
        self.base_url = (
            base_url
            or os.getenv("LYRIA_BASE_URL")
            or "https://generativelanguage.googleapis.com/v1beta/interactions"
        ).rstrip("/")
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")
        self.model = model or os.getenv("LYRIA_MODEL") or "lyria-3.5"
        self._http_call = http_call or self._default_http_call

    def render(
        self,
        request: AudioRenderRequest,
        output_path: str | Path,
        context: GenerationContext | None = None,
    ) -> AudioRenderResult:
        """Generate one song and return a verified local MP3 receipt."""
        if not self.api_key:
            return AudioRenderResult(
                status="blocked", error="GEMINI_API_KEY is not configured"
            )
        try:
            status, body, _ = self._http_call(
                "POST",
                self.base_url,
                self._headers(),
                self._payload(request, context),
            )
            if status in {401, 403}:
                return AudioRenderResult(
                    status="blocked", error="Google Lyria authentication was rejected"
                )
            if status == 429:
                return AudioRenderResult(
                    status="blocked", error="Google Lyria quota or rate limit reached"
                )
            if status >= 400:
                return AudioRenderResult(
                    status="failed",
                    error=f"Google Lyria request failed (HTTP {status})",
                )
            data = json.loads(body)
            audio_data = self._audio_data(data)
            if not audio_data:
                raise ValueError("Lyria returned no audio data")
            audio = base64.b64decode(audio_data, validate=True)
            if not audio:
                raise ValueError("Lyria returned an empty audio artifact")
            path = Path(output_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(audio)
            return AudioRenderResult(
                status="succeeded",
                task_id=str(data.get("id") or ""),
                artifact_path=str(path),
                sample_rate=44100,
                channels=2,
                sha256=hashlib.sha256(audio).hexdigest(),
            )
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            return AudioRenderResult(status="failed", error=str(exc))

    def _payload(
        self,
        request: AudioRenderRequest,
        context: GenerationContext | None = None,
    ) -> bytes:
        parts = [
            f"Genre: {request.genre}",
            f"target: {request.target_track}",
            f"tempo: {request.tempo} BPM",
            f"key: {request.key}",
            f"duration: {request.length_minutes:g} minutes.",
        ]
        template = context.knowledge.template() if context is not None else None
        if template is not None:
            parts.insert(1, f"mood: {context.parameters.mood or template.mood}")
            parts.insert(2, f"tracks: {', '.join(template.tracks)}")
        prompt = f"{'; '.join(parts)} {request.prompt}".strip()
        if request.negative_prompt:
            prompt += f" Avoid: {request.negative_prompt}"
        return json.dumps({"model": self.model, "input": prompt}).encode()

    @staticmethod
    def _audio_data(data: dict[str, Any]) -> str:
        direct = data.get("output_audio")
        if isinstance(direct, dict) and isinstance(direct.get("data"), str):
            return direct["data"]
        for step in data.get("steps") or []:
            for block in step.get("content") or []:
                if block.get("type") == "audio" and isinstance(block.get("data"), str):
                    return block["data"]
        return ""

    def _headers(self) -> dict[str, str]:
        return {
            "x-goog-api-key": self.api_key or "",
            "Content-Type": "application/json",
        }

    @staticmethod
    def _default_http_call(
        method: str, url: str, headers: dict[str, str], body: bytes | None
    ) -> tuple[int, bytes, dict[str, str]]:
        request = Request(url, data=body, headers=headers, method=method)
        try:
            with urlopen(request, timeout=30) as response:
                return response.status, response.read(), dict(response.headers.items())
        except (HTTPError, URLError) as exc:
            raise OSError(str(exc)) from exc
