"""Deterministic sample ranking. No model and no guessed key or tempo."""

from __future__ import annotations

from typing import Any

from kihachi_mcp.gateway.samples.meta import DRUM_ROLES, TONAL_ROLES

_RELATED = {
    "percussion": frozenset({"percussion", "drum_loop", "top_loop", "shaker"}),
    "drum_loop": frozenset({"drum_loop", "percussion", "top_loop"}),
    "fx": frozenset({"fx", "impact", "riser", "downlifter", "texture"}),
    "impact": frozenset({"impact", "fx"}),
    "vocal": frozenset({"vocal", "vocal_chop"}),
    "vocal_chop": frozenset({"vocal_chop", "vocal"}),
    "texture": frozenset({"texture", "dub", "fx"}),
    "dub": frozenset({"dub", "texture"}),
}
_RELATIVE = {
    "C minor": "D# major",
    "C# minor": "E major",
    "D minor": "F major",
    "D# minor": "F# major",
    "E minor": "G major",
    "F minor": "G# major",
    "F# minor": "A major",
    "G minor": "A# major",
    "G# minor": "B major",
    "A minor": "C major",
    "A# minor": "C# major",
    "B minor": "D major",
}
_FIFTH = {
    "D# minor": "A# minor",
    "A# minor": "F minor",
}


def sample_requirements(tempo: int, key: str) -> list[dict[str, Any]]:
    """Candidates for Mutant Dub Circuit. Nothing here is selected or placed."""
    return [
        {
            "role": "kick",
            "track": "KICK",
            "type": "oneshot",
            "bpm": None,
            "key": None,
            "tags": ["tech-house", "punchy", "deep"],
        },
        {
            "role": "drums",
            "track": "DRUMS",
            "accept_roles": ["snare", "clap", "closed_hat", "open_hat"],
            "exclude_roles": ["kick"],
            "type": "oneshot",
            "bpm": None,
            "key": None,
            "tags": ["snare", "clap", "hat"],
        },
        {
            "role": "percussion",
            "track": "PERCUSSION",
            "type": "loop",
            "bpm": tempo,
            "key": None,
            "tags": ["funky", "syncopated", "organic"],
        },
        {
            "role": "vocal",
            "track": "VOCAL",
            "accept_roles": ["vocal", "vocal_chop"],
            "type": "oneshot",
            "bpm": None,
            "key": key,
            "tags": ["processed", "futuristic"],
        },
        {
            "role": "fx",
            "track": "FX",
            "accept_roles": ["fx", "impact", "riser", "texture", "dub"],
            "type": "oneshot",
            "bpm": None,
            "key": None,
            "tags": ["impact", "riser", "dub", "texture"],
        },
    ]


def score_sample(record: dict[str, Any], requirement: dict[str, Any]) -> dict[str, Any]:
    """Score one indexed sample against one requirement. Unknown facts are not invented."""
    filename_meta = record.get("filename_metadata") or {}
    role = str(filename_meta.get("role") or "other")
    wanted = str(requirement.get("role") or "other")
    accept = {str(item) for item in requirement.get("accept_roles") or []}
    exclude = {str(item) for item in requirement.get("exclude_roles") or []}
    if role in exclude:
        role_score = 0.0
    elif accept:
        role_score = 1.0 if role in accept else 0.0
    elif role == wanted:
        role_score = 1.0
    elif role in _RELATED.get(wanted, frozenset()):
        role_score = 0.55
    else:
        role_score = 0.0
    bpm_score, bpm_gap = _bpm_score(filename_meta.get("bpm"), requirement.get("bpm"), filename_meta.get("kind"))
    drumish = wanted in DRUM_ROLES or bool(accept) and accept <= DRUM_ROLES
    tonal = wanted in TONAL_ROLES or bool(accept & TONAL_ROLES)
    key_weight = 0.0 if drumish else 0.25 if tonal else 0.1
    key_score = _key_score(filename_meta.get("key"), requirement.get("key"))
    kind_score = _kind_score(filename_meta.get("kind"), requirement.get("type"), record.get("duration_seconds"))
    tag_score = _tag_score(filename_meta.get("tags") or [], requirement.get("tags") or [])
    role_weight = 0.55 - key_weight
    total = (
        role_weight * role_score
        + 0.25 * bpm_score
        + key_weight * key_score
        + 0.1 * kind_score
        + 0.1 * tag_score
    )
    return {
        "score": round(total, 4),
        "role": round(role_score, 4),
        "bpm": round(bpm_score, 4),
        "bpm_gap": bpm_gap,
        "key": round(key_score, 4),
        "kind": round(kind_score, 4),
        "tags": round(tag_score, 4),
        "energy": None,
        "automatic": _automatic(total, filename_meta.get("kind"), bpm_gap),
        "match_reasons": _reasons(
            role=role,
            wanted=wanted,
            role_score=role_score,
            bpm_gap=bpm_gap,
            kind=filename_meta.get("kind"),
            key_weight=key_weight,
            key_score=key_score,
            actual_key=filename_meta.get("key"),
            tag_score=tag_score,
        ),
    }


def _bpm_score(bpm: object, target: object, kind: object) -> tuple[float, float | None]:
    if not isinstance(target, (int, float)) or isinstance(target, bool):
        return 0.5, None
    if not isinstance(bpm, int) or isinstance(bpm, bool):
        return 0.25, None
    gap = abs(float(bpm) - float(target))
    if kind == "loop" and gap > 20:
        return 0.0, gap
    if gap <= 3:
        return 1.0, gap
    if gap <= 8:
        return 0.7, gap
    if gap <= 16:
        return 0.35, gap
    return 0.0, gap


def _key_score(actual: object, wanted: object) -> float:
    if not isinstance(wanted, str) or not wanted:
        return 0.5
    if not isinstance(actual, str) or not actual:
        return 0.2
    if actual == wanted:
        return 1.0
    if _RELATIVE.get(wanted) == actual or _RELATIVE.get(actual) == wanted:
        return 0.8
    if _parallel(actual) == wanted or _parallel(wanted) == actual:
        return 0.5
    if _FIFTH.get(wanted) == actual:
        return 0.4
    return 0.0


def _parallel(key: str) -> str | None:
    parts = key.split()
    if len(parts) != 2:
        return None
    mode = "major" if parts[1] == "minor" else "minor" if parts[1] == "major" else None
    if mode is None:
        return None
    return f"{parts[0]} {mode}"


def _kind_score(kind: object, wanted: object, duration: object) -> float:
    if not isinstance(wanted, str) or wanted not in {"loop", "oneshot"}:
        return 0.5
    if kind == wanted:
        return 1.0
    if kind in {"loop", "oneshot"}:
        return 0.1
    if not isinstance(duration, (int, float)) or isinstance(duration, bool):
        return 0.4
    long_enough = float(duration) >= 1.5
    if wanted == "loop":
        return 0.6 if long_enough else 0.3
    return 0.6 if not long_enough else 0.3


def _tag_score(tags: list[str], wanted: list[str]) -> float:
    if not wanted:
        return 0.5
    found = {str(tag).lower() for tag in tags}
    hits = 0
    for tag in wanted:
        text = str(tag).lower().replace("-", " ")
        parts = [part for part in text.split() if part]
        if text in found or (parts and all(part in found for part in parts)):
            hits += 1
    return hits / len(wanted)


def _reasons(
    *,
    role: str,
    wanted: str,
    role_score: float,
    bpm_gap: float | None,
    kind: object,
    key_weight: float,
    key_score: float,
    actual_key: object,
    tag_score: float,
) -> list[str]:
    reasons = [f"role:{role}" if role == wanted else "related_role" if role_score > 0 else "role:miss"]
    if bpm_gap is None:
        reasons.append("bpm:unknown")
    elif kind == "loop" and bpm_gap > 20:
        reasons.append("bpm:far")
    else:
        reasons.append(f"bpm_gap:{bpm_gap:g}")
    if key_weight == 0:
        reasons.append("key:not_required")
    elif key_score == 1:
        reasons.append("key:exact")
    elif not isinstance(actual_key, str) or not actual_key:
        reasons.append("key:unknown")
    else:
        reasons.append("key:partial" if key_score > 0 else "key:clash")
    reasons.append(f"kind:{kind if isinstance(kind, str) else 'unknown'}")
    reasons.append(f"tags:{tag_score:g}")
    return reasons


def _automatic(total: float, kind: object, gap: float | None) -> bool:
    if total < 0.55:
        return False
    return not (kind == "loop" and gap is not None and gap > 20)
