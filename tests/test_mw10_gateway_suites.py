"""Run the MW10 gateway's script-style suites under pytest.

The gateway suites in mw10_gateway/tests/ are standalone scripts (they run
their checks at module level and exit non-zero on failure). This shim runs
each one in a subprocess so CI exercises them exactly as written.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SUITES = [
    "test_automation_log.py",
    "test_midi_log.py",
    "test_midi_distribution.py",
    "test_pitch_range.py",
    "test_music_pipeline.py",
]


@pytest.mark.parametrize("suite", SUITES)
def test_mw10_gateway_suite(suite: str) -> None:
    script = REPO_ROOT / "mw10_gateway" / "tests" / suite
    env = dict(os.environ)
    pythonpath = [str(REPO_ROOT / "mw10_gateway"), str(REPO_ROOT / "src")]
    if env.get("PYTHONPATH"):
        pythonpath.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(pythonpath)
    result = subprocess.run(
        [sys.executable, str(script)],
        check=False,
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )
    assert result.returncode == 0, (
        f"{suite} failed:\n{result.stdout}\n{result.stderr}"
    )
    assert "ALL PASS" in result.stdout, f"{suite} did not report ALL PASS"
