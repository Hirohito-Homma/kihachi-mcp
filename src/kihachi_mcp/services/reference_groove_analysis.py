"""Reference Groove & Musical Knowledge Analyzer.

Analyzes reference audio (either full track via streaming with memory bounds,
or an 8-16 bar segment) for Kick & Bass relationship, note density, rests,
accent patterns, energy dynamics, harmonic/key candidates, and section flow.
Never copies raw MIDI notes; extracts abstract musical rules with explicit confidence metrics.
"""

from __future__ import annotations

import math
from pathlib import Path
from collections.abc import Callable
import time
from typing import Any

import numpy as np

# Audio analysis constants
RATE = 22050
WINDOW = 2048
HOP = 512

PITCH_CLASSES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
# Krumhansl-Schmuckler Key Profiles
MAJOR_PROFILE = np.array(
    [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88],
    dtype=np.float64,
)
MINOR_PROFILE = np.array(
    [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17],
    dtype=np.float64,
)


class GrooveAnalysisError(ValueError):
    """User-facing analysis error."""


def _format_time(sec: float) -> str:
    """Format seconds into MM:SS format."""
    m = int(sec // 60)
    s = int(sec % 60)
    return f"{m}:{s:02d}"


def _load_audio_segment(
    path: Path, start_seconds: float = 0.0, duration_seconds: float = 16.0
) -> tuple[np.ndarray, float, float]:
    """Load and downmix an audio segment safely using soundfile."""
    try:
        import soundfile as sf
    except ImportError:
        raise GrooveAnalysisError("soundfileライブラリが必要です") from None

    if not path.is_file():
        raise GrooveAnalysisError(f"音源ファイルが見つかりません: {path}")

    try:
        info = sf.info(str(path))
        orig_sr = info.samplerate
        total_sec = info.duration
        if total_sec <= 0:
            raise GrooveAnalysisError("音源の長さが0秒です")

        actual_start = max(0.0, min(start_seconds, total_sec))
        actual_dur = min(duration_seconds, total_sec - actual_start)
        if actual_dur <= 0.5:
            raise GrooveAnalysisError("解析対象区間が短すぎます（最低0.5秒必要です）")

        start_frame = int(actual_start * orig_sr)
        frames_to_read = int(actual_dur * orig_sr)

        data, sr = sf.read(str(path), start=start_frame, frames=frames_to_read, always_2d=True)
    except Exception as exc:
        raise GrooveAnalysisError(f"音源ファイルの読み込みに失敗しました: {exc}") from exc

    # Downmix to mono
    mono = np.mean(data, axis=1)

    # Resample to RATE if needed
    if sr != RATE and len(mono) > 0:
        new_length = int(len(mono) * RATE / sr)
        if new_length > 0:
            indices = np.linspace(0, len(mono) - 1, new_length)
            mono = np.interp(indices, np.arange(len(mono)), mono)

    return mono.astype(np.float32), float(actual_dur), float(total_sec)


def _bandpass_energy(
    spec: np.ndarray, freqs: np.ndarray, low_hz: float, high_hz: float
) -> np.ndarray:
    """Sum spectral magnitude within a specific frequency band over time."""
    mask = (freqs >= low_hz) & (freqs < high_hz)
    if not np.any(mask):
        return np.zeros(spec.shape[1], dtype=np.float32)
    return np.sum(np.abs(spec[mask, :]), axis=0)


def _find_peaks(signal: np.ndarray, threshold_ratio: float = 0.35, min_dist: int = 4) -> list[int]:
    """Simple peak picking on 1D onset / energy envelope."""
    if len(signal) == 0:
        return []
    max_val = float(np.max(signal))
    if max_val < 1e-6:
        return []
    thresh = max_val * threshold_ratio
    peaks = []
    last_peak = -min_dist
    for i in range(1, len(signal) - 1):
        if (
            signal[i] > thresh
            and signal[i] > signal[i - 1]
            and signal[i] >= signal[i + 1]
            and (i - last_peak) >= min_dist
        ):
            peaks.append(i)
            last_peak = i
    return peaks


def _compute_chroma(spec: np.ndarray, freqs: np.ndarray) -> np.ndarray:
    """Compute 12-semitone pitch class profile (Chroma) from STFT magnitude."""
    chroma = np.zeros(12, dtype=np.float64)
    # Pitch range 65.4 Hz (C2) to 1046.5 Hz (C6)
    valid_mask = (freqs >= 65.0) & (freqs <= 1050.0)
    if not np.any(valid_mask):
        return chroma
    valid_freqs = freqs[valid_mask]
    mag_sum = np.sum(np.abs(spec[valid_mask, :]), axis=1)
    for f, mag in zip(valid_freqs, mag_sum, strict=False):
        if f > 0:
            midi_num = 69.0 + 12.0 * math.log2(f / 440.0)
            pc = int(round(midi_num)) % 12
            chroma[pc] += float(mag)
    total = np.sum(chroma)
    if total > 1e-6:
        chroma = chroma / total
    return chroma


def _estimate_chord_from_chroma(chroma: np.ndarray) -> dict[str, Any]:
    """Return an explicitly unverified triad candidate from a local chroma profile."""
    profile = np.asarray(chroma, dtype=np.float64)
    if profile.shape != (12,) or float(np.sum(profile)) < 1e-6 or float(np.std(profile)) < 1e-5:
        return {
            "chord": "unknown",
            "confidence": 0.0,
            "status": "unknown",
            "method": "12-tone chroma triad-template correlation",
            "reason": "有効なクロマ情報が不足しているためコード候補を出せません",
            "top_candidates": [],
        }

    profile = (profile - np.mean(profile)) / (np.std(profile) + 1e-9)
    candidates: list[tuple[str, float]] = []
    qualities = (("major", (0, 4, 7)), ("minor", (0, 3, 7)), ("diminished", (0, 3, 6)))
    for root, name in enumerate(PITCH_CLASSES):
        for quality, intervals in qualities:
            template = np.zeros(12, dtype=np.float64)
            template[(root + np.asarray(intervals)) % 12] = 1.0
            template = (template - np.mean(template)) / (np.std(template) + 1e-9)
            score = float(np.mean(profile * template))
            candidates.append((f"{name} {quality}", score))

    candidates.sort(key=lambda item: item[1], reverse=True)
    best, score = candidates[0]
    margin = score - candidates[1][1]
    # Mixed tracks and short windows often produce ambiguous chroma; never call this verified.
    confidence = round(max(0.0, min(0.65, (score - 0.25) * 0.7 + margin)), 2)
    accepted = score >= 0.35 and margin >= 0.08
    return {
        "chord": best if accepted else "unknown",
        "confidence": confidence if accepted else min(confidence, 0.30),
        "status": "unverified" if accepted else "unknown",
        "method": "12-tone chroma triad-template correlation",
        "reason": (
            f"局所クロマの三和音テンプレート候補（スコア {score:.2f}、次点との差 {margin:.2f}）。"
            "転回形・非和声音・ベース音・機能和声は未確定のため、耳またはDAWで要確認"
            if accepted
            else f"和音候補が曖昧（最高スコア {score:.2f}、次点との差 {margin:.2f}）"
        ),
        "top_candidates": [
            {"chord": chord, "score": round(value, 3)} for chord, value in candidates[:3]
        ],
    }


def _estimate_key_from_chroma(chroma: np.ndarray) -> dict[str, Any]:
    """Estimate musical key from 12-pitch-class profile using correlation."""
    if np.sum(chroma) < 1e-6 or np.std(chroma) < 1e-5:
        return {
            "key": "unknown",
            "scale": "unknown",
            "confidence": 0.0,
            "status": "unverified",
            "detection_method": "12-tone Pitch Class Profile Correlation",
            "reason": "音響解析上の相関が弱く曖昧なため未確定（倍音混信・無音区間）",
            "top_candidates": [],
        }

    c_norm = (chroma - np.mean(chroma)) / (np.std(chroma) + 1e-9)
    scores: list[tuple[str, str, float]] = []

    for i, pc in enumerate(PITCH_CLASSES):
        # Major
        maj_p = np.roll(MAJOR_PROFILE, i)
        maj_norm = (maj_p - np.mean(maj_p)) / (np.std(maj_p) + 1e-9)
        corr_maj = float(np.mean(c_norm * maj_norm))
        scores.append((f"{pc} major", "major", corr_maj))

        # Minor
        min_p = np.roll(MINOR_PROFILE, i)
        min_norm = (min_p - np.mean(min_p)) / (np.std(min_p) + 1e-9)
        corr_min = float(np.mean(c_norm * min_norm))
        scores.append((f"{pc} minor", "minor", corr_min))

    scores.sort(key=lambda x: x[2], reverse=True)
    best_key, best_scale, best_corr = scores[0]
    second_key, _, second_corr = scores[1]

    delta = best_corr - second_corr
    if best_corr < 0.35 or delta < 0.04:
        return {
            "key": "unknown",
            "scale": "unknown",
            "confidence": 0.0,
            "status": "unverified",
            "detection_method": "12-tone Pitch Class Profile Correlation",
            "reason": f"相関が弱く曖昧なため未確定（相関度 {best_corr:.2f}, 1位と2位の差分 {delta:.2f}）",
            "top_candidates": [{"key": k, "correlation": round(c, 2)} for k, _, c in scores[:3]],
        }

    conf = min(0.85, max(0.35, round(0.50 + delta * 2.0, 2)))
    return {
        "key": best_key,
        "scale": best_scale,
        "confidence": conf,
        "status": "unverified",
        "detection_method": "12-tone Pitch Class Profile Correlation (Krumhansl-Schmuckler)",
        "reason": f"クロマ相関度 {best_corr:.2f}（2位: {second_key} との差 {delta:.2f}）による機械推定",
        "top_candidates": [{"key": k, "correlation": round(c, 2)} for k, _, c in scores[:3]],
    }


def _extract_harmonic_timeline(
    chroma_history: list[tuple[float, float, np.ndarray]],
    overall_key: str,
) -> list[dict[str, Any]]:
    """Detect modulation and key shift candidates across time segments."""
    candidates = []
    for start_sec, end_sec, chroma in chroma_history:
        info = _estimate_key_from_chroma(chroma)
        k = info.get("key")
        conf = info.get("confidence", 0.0)
        if k and k != "unknown" and k != overall_key and conf >= 0.40:
            candidates.append({
                "time_range": f"{_format_time(start_sec)}–{_format_time(end_sec)}",
                "start_sec": round(start_sec, 2),
                "end_sec": round(end_sec, 2),
                "key_candidate": k,
                "confidence": conf,
                "status": "unverified",
                "detection_method": "Windowed Chroma Profile Correlation",
                "evidence": f"全体調（{overall_key}）からの周波数エネルギー分布の偏りシフト（局所確信度 {conf:.2f}）",
            })
    return candidates


def _detect_sections_from_timeline(
    timeline: list[dict[str, Any]], total_sec: float
) -> list[dict[str, Any]]:
    """Infer section candidates (Intro, Verse, Build, Drop, Break, Outro) from energy and rhythm dynamics."""
    if not timeline:
        return []

    avg_rms = float(np.mean([item["energy"]["rms"] for item in timeline])) or 1e-4

    raw_labels = []
    for idx, item in enumerate(timeline):
        rms = item["energy"]["rms"]
        kick_hits = item["kick"]["hit_count"]
        four_floor = item["kick"]["is_four_on_the_floor"]
        sub_ratio = item["energy"]["band_ratios"]["sub_ratio"]
        start_sec = item["start_sec"]
        end_sec = item["end_sec"]

        prev_rms = timeline[idx - 1]["energy"]["rms"] if idx > 0 else rms
        rms_slope = rms / max(1e-4, prev_rms)

        if start_sec < 25.0 and (kick_hits <= 2 or rms < 0.5 * avg_rms):
            label = "Intro candidate"
            conf = 0.55
            evidence = f"曲頭区間でキック検出 {kick_hits} 回、RMS {rms:.4f}（全区間平均比 {rms / max(avg_rms, 1e-4):.2f}）"
        elif end_sec > total_sec * 0.82 and rms < 0.65 * avg_rms:
            label = "Outro candidate"
            conf = 0.55
            evidence = f"曲末尾区間のRMS {rms:.4f}（全区間平均比 {rms / max(avg_rms, 1e-4):.2f}）。フェードアウト自体は未判定"
        elif rms_slope >= 1.35 and idx > 0:
            label = "Build candidate"
            conf = 0.50
            evidence = f"区間RMSが直前区間の {rms_slope:.2f} 倍。上昇は測定値、ビルド解釈は候補"
        elif (rms >= 1.10 * avg_rms and four_floor) or (sub_ratio >= 0.35 and four_floor):
            label = "High-energy groove candidate"
            conf = 0.50
            evidence = f"RMS {rms:.4f}（全区間平均比 {rms / max(avg_rms, 1e-4):.2f}）、4つ打ち判定 {four_floor}、Sub比率 {sub_ratio:.2f}。Drop確定ではありません"
        elif idx > 0 and kick_hits <= 1 and sub_ratio < 0.20:
            label = "Low-energy / sparse-kick candidate"
            conf = 0.50
            evidence = f"キック検出 {kick_hits} 回、Sub比率 {sub_ratio:.2f}。Break解釈は候補"
        else:
            label = "Groove core candidate"
            conf = 0.45
            evidence = f"キック検出 {kick_hits} 回（約 {item['kick']['hits_per_bar']} hits/bar）、RMS {rms:.4f}"

        raw_labels.append({
            "label": label,
            "conf": conf,
            "evidence": evidence,
            "start_sec": start_sec,
            "end_sec": end_sec,
        })

    # Merge adjacent identical labels
    merged: list[dict[str, Any]] = []
    for r in raw_labels:
        if not merged:
            merged.append(dict(r))
        else:
            last = merged[-1]
            if last["label"] == r["label"]:
                last["end_sec"] = r["end_sec"]
            else:
                merged.append(dict(r))

    sections = []
    for m in merged:
        t_range = f"{_format_time(m['start_sec'])}–{_format_time(m['end_sec'])}"
        sections.append({
            "section_name": m["label"],
            "start_sec": round(m["start_sec"], 2),
            "end_sec": round(m["end_sec"], 2),
            "time_range": t_range,
            "confidence": round(m["conf"], 2),
            "status": "unverified",
            "evidence": m["evidence"],
        })
    return sections


def _build_theory_timeline(
    sections: list[dict[str, Any]],
    timeline: list[dict[str, Any]],
    bpm: float | None,
    chord_timeline: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Explain measured rhythmic properties without claiming unmeasured production intent."""
    results = []
    for sec in sections:
        matching = [
            it for it in timeline
            if it["start_sec"] >= sec["start_sec"] - 1.0
            and it["end_sec"] <= sec["end_sec"] + 1.0
        ]
        if not matching:
            continue

        avg_sync = float(np.mean([it["bass"]["syncopation_ratio"] for it in matching]))
        avg_rest = float(np.mean([it["bass"]["rest_ratio"] for it in matching]))
        avg_dens = float(np.mean([it["bass"]["density_notes_per_bar"] for it in matching]))
        avg_reg = float(np.mean([it["kick"]["regularity"] for it in matching]))
        avg_rms = float(np.mean([it["energy"]["rms"] for it in matching]))
        kick_share = float(np.mean([1.0 if it["kick"]["is_four_on_the_floor"] else 0.0 for it in matching]))

        harmonic_rows = [
            row for row in (chord_timeline or [])
            if row["start_sec"] < sec["end_sec"] and row["end_sec"] > sec["start_sec"]
        ]
        chord_names = list(dict.fromkeys(
            row["chord"] for row in harmonic_rows
            if row.get("chord") and row["chord"] != "unknown"
        ))
        chord_evidence = (
            "三和音候補 " + " → ".join(chord_names[:8]) + "（全候補未検証）"
            if chord_names else "コード候補なし（未確定）"
        )
        evidence = (
            f"キック規則性 {avg_reg:.2f}、4つ打ち判定率 {kick_share:.0%}、"
            f"ベース密度 {avg_dens:.1f}音/小節、検出上の非同時発音率 {avg_sync:.0%}、"
            f"休符比率 {avg_rest:.0%}、RMS {avg_rms:.4f}、{chord_evidence}"
        )
        interpretations = []
        if avg_reg >= 0.65:
            interpretations.append("キックの周期性が高く、拍節の基準を作る傾向があります")
        else:
            interpretations.append("キックの周期性は限定的で、拍の基準は他の音や聴感との照合が必要です")
        if avg_sync >= 0.55:
            interpretations.append("ベースは検出上キックと同時でない発音が多く、シンコペーションによる拍との対比が候補になります")
        else:
            interpretations.append("ベースとキックの非同時発音は多くなく、拍の補強または重なりが候補になります")
        if avg_rest >= 0.40:
            interpretations.append("ベースの休符が比較的多く、低域の発音密度を抑えた配置です")
        else:
            interpretations.append("ベースの休符が比較的少なく、持続的な発音密度を持つ配置です")
        if chord_names:
            interpretations.append(
                "クロマの三和音照合では " + " → ".join(chord_names[:8])
                + " が時間順の候補です。コード進行の確定ではなく、転回形・非和声音・ベース音との照合は未実施で、機能和声は未確定です"
            )
        else:
            interpretations.append("三和音候補は安定して得られず、コード進行と機能和声は未確定です")
        interpretations.append("これらは信号特徴からの解釈候補であり、音色、リバーブ、作曲意図を直接検出した結果ではありません")

        results.append({
            "time_range": sec["time_range"],
            "start_sec": sec["start_sec"],
            "end_sec": sec["end_sec"],
            "target_section": sec["section_name"],
            "measurement_evidence": evidence,
            "interpretation": "。".join(interpretations) + "。",
            "confidence": round(min(
                sec.get("confidence", 0.0),
                avg_reg,
                max(avg_sync, avg_rest),
                0.35 if chord_names else 0.50,
            ), 2),
            "status": "unverified",
            "analysis_note": "コード進行・音色・効果音はこの解釈の根拠に含めていません",
        })
    return results


def _build_full_track_theory_interpretation(
    kick: dict[str, Any],
    bass: dict[str, Any],
    energy: dict[str, Any],
    harmonic: dict[str, Any],
    sections: list[dict[str, Any]],
    bpm: float | None,
) -> dict[str, Any]:
    """Summarize whole-track measurements as cautious theory hypotheses."""
    lines: list[str] = []
    if bpm is not None:
        lines.append(f"全曲テンポ候補は {bpm:.1f} BPM です。これは区間ごとの自己相関候補の中央値で、拍位置の聴感確認は未実施です。")

    four_floor_share = float(kick.get("regularity", 0.0))
    if kick.get("is_four_on_the_floor"):
        lines.append(f"12秒解析窓の約 {four_floor_share:.0%} で4つ打ち判定があり、全曲の主要な拍節アンカー候補です。")
    else:
        lines.append(f"4つ打ち判定は12秒解析窓の約 {four_floor_share:.0%} でした。拍節の感じ方は他パートも含めて確認が必要です。")

    sync_ratio = float(bass.get("syncopation_ratio", 0.0))
    rest_ratio = float(bass.get("rest_ratio", 0.0))
    density = float(bass.get("density_notes_per_bar", 0.0))
    lines.append(
        f"低中域ピーク検出は小節あたり約 {density:.1f} 回、休符比率の代理指標は {rest_ratio:.0%}、"
        f"キックから離れたベースピークの比率は {sync_ratio:.0%} です。後者は拍裏を直接認識した値ではなく、シンコペーション解釈は候補です。"
    )
    lines.append(
        "休符比率は低中域エネルギー閾値に基づく代理指標です。無音やリバーブ、楽器の種類、制作者の意図を直接検出した結果ではありません。"
    )

    sec_names = [s["section_name"] for s in sections]
    if sec_names:
        lines.append(
            f"時間軸上の構成ラベル候補は {' → '.join(sec_names)} です。エネルギーと帯域・キックの簡易ヒューリスティックによる候補で、正式な曲構成ラベルではありません。"
        )
    if harmonic.get("key") and harmonic["key"] != "unknown":
        lines.append(
            f"全体クロマからの調性候補は {harmonic['key']}（確信度 {harmonic.get('confidence', 0.0):.2f}、未検証）です。"
        )
    else:
        lines.append("全体キー候補は未確定です。")
    chord_sequence = harmonic.get("chord_candidate_sequence") or []
    if chord_sequence:
        lines.append(
            "時間窓ごとの三和音候補は " + " → ".join(chord_sequence[:12])
            + " です。これはクロマ照合による未検証候補列で、確定コード進行・機能和声の判定ではありません。"
        )
    else:
        lines.append("三和音候補が安定していないため、コード進行と機能和声は未確定です。")

    return {
        "summary": "全曲の信号特徴に基づくリズム・エネルギー・調性候補の整理",
        "detailed_explanations": lines,
        "theoretical_focus": "Measured rhythm, low-mid density, energy dynamics and unverified harmony",
        "status": "unverified",
    }


def _analyze_audio_streaming(
    path: Path,
    user_hint_bpm: float | None = None,
    chunk_seconds: float = 12.0,
    progress_callback: Callable[[float, str], None] | None = None,
) -> dict[str, Any]:
    """Analyze entire track using streaming windows with bounded memory."""
    try:
        import soundfile as sf
    except ImportError:
        raise GrooveAnalysisError("soundfileライブラリが必要です") from None

    if not path.is_file():
        raise GrooveAnalysisError(f"音源ファイルが見つかりません: {path}")

    start_perf = time.perf_counter()
    info = sf.info(str(path))
    orig_sr = info.samplerate
    total_frames = info.frames
    total_sec = info.duration

    if total_sec <= 0.5:
        raise GrooveAnalysisError("音源の長さが短すぎます（最低0.5秒必要です）")

    block_frames = int(chunk_seconds * orig_sr)
    current_frame = 0

    accumulated_chroma = np.zeros(12, dtype=np.float64)
    chroma_history: list[tuple[float, float, np.ndarray]] = []
    timeline: list[dict[str, Any]] = []

    total_kick_hits = 0
    total_bass_hits = 0
    total_four_floor_count = 0
    syncopation_ratios = []
    rest_ratios = []
    chunk_bpms = []

    window = np.hanning(WINDOW)
    freqs = np.fft.rfftfreq(WINDOW, 1.0 / RATE)
    fps = RATE / HOP

    # Stream file block-by-block with bounded memory consumption
    with sf.SoundFile(str(path)) as snd:
        chunk_idx = 0
        while True:
            data = snd.read(frames=block_frames, dtype="float32", always_2d=True)
            read_len = len(data)
            if read_len == 0:
                break

            chunk_start = current_frame / orig_sr
            chunk_dur = read_len / orig_sr
            chunk_end = chunk_start + chunk_dur

            # Downmix to mono
            mono = np.mean(data, axis=1)

            # Resample to RATE if needed
            if orig_sr != RATE and len(mono) > 0:
                new_len = int(len(mono) * RATE / orig_sr)
                if new_len > 0:
                    indices = np.linspace(0, len(mono) - 1, new_len)
                    mono = np.interp(indices, np.arange(len(mono)), mono).astype(np.float32)

            if len(mono) >= WINDOW:
                num_frames = 1 + (len(mono) - WINDOW) // HOP
                frames = np.lib.stride_tricks.sliding_window_view(mono, WINDOW)[::HOP]
                windowed = frames * window
                stft = np.fft.rfft(windowed, axis=1).T

                # Bandpass energies
                kick_e = _bandpass_energy(stft, freqs, 30.0, 100.0)
                bass_e = _bandpass_energy(stft, freqs, 100.0, 300.0)
                mid_e = _bandpass_energy(stft, freqs, 300.0, 2000.0)
                high_e = _bandpass_energy(stft, freqs, 2000.0, 8000.0)
                tot_e = _bandpass_energy(stft, freqs, 20.0, 8000.0)

                # Chroma: retain 3-second local profiles so chord changes are not collapsed
                # into one 12-second estimate. The chunk itself remains the bounded read unit.
                chunk_chroma = _compute_chroma(stft, freqs)
                accumulated_chroma += chunk_chroma
                frames_per_harmony = max(1, int(3.0 * RATE / HOP))
                for h_start in range(0, stft.shape[1], frames_per_harmony):
                    h_end = min(stft.shape[1], h_start + frames_per_harmony)
                    local_chroma = _compute_chroma(stft[:, h_start:h_end], freqs)
                    local_start = chunk_start + h_start * HOP / RATE
                    local_end = min(chunk_end, chunk_start + h_end * HOP / RATE)
                    chroma_history.append((local_start, local_end, local_chroma))

                # Normalize envelopes
                k_max = float(np.max(kick_e)) or 1.0
                b_max = float(np.max(bass_e)) or 1.0
                norm_k = kick_e / k_max
                norm_b = bass_e / b_max

                # Peaks
                k_peaks = _find_peaks(norm_k, threshold_ratio=0.4, min_dist=int(fps * 0.25))
                b_peaks = _find_peaks(norm_b, threshold_ratio=0.3, min_dist=int(fps * 0.12))

                # Estimated BPM of this chunk
                cBpm, cConf, _ = _estimate_bpm(kick_e, chunk_dur, user_hint_bpm)
                if cBpm and cConf >= 0.5:
                    chunk_bpms.append((cBpm, cConf))

                # Kick & Bass analysis for chunk
                k_info = _analyze_kick_pattern(k_peaks, chunk_dur, cBpm)
                b_info = _analyze_bass_groove(b_peaks, k_peaks, norm_b, chunk_dur, cBpm)
                e_info = _analyze_energy_dynamics(tot_e, chunk_dur)

                # Band ratios
                tot_sum = float(np.sum(tot_e)) or 1.0
                sub_r = float(np.sum(kick_e)) / tot_sum
                bass_r = float(np.sum(bass_e)) / tot_sum
                mid_r = float(np.sum(mid_e)) / tot_sum
                high_r = float(np.sum(high_e)) / tot_sum

                # Key and triad candidates for this displayed groove window.
                c_key_info = _estimate_key_from_chroma(chunk_chroma)
                c_chord_info = _estimate_chord_from_chroma(chunk_chroma)

                # Accumulate
                total_kick_hits += len(k_peaks)
                total_bass_hits += len(b_peaks)
                if k_info["is_four_on_the_floor"]:
                    total_four_floor_count += 1
                syncopation_ratios.append(b_info["syncopation_ratio"])
                rest_ratios.append(b_info["rest_ratio"])

                # RMS energy
                rms_val = float(np.sqrt(np.mean(mono**2)))

                timeline.append({
                    "index": chunk_idx,
                    "start_sec": round(chunk_start, 2),
                    "end_sec": round(chunk_end, 2),
                    "duration_sec": round(chunk_dur, 2),
                    "time_range": f"{_format_time(chunk_start)}–{_format_time(chunk_end)}",
                    "kick": {
                        "hit_count": len(k_peaks),
                        "hits_per_bar": k_info["hits_per_bar"],
                        "is_four_on_the_floor": k_info["is_four_on_the_floor"],
                        "regularity": k_info["regularity"],
                        "confidence": k_info["confidence"],
                    },
                    "bass": {
                        "hit_count": len(b_peaks),
                        "density_notes_per_bar": b_info["density_notes_per_bar"],
                        "rest_ratio": b_info["rest_ratio"],
                        "syncopation_ratio": b_info["syncopation_ratio"],
                        "confidence": b_info["confidence"],
                    },
                    "energy": {
                        "rms": round(rms_val, 4),
                        "dynamic_range_db": e_info["dynamic_range_db"],
                        "trend": e_info["trend"],
                        "band_ratios": {
                            "sub_ratio": round(sub_r, 3),
                            "bass_ratio": round(bass_r, 3),
                            "mid_ratio": round(mid_r, 3),
                            "high_ratio": round(high_r, 3),
                        },
                    },
                    "key_candidate": c_key_info["key"],
                    "key_confidence": c_key_info["confidence"],
                    "chord_candidate": c_chord_info["chord"],
                    "chord_confidence": c_chord_info["confidence"],
                    "chord_status": c_chord_info["status"],
                })

            current_frame += read_len
            chunk_idx += 1
            if progress_callback:
                try:
                    progress_callback(min(0.88, 0.88 * current_frame / max(1, total_frames)), "audio_analysis")
                except Exception:
                    pass
            if read_len < block_frames:
                break

    reached_end = bool(current_frame >= total_frames)
    if progress_callback:
        try:
            progress_callback(0.92, "theory_interpretation")
        except Exception:
            pass
    elapsed_seconds = round(time.perf_counter() - start_perf, 3)
    analyzed_dur = min(total_sec, current_frame / orig_sr)

    # Global BPM estimation
    if user_hint_bpm and 60 <= user_hint_bpm <= 180:
        overall_bpm = float(round(user_hint_bpm, 1))
        overall_bpm_conf = 0.90
        overall_bpm_method = "user_specified_hint"
    elif chunk_bpms:
        bpms = [b for b, _ in chunk_bpms]
        confs = [c for _, c in chunk_bpms]
        overall_bpm = round(float(np.median(bpms)), 1)
        overall_bpm_conf = round(float(np.mean(confs)), 2)
        overall_bpm_method = "streaming_autocorrelation_consensus"
    else:
        overall_bpm = 120.0
        overall_bpm_conf = 0.30
        overall_bpm_method = "default_fallback_unverified"

    # Overall Kick / Bass metrics
    num_chunks = max(1, len(timeline))
    sec_per_bar = (60.0 / overall_bpm) * 4.0
    est_total_bars = max(1.0, analyzed_dur / sec_per_bar)

    overall_kick_density = round(total_kick_hits / est_total_bars, 2)
    overall_bass_density = round(total_bass_hits / est_total_bars, 2)
    overall_four_floor = (total_four_floor_count / num_chunks) >= 0.50
    avg_sync = round(float(np.mean(syncopation_ratios)) if syncopation_ratios else 0.5, 2)
    avg_rest = round(float(np.mean(rest_ratios)) if rest_ratios else 0.5, 2)

    overall_kick_info = {
        "hit_count": total_kick_hits,
        "hits_per_bar": overall_kick_density,
        "is_four_on_the_floor": overall_four_floor,
        "interval_summary": "全曲を通じた4つ打ちキック基調"
        if overall_four_floor
        else f"変則/複合リズムキック（約 {overall_kick_density} hits/bar）",
        "regularity": round(total_four_floor_count / num_chunks, 2),
        "confidence": 0.85 if overall_four_floor else 0.65,
        "method": "streaming_full_track_peak_tracking",
    }

    overall_bass_info = {
        "hit_count": total_bass_hits,
        "density_notes_per_bar": overall_bass_density,
        "rest_ratio": avg_rest,
        "syncopation_ratio": avg_sync,
        "accent_positions": [],
        "confidence": 0.80 if total_bass_hits > 0 else 0.50,
        "method": "streaming_mid_low_band_transients",
    }

    # Energy dynamics across whole song
    all_rms = [it["energy"]["rms"] for it in timeline]
    if all_rms:
        first_q = float(np.mean(all_rms[: max(1, len(all_rms) // 4)]))
        last_q = float(np.mean(all_rms[-max(1, len(all_rms) // 4) :]))
        e_ratio = last_q / max(1e-6, first_q)
        overall_trend = (
            "building" if e_ratio >= 1.35 else ("dropping" if e_ratio <= 0.70 else "stable")
        )
        max_r = max(all_rms)
        min_r = max(1e-5, min(all_rms))
        dyn_db = round(20.0 * math.log10(max(1e-4, max_r) / min_r), 1)
    else:
        overall_trend = "stable"
        dyn_db = 0.0

    overall_energy_info = {
        "trend": overall_trend,
        "dynamic_range_db": dyn_db,
        "confidence": 0.85,
        "method": "full_track_rms_profile",
    }

    # Key / Scale
    overall_harmonic_info = _estimate_key_from_chroma(accumulated_chroma)
    harmonic_timeline = _extract_harmonic_timeline(
        chroma_history, overall_harmonic_info.get("key", "unknown")
    )
    chord_timeline = []
    for h_start, h_end, local_chroma in chroma_history:
        chord_info = _estimate_chord_from_chroma(local_chroma)
        local_key_info = _estimate_key_from_chroma(local_chroma)
        chord_timeline.append({
            "start_sec": round(h_start, 2),
            "end_sec": round(h_end, 2),
            "time_range": f"{_format_time(h_start)}–{_format_time(h_end)}",
            "key_candidate": local_key_info["key"],
            "key_confidence": local_key_info["confidence"],
            "key_status": local_key_info["status"],
            **chord_info,
        })
    chord_sequence = list(dict.fromkeys(
        row["chord"] for row in chord_timeline
        if row.get("chord") and row["chord"] != "unknown"
    ))
    overall_harmonic_info["chord_candidate_sequence"] = chord_sequence
    overall_harmonic_info["chord_progression_status"] = "candidate_only_unverified"

    # Sections
    sections = _detect_sections_from_timeline(timeline, total_sec)

    # Theory Interpretation
    theory_timeline = _build_theory_timeline(
        sections, timeline, overall_bpm, chord_timeline=chord_timeline
    )

    theory_interpretation = _build_full_track_theory_interpretation(
        overall_kick_info,
        overall_bass_info,
        overall_energy_info,
        overall_harmonic_info,
        sections,
        overall_bpm,
    )

    # Generation Rules
    generation_rules = _derive_generation_rules(
        overall_kick_info,
        overall_bass_info,
        overall_energy_info,
        overall_bpm,
    )
    if overall_harmonic_info.get("key") and overall_harmonic_info["key"] != "unknown":
        generation_rules["recommended_key"] = overall_harmonic_info["key"]

    return {
        "ok": True,
        "schema_version": "2.0.0",
        "analysis_scope": "full_track",
        "audio_file": path.name,
        "segment": {
            "start_seconds": 0.0,
            "duration_seconds": round(analyzed_dur, 2),
            "sample_rate": RATE,
        },
        "execution_stats": {
            "reached_end": reached_end,
            "elapsed_seconds": elapsed_seconds,
            "analyzed_duration_seconds": round(analyzed_dur, 2),
            "total_duration_seconds": round(total_sec, 2),
            "chunk_count": len(timeline),
            "scope": "full_track",
        },
        "bpm": {
            "value": overall_bpm,
            "confidence": overall_bpm_conf,
            "unit": "BPM",
            "method": overall_bpm_method,
            "status": "verified" if overall_bpm_conf >= 0.75 else "unverified",
        },
        "kick_pattern": overall_kick_info,
        "bass_groove": overall_bass_info,
        "energy_dynamics": overall_energy_info,
        "section": {
            "candidate": sections[0]["section_name"] if sections else "groove_core",
            "section_count": len(sections),
            "confidence": 0.70,
            "status": "unverified",
            "reason": f"全曲を通じて {len(sections)} セクションの展開を検出",
        },
        "sections": sections,
        "harmonic_analysis": {
            **overall_harmonic_info,
            "chord_progression_status": "candidate_only_unverified",
            "chord_progression_note": (
                "短区間クロマの三和音候補です。完成ミックスではコード確定として扱いません。"
                "転回形、非和声音、ベース音、機能和声は未確定です。"
            ),
        },
        "harmonic_timeline": harmonic_timeline,
        "chord_timeline": chord_timeline,
        "timeline": timeline,
        "theory_interpretation": theory_interpretation,
        "theory_timeline": theory_timeline,
        "generation_rules": generation_rules,
    }


def _analyze_groove_segment(
    path: Path,
    start_seconds: float = 0.0,
    duration_seconds: float = 16.0,
    user_hint_bpm: float | None = None,
    progress_callback: Callable[[float, str], None] | None = None,
) -> dict[str, Any]:
    """Analyze a single audio segment (legacy 8-16 bars mode)."""
    def report_progress(value: float, stage: str) -> None:
        if progress_callback:
            try:
                progress_callback(value, stage)
            except Exception:
                pass

    start_perf = time.perf_counter()
    report_progress(0.10, "audio_loading")
    samples, actual_dur, total_sec = _load_audio_segment(path, start_seconds, duration_seconds)
    report_progress(0.25, "audio_loaded")

    if len(samples) < WINDOW:
        return _empty_or_failed_analysis(path, "サンプル数が不足しています")

    window = np.hanning(WINDOW)
    num_frames = 1 + (len(samples) - WINDOW) // HOP
    if num_frames <= 0:
        return _empty_or_failed_analysis(path, "フレーム数が不足しています")

    frames = np.lib.stride_tricks.sliding_window_view(samples, WINDOW)[::HOP]
    windowed = frames * window
    stft = np.fft.rfft(windowed, axis=1).T
    freqs = np.fft.rfftfreq(WINDOW, 1.0 / RATE)
    report_progress(0.55, "audio_analysis")

    # 1. Band energies
    kick_energy = _bandpass_energy(stft, freqs, 30.0, 100.0)
    bass_energy = _bandpass_energy(stft, freqs, 100.0, 300.0)
    total_energy = _bandpass_energy(stft, freqs, 20.0, 5000.0)

    # Normalize envelopes
    k_max = float(np.max(kick_energy)) or 1.0
    b_max = float(np.max(bass_energy)) or 1.0
    norm_k = kick_energy / k_max
    norm_b = bass_energy / b_max

    # 2. BPM Estimation
    bpm_val, bpm_conf, bpm_method = _estimate_bpm(kick_energy, actual_dur, user_hint_bpm)

    # 3. Kick Analysis
    kick_peaks = _find_peaks(norm_k, threshold_ratio=0.4, min_dist=int(RATE / HOP * 0.25))
    kick_info = _analyze_kick_pattern(kick_peaks, actual_dur, bpm_val)

    # 4. Bass Analysis
    bass_peaks = _find_peaks(norm_b, threshold_ratio=0.3, min_dist=int(RATE / HOP * 0.12))
    bass_info = _analyze_bass_groove(bass_peaks, kick_peaks, norm_b, actual_dur, bpm_val)

    # 5. Energy Dynamic Trend
    energy_info = _analyze_energy_dynamics(total_energy, actual_dur)

    # 6. Chroma & Key Candidate
    chroma = _compute_chroma(stft, freqs)
    key_info = _estimate_key_from_chroma(chroma)
    chord_info = _estimate_chord_from_chroma(chroma)
    report_progress(0.82, "audio_analysis")

    # 7. Section Candidate
    section_candidate = "groove_core" if kick_info["is_four_on_the_floor"] else "break_or_intro"
    section_info = {
        "candidate": section_candidate,
        "confidence": 0.45 if kick_info["is_four_on_the_floor"] else 0.25,
        "status": "unverified",
        "reason": "指定区間解析に基づく簡易セクション推定（実曲全体の構成判断ではありません）",
    }
    sections = [
        {
            "section_name": section_candidate,
            "start_sec": round(start_seconds, 2),
            "end_sec": round(start_seconds + actual_dur, 2),
            "time_range": f"{_format_time(start_seconds)}–{_format_time(start_seconds + actual_dur)}",
            "confidence": section_info["confidence"],
            "status": "unverified",
            "evidence": f"区間内キック規則性 {kick_info['regularity']:.2f}, エネルギー動態 {energy_info['trend']}",
        }
    ]

    # 8. Music Theory Interpretation
    interpretation = _build_theory_interpretation(kick_info, bass_info, energy_info, bpm_val)

    # Timeline single segment entry
    single_chunk = {
        "index": 0,
        "start_sec": round(start_seconds, 2),
        "end_sec": round(start_seconds + actual_dur, 2),
        "duration_sec": round(actual_dur, 2),
        "time_range": f"{_format_time(start_seconds)}–{_format_time(start_seconds + actual_dur)}",
        "kick": {
            "hit_count": len(kick_peaks),
            "hits_per_bar": kick_info["hits_per_bar"],
            "is_four_on_the_floor": kick_info["is_four_on_the_floor"],
            "regularity": kick_info["regularity"],
            "confidence": kick_info["confidence"],
        },
        "bass": {
            "hit_count": len(bass_peaks),
            "density_notes_per_bar": bass_info["density_notes_per_bar"],
            "rest_ratio": bass_info["rest_ratio"],
            "syncopation_ratio": bass_info["syncopation_ratio"],
            "confidence": bass_info["confidence"],
        },
        "energy": {
            "rms": round(float(np.sqrt(np.mean(samples**2))), 4),
            "dynamic_range_db": energy_info["dynamic_range_db"],
            "trend": energy_info["trend"],
            "band_ratios": {
                "sub_ratio": round(float(np.sum(kick_energy)) / (float(np.sum(total_energy)) or 1.0), 3),
                "bass_ratio": round(float(np.sum(bass_energy)) / (float(np.sum(total_energy)) or 1.0), 3),
                "mid_ratio": 0.0,
                "high_ratio": 0.0,
            },
        },
        "key_candidate": key_info["key"],
        "key_confidence": key_info["confidence"],
        "chord_candidate": chord_info["chord"],
        "chord_confidence": chord_info["confidence"],
        "chord_status": chord_info["status"],
    }
    timeline = [single_chunk]

    theory_timeline = [
        {
            "time_range": f"{_format_time(start_seconds)}–{_format_time(start_seconds + actual_dur)}",
            "start_sec": round(start_seconds, 2),
            "end_sec": round(start_seconds + actual_dur, 2),
            "target_section": section_candidate,
            "measurement_evidence": f"キック規則性 {kick_info['regularity']:.2f}, ベースシンコペーション率 {int(bass_info['syncopation_ratio']*100)}%, 休符率 {int(bass_info['rest_ratio']*100)}%",
            "interpretation": interpretation["detailed_explanations"][0]
            if interpretation.get("detailed_explanations")
            else interpretation.get("summary", ""),
            "confidence": round(kick_info["confidence"] * 0.9, 2),
            "status": "unverified",
        }
    ]

    # 9. Generation Rules
    generation_rules = _derive_generation_rules(kick_info, bass_info, energy_info, bpm_val)
    if key_info.get("key") and key_info["key"] != "unknown":
        generation_rules["recommended_key"] = key_info["key"]

    report_progress(0.92, "theory_interpretation")
    elapsed_seconds = round(time.perf_counter() - start_perf, 3)

    return {
        "ok": True,
        "schema_version": "2.0.0",
        "analysis_scope": "segment",
        "audio_file": path.name,
        "segment": {
            "start_seconds": start_seconds,
            "duration_seconds": round(actual_dur, 2),
            "sample_rate": RATE,
        },
        "execution_stats": {
            "reached_end": bool(start_seconds + actual_dur >= total_sec - 0.05),
            "elapsed_seconds": elapsed_seconds,
            "analyzed_duration_seconds": round(actual_dur, 2),
            "total_duration_seconds": round(total_sec, 2),
            "chunk_count": 1,
            "scope": "segment",
        },
        "bpm": {
            "value": bpm_val,
            "confidence": bpm_conf,
            "unit": "BPM",
            "method": bpm_method,
            "status": "verified" if bpm_conf >= 0.75 else "unverified",
        },
        "kick_pattern": kick_info,
        "bass_groove": bass_info,
        "energy_dynamics": energy_info,
        "section": section_info,
        "sections": sections,
        "harmonic_analysis": key_info,
        "harmonic_timeline": [],
        "chord_timeline": [{
            "start_sec": round(start_seconds, 2),
            "end_sec": round(start_seconds + actual_dur, 2),
            "time_range": f"{_format_time(start_seconds)}–{_format_time(start_seconds + actual_dur)}",
            **chord_info,
        }],
        "timeline": timeline,
        "theory_interpretation": interpretation,
        "theory_timeline": theory_timeline,
        "generation_rules": generation_rules,
    }


def analyze_groove(
    path: Path,
    start_seconds: float = 0.0,
    duration_seconds: float | None = None,
    user_hint_bpm: float | None = None,
    scope: str | None = None,
    progress_callback: Callable[[float, str], None] | None = None,
) -> dict[str, Any]:
    """Analyze drum and bass relationship and groove characteristics from an audio file.

    Defaults to full-track streaming analysis ('full_track').
    Supports explicit segment analysis ('segment') for backward compatibility.
    """
    if scope is None:
        if duration_seconds is not None:
            scope = "segment"
        else:
            scope = "full_track"

    if scope == "full_track":
        return _analyze_audio_streaming(
            path=path,
            user_hint_bpm=user_hint_bpm,
            progress_callback=progress_callback,
        )

    # Legacy segment mode
    dur = 16.0 if duration_seconds is None else duration_seconds
    return _analyze_groove_segment(
        path=path,
        start_seconds=start_seconds,
        duration_seconds=dur,
        user_hint_bpm=user_hint_bpm,
        progress_callback=progress_callback,
    )


def _estimate_bpm(
    kick_energy: np.ndarray, duration_sec: float, user_hint_bpm: float | None
) -> tuple[float | None, float, str]:
    """Estimate BPM via autocorrelation of kick transient envelope."""
    if user_hint_bpm and 60 <= user_hint_bpm <= 180:
        return float(round(user_hint_bpm, 1)), 0.90, "user_specified_hint"

    fps = RATE / HOP
    if len(kick_energy) < int(fps * 2.0) or duration_sec < 2.0:
        return None, 0.0, "insufficient_duration"

    centered = kick_energy - np.mean(kick_energy)
    norm = float(centered @ centered)
    if norm < 1e-8:
        return None, 0.0, "silent_or_uniform"

    # Use ceil so discretization cannot admit a tempo above the 180 BPM bound.
    min_lag = math.ceil(fps * 60.0 / 180.0)
    max_lag = int(fps * 60.0 / 60.0)

    best_lag = None
    best_corr = -1.0
    for lag in range(min_lag, min(max_lag, len(centered) // 2)):
        corr = float(centered[:-lag] @ centered[lag:]) / norm
        if corr > best_corr:
            best_corr = corr
            best_lag = lag

    if best_lag is not None and best_corr > 0.20:
        est_bpm = round(60.0 * fps / best_lag, 1)
        conf = min(0.85, round(best_corr * 1.2, 2))
        return est_bpm, conf, "autocorrelation_lowband"

    return 120.0, 0.30, "default_fallback_unverified"


def _analyze_kick_pattern(
    kick_peaks: list[int], duration_sec: float, bpm: float | None
) -> dict[str, Any]:
    """Analyze kick frequency, spacing regularity, and four-on-the-floor characteristic."""
    count = len(kick_peaks)
    if count == 0:
        return {
            "hit_count": 0,
            "hits_per_bar": 0.0,
            "interval_summary": "キック検出なし（ブレイクまたは上物区間）",
            "is_four_on_the_floor": False,
            "regularity": 0.0,
            "confidence": 0.70,
            "method": "low_band_peak_spacing",
        }

    tempo = bpm or 120.0
    sec_per_beat = 60.0 / tempo
    sec_per_bar = sec_per_beat * 4.0
    estimated_bars = max(1.0, duration_sec / sec_per_bar)
    hits_per_bar = round(count / estimated_bars, 2)

    fps = RATE / HOP
    peak_times = [p / fps for p in kick_peaks]
    intervals = [peak_times[i] - peak_times[i - 1] for i in range(1, len(peak_times))]

    four_floor_match = False
    regularity = 0.0
    if intervals:
        devs = [abs(iv - sec_per_beat) for iv in intervals]
        avg_dev = float(np.mean(devs))
        regularity = max(0.0, min(1.0, round(1.0 - (avg_dev / sec_per_beat), 2)))
        four_floor_match = bool(regularity >= 0.75 and 3.4 <= hits_per_bar <= 4.6)

    summary = (
        "安定した4つ打ちキック（4 beats/bar）"
        if four_floor_match
        else f"変則/シンコペーション キック（約 {hits_per_bar} hits/bar）"
    )

    return {
        "hit_count": count,
        "hits_per_bar": hits_per_bar,
        "interval_summary": summary,
        "is_four_on_the_floor": four_floor_match,
        "regularity": regularity,
        "confidence": 0.85 if four_floor_match else 0.65,
        "method": "low_band_peak_spacing",
    }


def _analyze_bass_groove(
    bass_peaks: list[int],
    kick_peaks: list[int],
    norm_bass: np.ndarray,
    duration_sec: float,
    bpm: float | None,
) -> dict[str, Any]:
    """Analyze bass note density, rest ratio, syncopation against kick, and accent positions."""
    fps = RATE / HOP
    tempo = bpm or 120.0
    sec_per_bar = (60.0 / tempo) * 4.0
    estimated_bars = max(1.0, duration_sec / sec_per_bar)

    count = len(bass_peaks)
    density = round(count / estimated_bars, 2)

    below_thresh = np.sum(norm_bass < 0.15)
    rest_ratio = round(float(below_thresh) / max(1, len(norm_bass)), 2)

    syncopation_hits = 0
    kick_times = [k / fps for k in kick_peaks]
    for b_idx in bass_peaks:
        b_time = b_idx / fps
        min_kick_dist = min([abs(b_time - kt) for kt in kick_times], default=1.0)
        if min_kick_dist > (15.0 / tempo):
            syncopation_hits += 1

    syncopation_ratio = round(syncopation_hits / max(1, count), 2)

    accents: list[str] = []
    if bass_peaks:
        peak_vals = [norm_bass[p] for p in bass_peaks]
        thresh_accent = np.percentile(peak_vals, 75)
        for p, val in zip(bass_peaks, peak_vals, strict=False):
            if val >= thresh_accent:
                time_in_bar = (p / fps) % sec_per_bar
                step_16th = int((time_in_bar / sec_per_bar) * 16) + 1
                accents.append(f"Step {step_16th}")

    return {
        "hit_count": count,
        "density_notes_per_bar": density,
        "rest_ratio": rest_ratio,
        "syncopation_ratio": syncopation_ratio,
        "accent_positions": accents[:8],
        "confidence": 0.78 if count > 0 else 0.50,
        "method": "mid_low_band_transient_segmentation",
    }


def _analyze_energy_dynamics(total_energy: np.ndarray, duration_sec: float) -> dict[str, Any]:
    """Measure energy trend and dynamic contrast across the segment."""
    if len(total_energy) < 4:
        return {
            "trend": "stable",
            "dynamic_range_db": 0.0,
            "confidence": 0.30,
            "method": "insufficient_data",
        }

    q_len = len(total_energy) // 4
    first_quarter = float(np.mean(total_energy[:q_len]))
    last_quarter = float(np.mean(total_energy[-q_len:]))

    ratio = last_quarter / max(1e-6, first_quarter)
    if ratio >= 1.35:
        trend = "building"
    elif ratio <= 0.70:
        trend = "dropping"
    else:
        trend = "stable"

    p_max = float(np.max(total_energy))
    p_min = float(np.percentile(total_energy, 10))
    dyn_range = round(20.0 * math.log10(max(1e-5, p_max) / max(1e-5, p_min)), 1)

    return {
        "trend": trend,
        "start_to_end_ratio": round(ratio, 2),
        "dynamic_range_db": dyn_range,
        "confidence": 0.80,
        "method": "rms_windowed_energy_slope",
    }


def _build_theory_interpretation(
    kick: dict[str, Any], bass: dict[str, Any], energy: dict[str, Any], bpm: float | None
) -> dict[str, Any]:
    """Generate musically grounded explanations based on rhythm & arrangement theory."""
    lines: list[str] = []

    if kick["is_four_on_the_floor"]:
        lines.append(
            f"キックの周期性が高く、4つ打ち判定（約 {kick['hits_per_bar']} hits/bar）があります。"
            "拍節の基準を作る傾向としての候補解釈です。"
        )
    else:
        lines.append(
            f"キックは毎小節約 {kick['hits_per_bar']} 回で変則的に検出されています。"
            "拍節の感じ方は他パートや聴感との照合が必要な候補解釈です。"
        )

    sync_ratio = bass["syncopation_ratio"]
    rest_ratio = bass["rest_ratio"]
    density = bass["density_notes_per_bar"]

    if sync_ratio >= 0.50:
        lines.append(
            f"低中域ピークの約 {int(sync_ratio * 100)}% がキックと非同時の発音で、密度は小節あたり約 {density:.1f} 音です。"
            "拍との対比や帯域分離の可能性を示す候補解釈であり、マスキング回避の意図そのものを断定するものではありません。"
        )
    else:
        lines.append(
            "低中域ピークがキックと同時発音となる割合が比較的高く、拍の補強傾向が候補として考えられます。"
            "低音パンチを狙った設計かは聴感照合が必要です。"
        )

    if rest_ratio >= 0.40:
        lines.append(
            f"低中域の休符比率代理指標は {int(rest_ratio * 100)}% です。"
            "低中域エネルギー閾値に基づく代理指標であり、無音区間やリバーブ、他楽器の鳴り方を直接検出した結果ではありません（空間性の候補解釈）。"
        )
    else:
        lines.append(
            f"低中域の休符比率代理指標は {int(rest_ratio * 100)}% と低めで、持続的な低音配置傾向を示しています（音価やドライブ感の断定ではありません）。"
        )

    if energy["trend"] == "building":
        lines.append(
            f"区間後半に向けてエネルギーが上昇（{energy['trend']}）しています。"
            "上昇は測定値ですが、ビルドアップやクレッシェンド等のセクション解釈は候補です。"
        )
    elif energy["trend"] == "dropping":
        lines.append(
            "区間後半に向けてエネルギーが減少しています。"
            "減少は測定値ですが、ブレイクダウン等のセクション移行解釈は候補です。"
        )

    return {
        "summary": "キックとベースの帯域分離と休符傾向に基づくグルーヴ候補解釈",
        "detailed_explanations": lines,
        "theoretical_focus": "Rhythmic Counterpoint & Low-end Frequency Management",
        "status": "unverified",
    }


def _derive_generation_rules(
    kick: dict[str, Any], bass: dict[str, Any], energy: dict[str, Any], bpm: float | None
) -> dict[str, Any]:
    """Derive concrete, abstract production rules for candidate generation without copying raw notes."""
    raw_density = bass.get("density_notes_per_bar", 4.0)
    target_density = "sparse" if raw_density < 4.0 else ("dense" if raw_density > 8.0 else "normal")

    sync_val = min(0.9, max(0.2, bass.get("syncopation_ratio", 0.5)))
    rest_val = min(0.8, max(0.1, bass.get("rest_ratio", 0.4)))

    return {
        "bpm_target": bpm or 120.0,
        "kick_style": "four_on_the_floor" if kick.get("is_four_on_the_floor") else "broken_beat",
        "kick_density_per_bar": float(kick.get("hits_per_bar", 4.0) or 4.0),
        "bass_density_profile": target_density,
        "bass_density_value": raw_density,
        "bass_syncopation": sync_val,
        "bass_kick_separation_ratio": sync_val,
        "bass_rest_ratio": rest_val,
        "kick_bass_interlock": "avoid_collision"
        if bass.get("syncopation_ratio", 0.5) >= 0.45
        else "reinforce_punch",
        "dynamic_profile": energy.get("trend", "stable"),
        "preserve_space": rest_val >= 0.35,
    }


def _empty_or_failed_analysis(path: Path, reason: str) -> dict[str, Any]:
    """Safe fallback when audio is insufficient or invalid."""
    return {
        "ok": False,
        "schema_version": "2.0.0",
        "analysis_scope": "unknown",
        "error": reason,
        "audio_file": path.name,
        "segment": {"start_seconds": 0.0, "duration_seconds": 0.0, "sample_rate": RATE},
        "execution_stats": {
            "reached_end": False,
            "elapsed_seconds": 0.0,
            "analyzed_duration_seconds": 0.0,
            "total_duration_seconds": 0.0,
            "chunk_count": 0,
            "scope": "unknown",
        },
        "bpm": {"value": None, "confidence": 0.0, "unit": "BPM", "method": "none", "status": "unknown"},
        "kick_pattern": {"hit_count": 0, "interval_summary": "unknown", "confidence": 0.0, "method": "none"},
        "bass_groove": {"hit_count": 0, "rest_ratio": 0.0, "confidence": 0.0, "method": "none"},
        "energy_dynamics": {"trend": "unknown", "confidence": 0.0, "method": "none"},
        "section": {"candidate": "unknown", "confidence": 0.0, "status": "unknown", "reason": reason},
        "sections": [],
        "harmonic_analysis": {"key": "unknown", "confidence": 0.0, "status": "unknown", "reason": reason},
        "harmonic_timeline": [],
        "chord_timeline": [],
        "timeline": [],
        "theory_interpretation": {"summary": "解析失敗", "detailed_explanations": [reason]},
        "theory_timeline": [],
        "generation_rules": {},
    }


analyze_reference_groove = analyze_groove
