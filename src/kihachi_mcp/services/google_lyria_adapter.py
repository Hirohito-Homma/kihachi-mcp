import base64
import binascii
import hashlib
import json
import os
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from kihachi_mcp.models import GenerationContext
from kihachi_mcp.models.audio_plan import AudioRenderRequest, AudioRenderResult
from kihachi_mcp.services.lyria_prompt_builder import LyriaPromptBuilder

HttpCall = Callable[
    [str, str, dict[str, str], bytes | None], tuple[int, bytes, dict[str, str]]
]

_DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/interactions"
_DEFAULT_MODEL = "lyria-3.5"
_DEFAULT_TIMEOUT_SECONDS = 300
_LYRIA_SAMPLE_RATE = 44100
_LYRIA_CHANNELS = 2
_SECRET_MARKERS = ("GEMINI_API_KEY", "x-goog-api-key", "Authorization")


class LyriaTransportError(Exception):
    """Provider-neutral transport failure with a public, sanitized message."""

    def __init__(self, kind: str, message: str) -> None:
        self.kind = kind
        self.message = message
        super().__init__(message)


class GoogleLyriaAdapter:
    """Generate Lyria audio without exposing Google provider details."""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        http_call: HttpCall | None = None,
        prompt_builder: LyriaPromptBuilder | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        self.base_url = (
            base_url or os.getenv("LYRIA_BASE_URL") or _DEFAULT_BASE_URL
        ).rstrip("/")
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")
        self.model = model or os.getenv("LYRIA_MODEL") or _DEFAULT_MODEL
        self._http_call = http_call or self._default_http_call
        self._prompt_builder = prompt_builder or LyriaPromptBuilder()
        self.timeout_seconds = (
            timeout_seconds
            if timeout_seconds is not None
            else _env_timeout(os.getenv("LYRIA_TIMEOUT"))
        )

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
        except LyriaTransportError as exc:
            return AudioRenderResult(status="failed", error=exc.message)
        except HTTPError as exc:
            return self._result_from_status(int(exc.code))
        except TimeoutError:
            return AudioRenderResult(
                status="failed", error="Google Lyria request timed out"
            )
        except OSError:
            return AudioRenderResult(
                status="failed", error="Google Lyria network request failed"
            )
        classified = self._result_from_status(status)
        if classified is not None:
            return classified
        try:
            return self._complete(body, Path(output_path))
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            return AudioRenderResult(status="failed", error=self._public_error(exc))

    def _payload(
        self,
        request: AudioRenderRequest,
        context: GenerationContext | None = None,
    ) -> bytes:
        return json.dumps(
            {
                "model": self.model,
                "input": self._prompt_builder.build(request, context),
                "response_format": {"type": "audio"},
            }
        ).encode()

    def _complete(self, body: bytes, output_path: Path) -> AudioRenderResult:
        data = json.loads(body)
        if not isinstance(data, dict):
            raise TypeError("Lyria returned an invalid response")
        audio_data = _audio_data(data)
        if not audio_data:
            raise ValueError("Lyria returned no audio data")
        try:
            audio = base64_decode(audio_data)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("Lyria returned invalid audio encoding") from exc
        if not audio:
            raise ValueError("Lyria returned an empty audio artifact")
        if not looks_like_mp3(audio):
            raise ValueError("Lyria returned an invalid audio artifact")
        written = write_artifact_atomically(output_path, audio)
        return AudioRenderResult(
            status="succeeded",
            task_id=str(data.get("id") or ""),
            artifact_path=str(written),
            sample_rate=_LYRIA_SAMPLE_RATE,
            channels=_LYRIA_CHANNELS,
            sha256=hashlib.sha256(audio).hexdigest(),
        )

    def _result_from_status(self, status: int) -> AudioRenderResult | None:
        if 200 <= status < 300:
            return None
        if status in {401, 403}:
            return AudioRenderResult(
                status="blocked",
                error="Google Lyria authentication was rejected",
            )
        if status == 429:
            return AudioRenderResult(
                status="blocked",
                error="Google Lyria quota or rate limit reached",
            )
        if status == 400:
            return AudioRenderResult(
                status="failed", error="Google Lyria request was invalid"
            )
        if status == 404:
            return AudioRenderResult(
                status="failed",
                error="Google Lyria endpoint or model was not found",
            )
        if status >= 500:
            return AudioRenderResult(
                status="failed", error="Google Lyria provider error"
            )
        return AudioRenderResult(
            status="failed",
            error=f"Google Lyria request failed (HTTP {status})",
        )

    def _public_error(self, exc: Exception) -> str:
        if isinstance(exc, json.JSONDecodeError):
            return "Google Lyria returned invalid JSON"
        if isinstance(exc, OSError):
            return "Failed to write audio artifact"
        message = str(exc) or "Google Lyria request failed"
        return self._redact(message)

    def _redact(self, message: str) -> str:
        redacted = message
        if self.api_key:
            redacted = redacted.replace(self.api_key, "[redacted]")
        for marker in _SECRET_MARKERS:
            redacted = redacted.replace(marker, "[redacted]")
        return redacted

    def _headers(self) -> dict[str, str]:
        return {
            "x-goog-api-key": self.api_key or "",
            "Content-Type": "application/json",
        }

    def _default_http_call(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes | None,
    ) -> tuple[int, bytes, dict[str, str]]:
        request = Request(url, data=body, headers=headers, method=method)
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                return (
                    int(response.status),
                    response.read(),
                    dict(response.headers.items()),
                )
        except HTTPError as exc:
            payload = exc.read() if exc.fp is not None else b""
            response_headers = (
                dict(exc.headers.items()) if exc.headers is not None else {}
            )
            return int(exc.code), payload, response_headers
        except TimeoutError as exc:
            raise LyriaTransportError(
                "timeout", "Google Lyria request timed out"
            ) from exc
        except URLError as exc:
            reason = exc.reason
            if isinstance(reason, TimeoutError) or _is_timeout(reason):
                raise LyriaTransportError(
                    "timeout", "Google Lyria request timed out"
                ) from exc
            raise LyriaTransportError(
                "network", "Google Lyria network request failed"
            ) from exc


def base64_decode(value: str) -> bytes:
    """Decode validated base64 audio without accepting empty padding-only input."""
    return base64.b64decode(value, validate=True)


def looks_like_mp3(audio: bytes) -> bool:
    """Accept ID3-tagged or MPEG-frame MP3 leading bytes."""
    if len(audio) < 3:
        return False
    if audio.startswith(b"ID3"):
        return True
    return audio[0] == 0xFF and (audio[1] & 0xE0) == 0xE0


def write_artifact_atomically(output_path: Path, audio: bytes) -> Path:
    """Write verified bytes to a temp file, then atomically replace the target."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{output_path.name}.", suffix=".part", dir=output_path.parent
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(audio)
            handle.flush()
            os.fsync(handle.fileno())
        if tmp_path.stat().st_size != len(audio):
            raise ValueError("Lyria returned an invalid audio artifact")
        os.replace(tmp_path, output_path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise
    if not output_path.is_file() or output_path.stat().st_size == 0:
        raise ValueError("Lyria returned an invalid audio artifact")
    return output_path


def _audio_data(data: dict[str, Any]) -> str:
    found = ""
    for step in data.get("steps") or []:
        if not isinstance(step, dict):
            continue
        step_type = step.get("type")
        if step_type not in {None, "", "model_output"}:
            continue
        audio = _audio_from_content(step.get("content"))
        if audio:
            found = audio
    if found:
        return found
    return _audio_from_output_audio(data.get("output_audio"))


def _audio_from_output_audio(value: Any) -> str:
    if isinstance(value, str) and value:
        return value
    if isinstance(value, dict):
        return _audio_from_block(value)
    return ""


def _audio_from_content(content: Any) -> str:
    if isinstance(content, dict):
        return _audio_from_block(content)
    if isinstance(content, list):
        found = ""
        for block in content:
            if not isinstance(block, dict):
                continue
            audio = _audio_from_block(block)
            if audio:
                found = audio
        return found
    return ""


def _audio_from_block(block: dict[str, Any]) -> str:
    if block.get("type") not in {None, "", "audio"}:
        return ""
    data = block.get("data")
    if isinstance(data, str) and data:
        return data
    if isinstance(data, dict) and isinstance(data.get("data"), str):
        return data["data"]
    return ""


def _env_timeout(raw: str | None) -> float:
    if raw is None or not raw.strip():
        return _DEFAULT_TIMEOUT_SECONDS
    try:
        value = float(raw)
    except ValueError:
        return _DEFAULT_TIMEOUT_SECONDS
    return value if value > 0 else _DEFAULT_TIMEOUT_SECONDS


def _is_timeout(reason: Any) -> bool:
    return "timed out" in str(reason).lower()
