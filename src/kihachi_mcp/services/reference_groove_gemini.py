"""Music theory interpretation and rule enhancement via Google Gemini API.

Consumes mechanical analysis (BPM, kick regularity, bass density, rests, syncopation)
and produces structured music theory explanations and generative guidance rules.
Maintains strict schema validation and falls back safely to offline heuristics.
"""

from __future__ import annotations

import io
import logging
from pathlib import Path
from typing import Any

from kihachi_mcp.services.ai_provider import GeminiAIProvider, InterpretationError

LOGGER = logging.getLogger(__name__)

# Strict JSON Schema for Gemini structured output
GEMINI_GROOVE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "theory_interpretation": {
            "type": "object",
            "properties": {
                "summary": {"type": "string"},
                "groove_structure": {"type": "string"},
                "kick_bass_relationship": {"type": "string"},
                "space_and_rests": {"type": "string"},
                "energy_and_arrangement": {"type": "string"},
            },
            "required": [
                "summary",
                "groove_structure",
                "kick_bass_relationship",
                "space_and_rests",
            ],
        },
        "generation_rules": {
            "type": "object",
            "properties": {
                "bpm_target": {"type": "number"},
                "kick_style": {"type": "string", "enum": ["four_on_the_floor", "syncopated", "broken_beat"]},
                "bass_syncopation": {"type": "number"},
                "bass_rest_ratio": {"type": "number"},
                "kick_bass_interlock": {"type": "boolean"},
                "recommended_key": {"type": "string"},
            },
            "required": [
                "bpm_target",
                "kick_style",
                "bass_syncopation",
                "bass_rest_ratio",
                "kick_bass_interlock",
            ],
        },
        "audio_confidence_notes": {
            "type": "object",
            "properties": {
                "rhythm_confidence": {"type": "number"},
                "harmonic_status": {"type": "string", "enum": ["unverified", "unknown"]},
                "explanation": {"type": "string"},
            },
            "required": ["harmonic_status", "explanation"],
        },
    },
    "required": ["theory_interpretation", "generation_rules", "audio_confidence_notes"],
}


def enhance_groove_with_gemini(
    mechanical_analysis: dict[str, Any],
    genre: str = "",
    title: str = "",
    provider: GeminiAIProvider | None = None,
) -> dict[str, Any]:
    """Enhance mechanical groove analysis with Gemini music theory interpretation and generation rules.

    Safe fallback: If Gemini fails (no API key, rate limit, network down, malformed reply),
    this function returns ok: False with safe diagnostic error without crashing.
    """
    ai = provider or GeminiAIProvider()

    # Pre-check credentials to fail fast and clearly
    health = ai.health()
    if not health.get("ok"):
        return {
            "ok": False,
            "error": health.get("message", "Gemini APIキーが設定されていません"),
            "fallback_used": True,
            "provider": "gemini",
        }

    # Format factual input prompt (Strictly objective mechanical facts, no hallucinated notes)
    prompt = _build_gemini_prompt(mechanical_analysis, genre=genre, title=title)

    try:
        raw_result = ai.generate_structured(prompt, GEMINI_GROOVE_SCHEMA)
    except InterpretationError as exc:
        LOGGER.warning("Gemini enhancement failed: %s", exc)
        return {
            "ok": False,
            "error": str(exc),
            "fallback_used": True,
            "provider": "gemini",
        }

    # Validate and clean up schema
    cleaned = _validate_and_sanitize_gemini_output(raw_result, mechanical_analysis)
    return {
        "ok": True,
        "enhanced_by": "gemini",
        "analysis_mode": "gemini_mode_a",
        "model": ai.model,
        "theory_interpretation": cleaned["theory_interpretation"],
        "generation_rules": cleaned["generation_rules"],
        "audio_confidence_notes": cleaned["audio_confidence_notes"],
    }


def analyze_groove_audio_with_gemini(
    audio_path: Path,
    start_sec: float = 0.0,
    duration_sec: float = 16.0,
    mechanical_analysis: dict[str, Any] | None = None,
    genre: str = "",
    title: str = "",
    provider: GeminiAIProvider | None = None,
) -> dict[str, Any]:
    """Mode B: Send sliced audio clip (8-16 bars) directly to Gemini Multimodal API.

    Explicit user confirmation required. Extracts music theory interpretations
    and generative rules directly from the audio recording.
    """
    ai = provider or GeminiAIProvider()

    health = ai.health()
    if not health.get("ok"):
        return {
            "ok": False,
            "error": health.get("message", "Gemini APIキーが設定されていません"),
            "fallback_used": True,
            "analysis_mode": "gemini_mode_b",
        }

    try:
        audio_bytes, mime_type = _slice_audio_bytes(audio_path, start_sec, duration_sec)
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("Failed to slice audio for Gemini: %s", exc)
        return {
            "ok": False,
            "error": f"音声区間の切り出しに失敗しました: {exc}",
            "fallback_used": True,
            "analysis_mode": "gemini_mode_b",
        }

    prompt = _build_gemini_audio_prompt(genre=genre, title=title, start_sec=start_sec, duration_sec=duration_sec)

    try:
        raw_result = ai.generate_with_audio(
            prompt=prompt,
            audio_bytes=audio_bytes,
            mime_type=mime_type,
            schema=GEMINI_GROOVE_SCHEMA,
        )
    except InterpretationError as exc:
        LOGGER.warning("Gemini multimodal analysis failed: %s", exc)
        return {
            "ok": False,
            "error": str(exc),
            "fallback_used": True,
            "analysis_mode": "gemini_mode_b",
        }

    cleaned = _validate_and_sanitize_gemini_output(raw_result, mechanical_analysis or {})
    return {
        "ok": True,
        "enhanced_by": "gemini",
        "analysis_mode": "gemini_mode_b",
        "model": ai.model,
        "theory_interpretation": cleaned["theory_interpretation"],
        "generation_rules": cleaned["generation_rules"],
        "audio_confidence_notes": cleaned["audio_confidence_notes"],
    }


def _slice_audio_bytes(audio_path: Path, start_sec: float, duration_sec: float) -> tuple[bytes, str]:
    """Extract a slice of audio and return WAV bytes within 20MB limit."""
    import soundfile as sf

    info = sf.info(str(audio_path))
    sr = info.samplerate
    start_frame = int(max(0.0, start_sec) * sr)
    frame_count = int(min(duration_sec, 60.0) * sr)

    data, samplerate = sf.read(
        str(audio_path),
        start=start_frame,
        stop=start_frame + frame_count,
        always_2d=True,
    )

    out = io.BytesIO()
    sf.write(out, data, samplerate, format="WAV", subtype="PCM_16")
    return out.getvalue(), "audio/wav"


def _build_gemini_audio_prompt(
    genre: str = "",
    title: str = "",
    start_sec: float = 0.0,
    duration_sec: float = 16.0,
) -> str:
    return f"""あなたはプロの音楽プロデューサー兼音楽理論の専門家です。
添付された音声ファイルは、参考曲の {start_sec:.1f}秒 〜 {start_sec + duration_sec:.1f}秒（約8〜16小節）のドラムおよびベース区間です。
タイトル: {title or '未指定'}
ジャンル: {genre or '未分類'}

【指示】
1. 音符列（MIDIノート列）をそのまま複製しないでください。
2. ドラム（キック）とベースの関係性、リズム密度、休符の広がり、帯域の住み分け（インターロック）を音楽理論に照らして解釈（theory_interpretation）してください。
3. 既存の作曲エンジン（DeterministicMIDIComposer）に渡す抽象的な制作ルール（generation_rules）を提案してください。
4. キーやコード進行を推測で断定せず、harmonic_status は 'unverified' または 'unknown' としてください。
5. 出力は指定されたJSONスキーマに厳密に準拠してください。
"""


def _build_gemini_prompt(
    analysis: dict[str, Any],
    genre: str = "",
    title: str = "",
) -> str:
    bpm_val = analysis.get("bpm", {}).get("value")
    bpm_conf = analysis.get("bpm", {}).get("confidence", 0.0)
    kick = analysis.get("kick_pattern", {})
    bass = analysis.get("bass_groove", {})
    energy = analysis.get("energy_dynamics", {})
    scope = analysis.get("analysis_scope", "full_track")
    exec_stats = analysis.get("execution_stats", {})
    sections = analysis.get("sections", [])
    harm = analysis.get("harmonic_analysis", {})

    section_summary = " → ".join([s["section_name"] for s in sections]) if sections else "未分割"
    total_dur = exec_stats.get("total_duration_seconds") or analysis.get("segment", {}).get("duration_seconds", 0.0)

    return f"""あなたはプロの音楽プロデューサーおよび音楽理論の専門家です。
以下はオーディオ（{ '全曲 ' + str(round(total_dur, 1)) + '秒' if scope == 'full_track' else '区間解析' }）から機械的に周波数分解および自己相関で検出された、ドラム（キック）とベースの客観的測定データです。

【曲・セクション情報】
タイトル: {title or '未指定'}
ジャンル指定: {genre or '未分類'}
解析範囲: { '全曲分析 (Full-Track)' if scope == 'full_track' else '指定区間分析 (Segment)' }
検出されたセクション展開: {section_summary}

【検出された客観データ】
- 推定BPM: {bpm_val or '不明'} (確信度: {bpm_conf:.2f})
- キックパターン: {'4つ打ち (Four-on-the-floor)' if kick.get('is_four_on_the_floor') else '変則/非4つ打ち'}
- キック発音回数: {kick.get('hit_count', 0)} 回 / 間隔概要: {kick.get('interval_summary', '不明')}
- ベース発音密度: 小節あたり約 {bass.get('density_notes_per_bar', 0.0):.1f} 音
- ベース休符比率: {int(bass.get('rest_ratio', 0.0) * 100)}%
- キック拍頭に対する裏拍（シンコペーション）率: {int(bass.get('syncopation_ratio', 0.0) * 100)}%
- エネルギー動態: {energy.get('trend', 'stable')}
- 音響解析キー候補（未検証）: {harm.get('key', 'unknown')} (確信度: {harm.get('confidence', 0.0):.2f})

【指示】
1. 音符列（MIDIノート列）を勝手に推測・複製しないでください。
2. 検出されたリズム密度、休符の広がり、キックとベースの帯域・時間的分離（インターロック）、およびセクション展開に基づき、音楽理論に照らした解説（theory_interpretation）を作成してください。
3. 既存の作曲エンジン（DeterministicMIDIComposer）に渡す抽象的な制作ルール（generation_rules）を提案してください。
4. キーや和声は倍音混信のためオーディオから未検証（unverified / unknown）です。絶対に推測でキーを断定せず、harmonic_status は 'unverified' または 'unknown' としてください。
5. 出力は指定されたJSONスキーマに厳密に準拠してください。
"""


def _validate_and_sanitize_gemini_output(
    data: dict[str, Any],
    mechanical_analysis: dict[str, Any],
) -> dict[str, Any]:
    """Ensure Gemini output respects safety boundaries, valid ranges, and unverified harmonic status."""
    theory = data.get("theory_interpretation", {})
    rules = data.get("generation_rules", {})
    notes = data.get("audio_confidence_notes", {})

    # Clamp numeric rule values safely
    default_bpm = mechanical_analysis.get("bpm", {}).get("value") or 120.0
    bpm_target = float(rules.get("bpm_target") or default_bpm)
    bpm_target = max(40.0, min(240.0, bpm_target))

    syncopation = float(rules.get("bass_syncopation", 0.5))
    syncopation = max(0.0, min(1.0, syncopation))

    rest_ratio = float(rules.get("bass_rest_ratio", 0.4))
    rest_ratio = max(0.0, min(1.0, rest_ratio))

    kick_style = rules.get("kick_style")
    if kick_style not in ("four_on_the_floor", "syncopated", "broken_beat"):
        kick_style = "four_on_the_floor" if mechanical_analysis.get("kick_pattern", {}).get("is_four_on_the_floor") else "syncopated"

    # Enforce unverified harmonic status - NEVER invent a definitive key from raw audio without evidence
    recommended_key = str(rules.get("recommended_key") or "unknown").strip()
    if recommended_key.lower() in ("c", "d", "e", "f", "g", "a", "b", "c major", "a minor"):
        # If Gemini guessed a common key without input, mark it unverified
        recommended_key = "unknown (unverified)"

    sanitized_rules = {
        "bpm_target": round(bpm_target, 1),
        "kick_style": kick_style,
        "bass_syncopation": round(syncopation, 2),
        "bass_rest_ratio": round(rest_ratio, 2),
        "kick_bass_interlock": bool(rules.get("kick_bass_interlock", True)),
        "recommended_key": recommended_key,
    }

    sanitized_theory = {
        "summary": str(theory.get("summary") or "Geminiによる音楽理論解釈"),
        "groove_structure": str(theory.get("groove_structure") or ""),
        "kick_bass_relationship": str(theory.get("kick_bass_relationship") or ""),
        "space_and_rests": str(theory.get("space_and_rests") or ""),
        "energy_and_arrangement": str(theory.get("energy_and_arrangement") or ""),
    }

    sanitized_notes = {
        "rhythm_confidence": float(notes.get("rhythm_confidence", 0.85)),
        "harmonic_status": "unverified",
        "explanation": str(notes.get("explanation") or "キー・和声は推測による誤判定を避けるため未検証です。"),
    }

    return {
        "theory_interpretation": sanitized_theory,
        "generation_rules": sanitized_rules,
        "audio_confidence_notes": sanitized_notes,
    }
