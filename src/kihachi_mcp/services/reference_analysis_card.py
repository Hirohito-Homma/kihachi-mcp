"""Reference Music Analysis Card data model and storage.

Stores:
- Source audio metadata and user title
- Segment-level machine measurements with explicit confidence & methods
- Musically grounded theoretical interpretations
- Concrete generative rules for candidate generation (no note cloning)
- User feedback, human evaluations, manual overrides, and adoption decisions
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger(__name__)


class AnalysisCard:
    """An immutable-by-default, versioned Reference Analysis Card."""

    def __init__(
        self,
        card_id: str,
        user_title: str,
        audio_filename: str,
        segment: dict[str, Any],
        bpm: dict[str, Any],
        kick_pattern: dict[str, Any],
        bass_groove: dict[str, Any],
        energy_dynamics: dict[str, Any],
        section: dict[str, Any],
        harmonic_analysis: dict[str, Any],
        theory_interpretation: dict[str, Any],
        generation_rules: dict[str, Any],
        user_feedback: dict[str, Any] | None = None,
        gemini_status: str = "skipped_not_requested",
        analysis_mode: str = "local_only",
        gemini_diagnostic: str = "",
        schema_version: str = "2.0.0",
        analysis_scope: str = "full_track",
        execution_stats: dict[str, Any] | None = None,
        sections: list[dict[str, Any]] | None = None,
        harmonic_timeline: list[dict[str, Any]] | None = None,
        chord_timeline: list[dict[str, Any]] | None = None,
        timeline: list[dict[str, Any]] | None = None,
        theory_timeline: list[dict[str, Any]] | None = None,
        created_at: str | None = None,
        updated_at: str | None = None,
    ) -> None:
        self.card_id = card_id
        self.user_title = user_title
        self.audio_filename = audio_filename
        self.segment = segment
        self.bpm = bpm
        self.kick_pattern = kick_pattern
        self.bass_groove = bass_groove
        self.energy_dynamics = energy_dynamics
        self.section = section
        self.harmonic_analysis = harmonic_analysis
        self.theory_interpretation = theory_interpretation
        self.generation_rules = generation_rules
        self.gemini_status = gemini_status
        self.analysis_mode = analysis_mode
        self.gemini_diagnostic = gemini_diagnostic

        # Schema v2.0 fields with backward compatibility
        self.schema_version = schema_version or "2.0.0"
        self.analysis_scope = analysis_scope or "full_track"
        self.execution_stats = execution_stats or {
            "reached_end": True,
            "elapsed_seconds": 0.0,
            "analyzed_duration_seconds": float(segment.get("duration_seconds", 16.0)),
            "total_duration_seconds": float(segment.get("duration_seconds", 16.0)),
            "chunk_count": 1,
            "scope": self.analysis_scope,
        }
        self.sections = sections or []
        self.harmonic_timeline = harmonic_timeline or []
        self.chord_timeline = chord_timeline or []
        self.timeline = timeline or []
        self.theory_timeline = theory_timeline or []

        self.user_feedback = user_feedback or {
            "human_rating": "UNRATED",
            "evaluated_by_human": False,
            "adopted": False,
            "rejection_reason": "",
            "notes": "",
            "manual_overrides": {},
        }
        self.created_at = created_at or datetime.now(UTC).isoformat()
        self.updated_at = updated_at or self.created_at

    @classmethod
    def from_analysis(
        cls,
        analysis_result: dict[str, Any],
        user_title: str,
        audio_filename: str,
        card_id: str | None = None,
    ) -> AnalysisCard:
        cid = card_id or f"card_{uuid.uuid4().hex[:12]}"
        return cls(
            card_id=cid,
            user_title=user_title or f"Analysis of {audio_filename}",
            audio_filename=audio_filename,
            segment=analysis_result.get("segment", {}),
            bpm=analysis_result.get("bpm", {}),
            kick_pattern=analysis_result.get("kick_pattern", {}),
            bass_groove=analysis_result.get("bass_groove", {}),
            energy_dynamics=analysis_result.get("energy_dynamics", {}),
            section=analysis_result.get("section", {}),
            harmonic_analysis=analysis_result.get("harmonic_analysis", {}),
            theory_interpretation=analysis_result.get("theory_interpretation", {}),
            generation_rules=analysis_result.get("generation_rules", {}),
            gemini_status=analysis_result.get("gemini_status", "skipped_not_requested"),
            analysis_mode=analysis_result.get("analysis_mode", "local_only"),
            gemini_diagnostic=analysis_result.get("gemini_diagnostic", ""),
            schema_version=analysis_result.get("schema_version", "2.0.0"),
            analysis_scope=analysis_result.get("analysis_scope", "full_track"),
            execution_stats=analysis_result.get("execution_stats"),
            sections=analysis_result.get("sections", []),
            harmonic_timeline=analysis_result.get("harmonic_timeline", []),
            chord_timeline=analysis_result.get("chord_timeline", []),
            timeline=analysis_result.get("timeline", []),
            theory_timeline=analysis_result.get("theory_timeline", []),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "card_id": self.card_id,
            "schema_version": self.schema_version,
            "analysis_scope": self.analysis_scope,
            "user_title": self.user_title,
            "audio_filename": self.audio_filename,
            "execution_stats": self.execution_stats,
            "segment": self.segment,
            "bpm": self.bpm,
            "kick_pattern": self.kick_pattern,
            "bass_groove": self.bass_groove,
            "energy_dynamics": self.energy_dynamics,
            "section": self.section,
            "sections": self.sections,
            "harmonic_analysis": self.harmonic_analysis,
            "harmonic_timeline": self.harmonic_timeline,
            "chord_timeline": self.chord_timeline,
            "timeline": self.timeline,
            "theory_interpretation": self.theory_interpretation,
            "theory_timeline": self.theory_timeline,
            "generation_rules": self.generation_rules,
            "gemini_status": self.gemini_status,
            "analysis_mode": self.analysis_mode,
            "gemini_diagnostic": self.gemini_diagnostic,
            "user_feedback": self.user_feedback,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AnalysisCard:
        schema_version = data.get("schema_version", "1.0.0")
        analysis_scope = data.get("analysis_scope", "segment" if schema_version == "1.0.0" else "full_track")
        segment = data.get("segment", {})

        fallback_execution_stats = {
            "reached_end": True,
            "elapsed_seconds": 0.0,
            "analyzed_duration_seconds": float(segment.get("duration_seconds", 16.0)),
            "total_duration_seconds": float(segment.get("duration_seconds", 16.0)),
            "chunk_count": 1,
            "scope": analysis_scope,
        }

        return cls(
            card_id=data["card_id"],
            schema_version=schema_version,
            analysis_scope=analysis_scope,
            user_title=data.get("user_title", ""),
            audio_filename=data.get("audio_filename", ""),
            execution_stats=data.get("execution_stats", fallback_execution_stats),
            segment=segment,
            bpm=data.get("bpm", {}),
            kick_pattern=data.get("kick_pattern", {}),
            bass_groove=data.get("bass_groove", {}),
            energy_dynamics=data.get("energy_dynamics", {}),
            section=data.get("section", {}),
            sections=data.get("sections", []),
            harmonic_analysis=data.get("harmonic_analysis", {}),
            harmonic_timeline=data.get("harmonic_timeline", []),
            chord_timeline=data.get("chord_timeline", []),
            timeline=data.get("timeline", []),
            theory_interpretation=data.get("theory_interpretation", {}),
            theory_timeline=data.get("theory_timeline", []),
            generation_rules=data.get("generation_rules", {}),
            user_feedback=data.get("user_feedback"),
            gemini_status=data.get("gemini_status", "skipped_not_requested"),
            analysis_mode=data.get("analysis_mode", "local_only"),
            gemini_diagnostic=data.get("gemini_diagnostic", ""),
            created_at=data.get("created_at"),
            updated_at=data.get("updated_at"),
        )

    def apply_user_feedback(
        self,
        rating: str | None = None,
        notes: str | None = None,
        adopted: bool | None = None,
        rejection_reason: str | None = None,
        manual_overrides: dict[str, Any] | None = None,
    ) -> None:
        """Update card with human audition evaluations and adjustments."""
        if rating is not None:
            self.user_feedback["human_rating"] = str(rating)
            self.user_feedback["evaluated_by_human"] = True
        if notes is not None:
            self.user_feedback["notes"] = str(notes)
        if adopted is not None:
            self.user_feedback["adopted"] = bool(adopted)
        if rejection_reason is not None:
            self.user_feedback["rejection_reason"] = str(rejection_reason)
        if manual_overrides is not None:
            self.user_feedback.setdefault("manual_overrides", {}).update(manual_overrides)
            # Apply overrides to generation rules if applicable
            for k, v in manual_overrides.items():
                if k in self.generation_rules:
                    self.generation_rules[k] = v

        self.updated_at = datetime.now(UTC).isoformat()

    def apply_gemini_enhancement(self, gemini_result: dict[str, Any]) -> None:
        """Update theory interpretation and generation rules from Gemini."""
        if gemini_result.get("ok"):
            if "theory_interpretation" in gemini_result:
                self.theory_interpretation = dict(gemini_result["theory_interpretation"])
                self.theory_interpretation["enhanced_by"] = gemini_result.get("enhanced_by", "gemini")
            if "theory_timeline" in gemini_result and gemini_result["theory_timeline"]:
                self.theory_timeline = list(gemini_result["theory_timeline"])
            if "generation_rules" in gemini_result:
                self.generation_rules.update(gemini_result["generation_rules"])
            self.gemini_status = "success"
            self.analysis_mode = gemini_result.get("analysis_mode", "gemini_mode_a")
            self.gemini_diagnostic = ""
        else:
            self.gemini_status = "failed_fallback"
            self.gemini_diagnostic = str(gemini_result.get("error", "Gemini解析失敗"))
        self.updated_at = datetime.now(UTC).isoformat()

    update_feedback = apply_user_feedback


class AnalysisCardStore:
    """Manages disk persistence and query of Analysis Cards."""

    def __init__(self, storage_dir: Path) -> None:
        self.storage_dir = storage_dir / "analysis_cards"
        self.storage_dir.mkdir(parents=True, exist_ok=True)

    def save(self, card: AnalysisCard) -> Path:
        target = self.storage_dir / f"{card.card_id}.json"
        temp = target.with_suffix(".tmp")
        payload = card.to_dict()
        temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(target)
        return target

    def get(self, card_id: str) -> AnalysisCard | None:
        target = self.storage_dir / f"{card_id}.json"
        if not target.is_file():
            return None
        try:
            data = json.loads(target.read_text(encoding="utf-8"))
            return AnalysisCard.from_dict(data)
        except (OSError, json.JSONDecodeError, KeyError, ValueError) as exc:
            LOGGER.warning("Failed to load analysis card %s: %s", card_id, exc)
            return None

    def list_cards(self, limit: int = 50) -> list[dict[str, Any]]:
        cards = []
        for p in sorted(self.storage_dir.glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                exec_stats = data.get("execution_stats", {})
                cards.append({
                    "card_id": data.get("card_id"),
                    "schema_version": data.get("schema_version", "1.0.0"),
                    "analysis_scope": data.get("analysis_scope", "segment"),
                    "user_title": data.get("user_title"),
                    "audio_filename": data.get("audio_filename"),
                    "bpm": data.get("bpm", {}).get("value"),
                    "bpm_confidence": data.get("bpm", {}).get("confidence"),
                    "evaluated": data.get("user_feedback", {}).get("evaluated_by_human", False),
                    "human_rating": data.get("user_feedback", {}).get("human_rating", "UNRATED"),
                    "gemini_status": data.get("gemini_status", "skipped_not_requested"),
                    "analysis_mode": data.get("analysis_mode", "local_only"),
                    "elapsed_seconds": exec_stats.get("elapsed_seconds"),
                    "reached_end": exec_stats.get("reached_end", True),
                    "total_duration_seconds": exec_stats.get("total_duration_seconds", data.get("segment", {}).get("duration_seconds")),
                    "created_at": data.get("created_at"),
                })
            except (OSError, json.JSONDecodeError, KeyError, ValueError) as exc:
                LOGGER.debug("Skipping unreadable card %s: %s", p, exc)
                continue
            if len(cards) >= limit:
                break
        return cards
