"""Exercise a built Studio app offline without touching Live or saved user settings."""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen


def _port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _get(port: int, path: str) -> tuple[int, dict | bytes]:
    with urlopen(f"http://127.0.0.1:{port}{path}", timeout=30) as response:
        data = response.read()
        return response.status, json.loads(data) if data.startswith(b"{") else data


def _post(port: int, path: str, body: dict) -> tuple[int, dict | bytes]:
    request = Request(
        f"http://127.0.0.1:{port}{path}",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urlopen(request, timeout=30) as response:
        data = response.read()
        return response.status, json.loads(data) if data.startswith(b"{") else data


@contextmanager
def _running_app(binary: Path, port: int, environment: dict[str, str]):
    process = subprocess.Popen(
        [str(binary), "--no-browser", "--port", str(port)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=environment,
    )
    try:
        for _ in range(80):
            if process.poll() is not None:
                raise RuntimeError(f"App exited early: {process.stdout.read()}")
            try:
                _get(port, "/api/settings")
                break
            except URLError:
                time.sleep(0.25)
        else:
            raise RuntimeError("Studio did not become ready within 20 seconds")
        yield
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
        if process.stdout:
            process.stdout.close()


def smoke(app: Path, bars: int = 8) -> dict:
    binary = app / "Contents" / "MacOS" / "KIHACHI MUSIC AI"
    if not binary.is_file():
        raise FileNotFoundError(f"App executable is missing: {binary}")
    if not 8 <= bars <= 256:
        raise ValueError("bars must be between 8 and 256")
    port = _port()
    with tempfile.TemporaryDirectory(prefix="kihachi-app-smoke-") as work:
        root = Path(work)
        environment = os.environ.copy()
        environment.update(
            {
                "KIHACHI_LIVE_STATE_DIR": str(root / "state"),
                "KIHACHI_PROJECT_DIR": str(root / "candidates"),
                "KIHACHI_REFERENCE_DIR": str(root / "references"),
                "KIHACHI_AI_PROVIDER": "deterministic",
                "KIHACHI_IGNORE_SAVED_SETTINGS": "1",
            }
        )
        with _running_app(binary, port, environment):
            _, settings = _get(port, "/api/settings")
            if settings["settings"]["ai_provider"] != "deterministic":
                raise RuntimeError("The isolated app did not select deterministic generation")
            _, queued = _post(port, "/api/generate", {"brief": f"124 BPM、D# minor、{bars}小節", "seed": 42})
            if not queued.get("ok") or not queued.get("started"):
                raise RuntimeError(f"Generation did not start: {queued}")
            for _ in range(120):
                _, job = _get(port, "/api/job")
                if job["state"] in {"done", "error", "cancelled"}:
                    break
                time.sleep(0.25)
            else:
                raise RuntimeError("Generation did not finish within 30 seconds")
            if job["state"] != "done" or not job["candidate_id"]:
                raise RuntimeError(f"Generation failed: {job}")
            candidate_id = job["candidate_id"]
            saved = root / "candidates" / f"{candidate_id}.json"
            if not saved.is_file():
                raise RuntimeError("The candidate was not saved")
        with _running_app(binary, port, environment):
            status, project = _get(port, f"/api/project?candidate_id={candidate_id}")
            if status != 200 or not project.get("ok") or project["songspec"]["tempo"] != 124:
                raise RuntimeError(f"Saved candidate did not reload: {project}")
            sections = project["arrangement"]["sections"]
            position = 1
            for section in sections:
                if section["start_bar"] != position or section["end_bar"] != position + section["length_bars"] - 1:
                    raise RuntimeError(f"Arrangement has a missing or overlapping bar: {section}")
                position = section["end_bar"] + 1
            if position != bars + 1 or not any(section["notes"] for section in sections):
                raise RuntimeError("Arrangement does not fill the requested bars with notes")
            if sum(section["notes"] for section in sections) != project["note_count"]:
                raise RuntimeError("Section note totals do not match the candidate")
            status, preview = _get(port, f"/api/candidate/{candidate_id}/preview.wav")
            if status != 200 or not preview.startswith(b"RIFF"):
                raise RuntimeError("Preview WAV is unavailable after restart")
        brief = json.loads(saved.read_text(encoding="utf-8"))["brief"]
        if brief["bars"]["value"] != bars or brief["key"]["value"] != "D#m":
            raise RuntimeError("Explicit musical values changed during interpretation")
        return {"ok": True, "candidate_id": candidate_id, "tempo": 124, "key": "D#m", "bars": bars, "sections": len(sections), "notes": sum(section["notes"] for section in sections), "preview_bytes": len(preview)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("app", type=Path, help="Built KIHACHI MUSIC AI.app")
    parser.add_argument("--bars", type=int, default=8, help="Explicit song length, 8 to 256 bars")
    args = parser.parse_args()
    print(json.dumps(smoke(args.app, args.bars), ensure_ascii=False))


if __name__ == "__main__":
    main()
