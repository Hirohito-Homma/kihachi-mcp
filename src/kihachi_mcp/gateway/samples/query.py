"""Search and the sample half of a dry-run. Nothing here calls Ableton."""

from __future__ import annotations

from typing import Any

from kihachi_mcp.gateway.samples.index import (
    find_sample,
    load_samples,
    resolve_inside_library,
    selections_for,
    write_manifest,
)
from kihachi_mcp.gateway.samples.meta import public_sample
from kihachi_mcp.gateway.samples.rank import score_sample


def search_indexed(
    *,
    role: str | None = None,
    bpm: float | None = None,
    key: str | None = None,
    tags: list[str] | None = None,
    kind: str | None = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    requirement = {"role": role or "other", "type": kind, "bpm": bpm, "key": key, "tags": tags or []}
    ranked = _rank(requirement)
    if role:
        ranked = [row for row in ranked if row["role"] == role or row["match"]["role"] > 0]
    return [_card(row) for row in _unique(ranked, max(1, min(limit, 50)))]


def sample_plan(plan: dict[str, Any], selection: str) -> dict[str, Any]:
    """Rank the index and write a manifest that contains no file paths."""
    mode = selection if selection in {"manual", "assisted", "automatic"} else "assisted"
    requirements = plan.get("sample_requirements")
    if not isinstance(requirements, list):
        requirements = []
    groups = []
    for requirement in requirements:
        if not isinstance(requirement, dict):
            continue
        ranked = _rank(requirement)
        groups.append(
            {
                "role": requirement.get("role"),
                "requirement": requirement,
                "candidate_count": len(ranked),
                "candidates": [_card(row) for row in _unique(ranked, 5)],
            }
        )
    adopted = _adopted(plan, mode, groups)
    manifest = {
        "song": (plan.get("song") or {}).get("title"),
        "tempo": (plan.get("song") or {}).get("tempo"),
        "key": (plan.get("song") or {}).get("key"),
        "sample_selection": mode,
        "samples": [
            {
                "sample_id": row.get("sample_id"),
                "source": row.get("source"),
                "filename": row.get("filename"),
                "role": row.get("adopted_role") or row.get("role"),
                "original_bpm": row.get("bpm"),
                "original_key": row.get("key"),
                "placement": None,
            }
            for row in adopted
        ],
        "candidate_counts": {str(group["role"]): group["candidate_count"] for group in groups},
    }
    plan_id = plan.get("plan_id")
    if isinstance(plan_id, str) and plan_id:
        write_manifest(plan_id, manifest)
    return {
        "selection": mode,
        "requirements": requirements,
        "groups": groups,
        "adopted": adopted,
        "manifest": manifest,
        "live_changed": False,
    }


def _rank(requirement: dict[str, Any]) -> list[dict[str, Any]]:
    ranked: list[dict[str, Any]] = []
    for record in load_samples():
        if resolve_inside_library(record) is None:
            continue
        view = public_sample(record)
        view["match"] = score_sample(record, requirement)
        if float(view["match"]["role"]) <= 0:
            continue
        ranked.append(view)
    ranked.sort(key=lambda row: (-float(row["match"]["score"]), str(row.get("sample_id") or "")))
    return ranked


def _unique(rows: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    seen: set[str] = set()
    chosen: list[dict[str, Any]] = []
    for row in rows:
        name = str(row.get("filename") or row.get("sample_id") or "")
        if name in seen:
            continue
        seen.add(name)
        chosen.append(row)
        if len(chosen) >= limit:
            break
    return chosen


def _card(row: dict[str, Any]) -> dict[str, Any]:
    match = row.get("match") if isinstance(row.get("match"), dict) else {}
    return {
        "sample_id": row.get("sample_id"),
        "filename": row.get("filename"),
        "role": row.get("role"),
        "bpm": row.get("bpm"),
        "key": row.get("key"),
        "duration": row.get("duration_seconds"),
        "loop_or_oneshot": row.get("kind"),
        "score": match.get("score"),
        "match_reasons": match.get("match_reasons"),
        "source": row.get("source"),
    }


def _adopted(plan: dict[str, Any], mode: str, groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Assisted mode keeps human selections only. Rank 1 is never adopted by itself."""
    del groups
    if mode != "assisted":
        return []
    title = str((plan.get("song") or {}).get("title") or "")
    chosen = selections_for(title)
    adopted = []
    for role, sample_id in chosen.items():
        record = find_sample(sample_id)
        if record is None or resolve_inside_library(record) is None:
            continue
        view = public_sample(record)
        view["adopted_role"] = role
        adopted.append(view)
    return adopted
