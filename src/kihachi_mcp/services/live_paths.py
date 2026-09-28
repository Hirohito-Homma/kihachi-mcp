"""Platform-specific runtime paths for the Live bridge.

Ableton Live runs on macOS and Windows, and the two disagree about where
per-user runtime state belongs. Every function takes the platform and
environment explicitly so the difference is testable on one machine.

Set path normalization lives in :mod:`kihachi_mcp.models.live_paths` because it
feeds the Set fingerprint.
"""

import os
import platform
from collections.abc import Mapping
from pathlib import PurePath, PureWindowsPath

SYSTEM_DARWIN = "Darwin"
SYSTEM_WINDOWS = "Windows"

APP_DIR_NAME = "KIHACHI"
BRIDGE_HANDSHAKE_FILENAME = "live-bridge.json"
CANDIDATE_DIR_NAME = "candidates"


def current_system() -> str:
    """Return the running platform name."""
    return platform.system()


def bridge_state_dir(
    system: str | None = None, environ: Mapping[str, str] | None = None
) -> PurePath:
    """Return the directory holding the bridge handshake file.

    macOS uses Application Support, Windows uses LOCALAPPDATA, and anything
    else (including CI containers) uses an XDG state directory. The handshake
    file and saved Studio candidates live here, and neither is committed.
    """
    system = system or current_system()
    env = environ if environ is not None else os.environ
    override = env.get("KIHACHI_LIVE_STATE_DIR")
    if override:
        return PurePath(override)
    if system == SYSTEM_DARWIN:
        return PurePath(
            env.get("HOME", "~"), "Library", "Application Support", APP_DIR_NAME
        )
    if system == SYSTEM_WINDOWS:
        base = env.get("LOCALAPPDATA") or env.get("APPDATA") or "C:\\"
        return PureWindowsPath(base, APP_DIR_NAME)
    base = env.get("XDG_STATE_HOME") or f"{env.get('HOME', '~')}/.local/state"
    return PurePath(base, APP_DIR_NAME.lower())


def bridge_handshake_path(
    system: str | None = None, environ: Mapping[str, str] | None = None
) -> PurePath:
    """Return the full path of the bridge handshake file."""
    return bridge_state_dir(system, environ) / BRIDGE_HANDSHAKE_FILENAME


def candidate_store_dir(
    system: str | None = None, environ: Mapping[str, str] | None = None
) -> PurePath:
    """Return the directory where Studio keeps candidates across restarts."""
    env = environ if environ is not None else os.environ
    override = env.get("KIHACHI_PROJECT_DIR")
    if override:
        return PurePath(override)
    return bridge_state_dir(system, environ) / CANDIDATE_DIR_NAME
