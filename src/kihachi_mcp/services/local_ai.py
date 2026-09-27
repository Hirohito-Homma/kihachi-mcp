"""Local-only brief interpretation and reviewable MIDI previews."""

from typing import Any

from kihachi_mcp.services import studio_interpreter
from kihachi_mcp.services.brief_parser import extract_explicit
from kihachi_mcp.services.midi_candidate_builder import (
    build_candidate,
    hat_counts_by_half,
)
from kihachi_mcp.services.studio_interpreter import (
    InterpretationError,
    assemble_brief,
    validate_model_intent,
)


def validate_intent(value: Any) -> dict[str, Any]:
    """Reject malformed model output. Kept for the existing MCP test surface."""
    return validate_model_intent(value)


def create_preview(brief: str, intent: dict[str, Any]) -> dict[str, Any]:
    """Build a candidate from already-validated model output. Does not touch Live."""
    extracted = extract_explicit(brief)
    production = assemble_brief(extracted, intent)
    return _preview_from_production(production)


def interpret_brief(brief: str) -> dict[str, Any]:
    """Use the installed model only; no downloads, redirects or cloud fallback."""
    production = studio_interpreter.interpret_brief(brief)
    return _preview_from_production(production)


def _preview_from_production(production: Any) -> dict[str, Any]:
    candidate = build_candidate(production)
    first_hats, second_hats = hat_counts_by_half(candidate)
    return {
        "ok": True,
        "provider": "local_ollama",
        "model": production.model,
        "brief": production.original_text,
        "intent": {
            "genre": production.genre.value,
            "tempo": production.tempo.value,
            "bars": production.bars.value,
            "key": production.key.value,
            "mood": production.mood.value,
            "hats_first_half": production.hats_first_half.value,
            "hats_second_half": production.hats_second_half.value,
            "drop_start_bar": production.drop_start_bar.value,
            "unhandled": list(production.unhandled),
        },
        "songspec": {
            "genre": production.genre.value,
            "tempo": production.tempo.value,
            "key": production.key.value,
            "bars": production.bars.value,
            "length_minutes": production.duration_minutes,
            "tracks": ["Kick", "Hats", "Bass", "Stab"],
        },
        "candidate": candidate.to_dict(),
        "midi_preview": [clip.to_dict() for clip in candidate.clips],
        "hat_counts": {"first_half": first_hats, "second_half": second_hats},
        "applied_to_live": False,
        "musical_quality_claimed": False,
        "limitations": [
            "形式検査と指示反映の確認であり、曲の良し悪しは評価していません",
            "完成音声・歌声・自動マスタリングは対象外です",
        ],
    }


__all__ = [
    "InterpretationError",
    "create_preview",
    "interpret_brief",
    "validate_intent",
]
