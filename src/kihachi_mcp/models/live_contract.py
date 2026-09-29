"""Shared constants, errors, and hashing for the Ableton Live automation contract.

Every model in the Live boundary carries ``schema_version``. A payload produced
by a different schema version is rejected instead of being coerced, because a
silently mis-parsed mutation plan could touch a user-owned part of a Live Set.
"""

import hashlib
import json
from typing import Any

SCHEMA_VERSION = 1

STATUS_READY = "ready"
STATUS_APPROVAL_REQUIRED = "approval_required"
STATUS_BLOCKED = "blocked"
STATUS_EXECUTING = "executing"
STATUS_PARTIALLY_APPLIED = "partially_applied"
STATUS_VERIFICATION_FAILED = "verification_failed"
STATUS_VERIFIED = "verified"
STATUS_UNAVAILABLE = "unavailable"

EXECUTION_STATUSES = frozenset(
    {
        STATUS_READY,
        STATUS_APPROVAL_REQUIRED,
        STATUS_BLOCKED,
        STATUS_EXECUTING,
        STATUS_PARTIALLY_APPLIED,
        STATUS_VERIFICATION_FAILED,
        STATUS_VERIFIED,
        STATUS_UNAVAILABLE,
    }
)

OP_SET_TEMPO = "set_tempo"
OP_CREATE_MIDI_TRACK = "create_midi_track"
OP_CREATE_AUDIO_TRACK = "create_audio_track"
OP_SET_TRACK_NAME = "set_track_name"
OP_SET_TRACK_COLOR = "set_track_color"
OP_CREATE_SCENE = "create_scene"
OP_CREATE_SESSION_CLIP = "create_session_clip"
OP_REPLACE_CLIP_NOTES = "replace_clip_notes"
OP_LOAD_LIVE_DEVICE = "load_live_device"
OP_LOAD_DRUM_PAD_SAMPLE = "load_drum_pad_sample"
OP_REPLACE_DRUM_PAD_SAMPLE = "replace_drum_pad_sample"
OP_SET_DEVICE_PARAMETER = "set_device_parameter"
OP_SET_TRACK_MIXER = "set_track_mixer"
OP_SET_SIDECHAIN_SOURCE = "set_sidechain_source"
OP_CREATE_LOCATOR = "create_locator"
OP_PLACE_ARRANGEMENT_CLIP = "place_arrangement_clip"
OP_DELETE_LOCATOR = "delete_locator"
OP_DELETE_ARRANGEMENT_CLIP = "delete_arrangement_clip"

SUPPORTED_OPS = frozenset(
    {
        OP_SET_TEMPO,
        OP_CREATE_MIDI_TRACK,
        OP_CREATE_AUDIO_TRACK,
        OP_SET_TRACK_NAME,
        OP_SET_TRACK_COLOR,
        OP_CREATE_SCENE,
        OP_CREATE_SESSION_CLIP,
        OP_REPLACE_CLIP_NOTES,
        OP_LOAD_LIVE_DEVICE,
        OP_LOAD_DRUM_PAD_SAMPLE,
        OP_REPLACE_DRUM_PAD_SAMPLE,
        OP_SET_DEVICE_PARAMETER,
        OP_SET_TRACK_MIXER,
        OP_SET_SIDECHAIN_SOURCE,
        OP_CREATE_LOCATOR,
        OP_PLACE_ARRANGEMENT_CLIP,
        OP_DELETE_LOCATOR,
        OP_DELETE_ARRANGEMENT_CLIP,
    }
)

STRUCTURAL_OPS = frozenset(
    {
        OP_CREATE_MIDI_TRACK,
        OP_CREATE_AUDIO_TRACK,
        OP_CREATE_SCENE,
        OP_CREATE_SESSION_CLIP,
        OP_LOAD_LIVE_DEVICE,
        OP_LOAD_DRUM_PAD_SAMPLE,
        OP_REPLACE_DRUM_PAD_SAMPLE,
        OP_PLACE_ARRANGEMENT_CLIP,
        OP_DELETE_LOCATOR,
        OP_DELETE_ARRANGEMENT_CLIP,
    }
)

MANAGED_MARKER = "[KIHACHI]"
MANAGED_CLIP_PREFIX = "K:"


class LiveContractError(Exception):
    """Base error for malformed Live boundary payloads."""


class SchemaVersionError(LiveContractError):
    """Raised when a payload declares an unsupported schema version."""


def require_schema_version(data: dict[str, Any], model: str) -> None:
    """Reject payloads that do not declare the supported schema version."""
    raw = data.get("schema_version")
    if raw is None:
        raise SchemaVersionError(f"{model} requires schema_version {SCHEMA_VERSION}")
    try:
        version = int(raw)
    except (TypeError, ValueError) as exc:
        raise SchemaVersionError(
            f"{model} schema_version must be an integer"
        ) from exc
    if version != SCHEMA_VERSION:
        raise SchemaVersionError(
            f"{model} schema_version {version} is not supported; "
            f"expected {SCHEMA_VERSION}"
        )


def canonical_json(value: Any) -> str:
    """Serialize a JSON-safe value so equal structures hash identically."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def canonical_hash(value: Any) -> str:
    """Return a stable sha256 over a JSON-safe value."""
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def is_managed_name(name: str) -> bool:
    """Report whether a Live element name carries a KIHACHI ownership marker."""
    return MANAGED_MARKER in name or f"[{MANAGED_CLIP_PREFIX}" in name


def managed_track_name(name: str) -> str:
    """Return the KIHACHI-owned form of a track name."""
    return name if is_managed_name(name) else f"{name} {MANAGED_MARKER}"


def managed_clip_name(section: str, managed_id: str) -> str:
    """Return the KIHACHI-owned form of a clip name."""
    return f"{section} [{MANAGED_CLIP_PREFIX}{managed_id}]"
