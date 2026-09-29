"""MCP entry point for safe, offline role-scoped MIDI composition."""

from typing import Any

from kihachi_mcp.models.live_contract import canonical_hash
from kihachi_mcp.models.midi_composer import (
    ComposedMidiNote,
    MidiCompositionRequest,
    MusicalConstraints,
)
from kihachi_mcp.services.midi_composer import DeterministicMIDIComposer


def compose_midi_part(
    constraints: dict[str, Any],
    operation: str = "generate",
    source_notes: list[dict[str, Any]] | None = None,
    seed: int = 0,
) -> dict[str, Any]:
    """Compose one MIDI role from explicit constraints without contacting a paid API."""
    try:
        request = MidiCompositionRequest(
            constraints=MusicalConstraints.from_dict(constraints),
            operation=operation,
            source_notes=tuple(
                ComposedMidiNote.from_dict(note) for note in source_notes or []
            ),
        )
        notes = DeterministicMIDIComposer(seed=seed).compose(request)
    except (TypeError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}
    payload = [note.to_dict() for note in notes]
    return {
        "ok": True,
        "provider": "deterministic",
        "operation": operation,
        "constraints": request.constraints.to_dict(),
        "notes": payload,
        "note_count": len(payload),
        "note_fingerprint": canonical_hash(payload),
        "paid_api": False,
        "applied_to_live": False,
    }
