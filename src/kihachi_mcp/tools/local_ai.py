"""JSON adapter for local creative brief previews."""

from typing import Any

from kihachi_mcp.services.local_ai import interpret_brief
from kihachi_mcp.services.studio_interpreter import (
    InferenceCancelled,
    InterpretationError,
)


def preview_local_ai_song(brief: str) -> dict[str, Any]:
    """Interpret Japanese instructions with local Ollama and preview MIDI; never edit Live."""
    try:
        return interpret_brief(brief)
    except (OSError, ValueError, KeyError, TypeError, InterpretationError, InferenceCancelled) as error:
        return {
            "ok": False,
            "provider": "local_ollama",
            "applied_to_live": False,
            "error": str(error),
        }
