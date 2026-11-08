"""Bridge from the MW10 gateway's pitch-range reader to the composer.

The gateway (``mw10_gateway/`` in this repo) accumulates the player's own
pitch distribution in Obsidian session notes; its ``pitch_range`` module
derives the playing range from that distribution. This loader imports that
module by file location — the gateway stays a standalone service and is
never imported as part of the ``kihachi_mcp`` package — and returns the
range only when it came from real note data. Any problem (gateway files
missing, no session notes, unparsable content, the reader's built-in
default) yields None, so generation behaves exactly as before.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from types import ModuleType

_MODULE: ModuleType | None = None


def _load_pitch_range_module() -> ModuleType | None:
    global _MODULE
    if _MODULE is not None:
        return _MODULE
    candidate = Path(__file__).resolve().parents[3] / "mw10_gateway" / "pitch_range.py"
    if not candidate.is_file():
        return None
    spec = importlib.util.spec_from_file_location("mw10_gateway_pitch_range", candidate)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    # Register before exec: the gateway module uses dataclasses, which
    # resolve their module through sys.modules during class creation.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    _MODULE = module
    return module


def pitch_range_from_vault(vault_path: str | Path) -> tuple[int, int] | None:
    """(low, high) from the vault's latest session note, or None.

    None means "no usable data": the caller must leave the constraints
    untouched rather than clamp generation to a default range.
    """
    module = _load_pitch_range_module()
    if module is None:
        return None
    try:
        result = module.extract_pitch_range(Path(vault_path))
    except Exception:  # noqa: BLE001 - fail-soft by design: any reader
        # problem means "no range", never a broken generation request
        return None
    if result is None or getattr(result, "source", "default") == "default":
        return None
    low, high = int(result.min_pitch), int(result.max_pitch)
    if not 0 <= low <= high <= 127:
        return None
    return (low, high)


def pitch_range_from_env() -> tuple[int, int] | None:
    """Range from the vault named by OBSIDIAN_VAULT_PATH, when configured.

    The variable is the gateway's own configuration, so the composer only
    picks the range up in deployments where the gateway is in use.
    """
    vault = os.environ.get("OBSIDIAN_VAULT_PATH", "").strip()
    if not vault:
        return None
    return pitch_range_from_vault(vault)
