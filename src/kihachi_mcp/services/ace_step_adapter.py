import hashlib
import json
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from kihachi_mcp.models.audio_plan import AudioRenderRequest, AudioRenderResult

HttpCall = Callable[
    [str, str, dict[str, str], bytes | None], tuple[int, bytes, dict[str, str]]
]


class AceStepAdapter:
    """Submit and collect ACE-Step tasks without exposing provider details."""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        http_call: HttpCall | None = None,
        poll_attempts: int = 10,
        poll_interval_seconds: float = 0,
    ) -> None:
        self.base_url = (
            base_url or os.getenv("ACESTEP_BASE_URL") or "http://127.0.0.1:8001"
        ).rstrip("/")
        self.api_key = api_key or os.getenv("ACESTEP_API_KEY")
        self.poll_attempts = max(1, poll_attempts)
        self.poll_interval_seconds = max(0, poll_interval_seconds)
        self._http_call = http_call or self._default_http_call

    def render(
        self, request: AudioRenderRequest, output_path: str | Path
    ) -> AudioRenderResult:
        """Run one task and return a verified local artifact receipt."""
        if not self.api_key:
            return AudioRenderResult(
                status="blocked", error="ACESTEP_API_KEY is not configured"
            )
        try:
            task_id = self._submit(request)
            result = self._poll(task_id)
            if result.get("status") not in {"succeeded", "completed", "success"}:
                return AudioRenderResult(
                    status="failed",
                    task_id=task_id,
                    error=str(result.get("error") or "ACE-Step task failed"),
                )
            artifact_url = str(
                result.get("audio_url") or result.get("output_url") or ""
            )
            if not artifact_url:
                return AudioRenderResult(
                    status="failed",
                    task_id=task_id,
                    error="ACE-Step returned no audio URL",
                )
            if not artifact_url.startswith(self.base_url + "/"):
                return AudioRenderResult(
                    status="failed",
                    task_id=task_id,
                    error="audio URL is outside ACE-Step base URL",
                )
            path = Path(output_path)
            status, body, _ = self._http_call("GET", artifact_url, {}, None)
            if status >= 400 or not body:
                return AudioRenderResult(
                    status="failed", task_id=task_id, error="audio download failed"
                )
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(body)
            digest = hashlib.sha256(body).hexdigest()
            return AudioRenderResult(
                status="succeeded",
                task_id=task_id,
                artifact_path=str(path),
                duration_seconds=float(result.get("duration_seconds") or 0),
                sample_rate=int(result.get("sample_rate") or 0),
                channels=int(result.get("channels") or 0),
                sha256=digest,
            )
        except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
            return AudioRenderResult(status="failed", error=str(exc))

    def _submit(self, request: AudioRenderRequest) -> str:
        payload = {
            "prompt": request.prompt,
            "negative_prompt": request.negative_prompt,
            "audio_duration": request.length_minutes * 60,
            "bpm": request.tempo,
            "key": request.key,
            "genre": request.genre,
            "target_track": request.target_track,
        }
        status, body, _ = self._http_call(
            "POST",
            f"{self.base_url}/release_task",
            self._headers(),
            json.dumps(payload).encode(),
        )
        if status >= 400:
            raise ValueError("ACE-Step task submission failed")
        data = json.loads(body)
        task_id = str(data.get("task_id") or data.get("id") or "")
        if not task_id:
            raise ValueError("ACE-Step returned no task id")
        return task_id

    def _poll(self, task_id: str) -> dict[str, Any]:
        for attempt in range(self.poll_attempts):
            status, body, _ = self._http_call(
                "POST",
                f"{self.base_url}/query_result",
                self._headers(),
                json.dumps({"task_id": task_id}).encode(),
            )
            if status >= 400:
                raise ValueError("ACE-Step result query failed")
            data = json.loads(body)
            if not isinstance(data, dict):
                raise TypeError("ACE-Step returned an invalid result")
            if data.get("status") not in {"pending", "queued", "running", "processing"}:
                return data
            if attempt + 1 < self.poll_attempts and self.poll_interval_seconds:
                time.sleep(self.poll_interval_seconds)
        return {"status": "failed", "error": "ACE-Step polling timed out"}

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
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
