"""Guarded live smoke for Google Lyria 3.5. Never prints secret values."""

from __future__ import annotations

import os
from pathlib import Path

from kihachi_mcp.models import Arrangement, AudioRenderRequest
from kihachi_mcp.services import GoogleLyriaAdapter

_REPO_ROOT = Path(__file__).resolve().parents[1]
_BLOCKED = "LIVE LYRIA TEST BLOCKED — GEMINI_API_KEY NOT CONFIGURED"


def _load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _has_key() -> bool:
    value = os.getenv("GEMINI_API_KEY", "").strip()
    return bool(value)


def main() -> int:
    _load_dotenv(_REPO_ROOT / ".env")
    if not _has_key():
        print(_BLOCKED)
        return 2
    output = _REPO_ROOT / "outputs" / "lyria-smoke.mp3"
    request = AudioRenderRequest(
        "Lyria Smoke",
        "dub techno",
        "Kick",
        110,
        "D#m",
        0.25,
        8,
        "Keep this a very short instrumental click-and-kick test. No vocals.",
        arrangement=(Arrangement("Intro", 1, 8),),
        mood="Deep",
        tracks=("Kick",),
    )
    result = GoogleLyriaAdapter().render(request, output)
    public = result.to_dict()
    secret = os.environ.get("GEMINI_API_KEY", "")
    leaked = bool(secret) and secret in str(public)
    print(f"status={result.status}")
    print(f"task_id={result.task_id}")
    print(f"artifact={result.artifact_path}")
    print(f"duration_seconds={result.duration_seconds}")
    print(f"sample_rate={result.sample_rate}")
    print(f"channels={result.channels}")
    print(f"sha256={result.sha256}")
    print(f"error={result.error}")
    print(f"file_exists={output.is_file()}")
    print(f"secret_absent={not leaked}")
    if result.status != "succeeded" or leaked or not output.is_file():
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
