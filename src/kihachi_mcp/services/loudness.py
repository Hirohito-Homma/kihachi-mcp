"""Measure an exported mix: loudness (ITU-R BS.1770-4 / EBU R128) and true peak.

Live's object model cannot render or meter LUFS, so the user exports the song
(File > Export Audio) and this reads the file. Nothing here touches Live.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import soundfile
from scipy.signal import butter, lfilter, resample_poly, sosfilt

#: Club target: loud but with transients left, and 1 dB for lossy encoding.
CLUB_INTEGRATED_LUFS = (-8.0, -6.0)
CLUB_TRUE_PEAK_DBTP = -1.0
LOW_END_HZ = 120.0

_BLOCK_SECONDS = 0.4
_SHORT_TERM_SECONDS = 3.0
_ABSOLUTE_GATE = -70.0


@dataclass(frozen=True)
class Section:
    name: str
    start_bar: int
    bars: int


def _biquads(rate: int) -> list[tuple[np.ndarray, np.ndarray]]:
    """The two K-weighting stages of BS.1770, designed for ``rate``.

    The design and constants are libebur128's, which reproduce the standard's
    48 kHz coefficients and extend them to other sample rates.
    """
    shelf_hz, gain_db, shelf_q = 1681.974450955533, 3.999843853973347, 0.7071752369554196
    k = math.tan(math.pi * shelf_hz / rate)
    vh = 10 ** (gain_db / 20)
    vb = vh**0.4996667741545416
    a0 = 1 + k / shelf_q + k * k
    shelf_b = np.array([vh + vb * k / shelf_q + k * k, 2 * (k * k - vh), vh - vb * k / shelf_q + k * k]) / a0
    shelf_a = np.array([1.0, 2 * (k * k - 1) / a0, (1 - k / shelf_q + k * k) / a0])
    pass_hz, pass_q = 38.13547087602444, 0.5003270373238773
    k = math.tan(math.pi * pass_hz / rate)
    a0 = 1 + k / pass_q + k * k
    pass_a = np.array([1.0, 2 * (k * k - 1) / a0, (1 - k / pass_q + k * k) / a0])
    return [(shelf_b, shelf_a), (np.array([1.0, -2.0, 1.0]), pass_a)]


def _block_loudness(weighted: np.ndarray, rate: int, seconds: float, hop: float) -> np.ndarray:
    """Loudness of each window, summed over channels (L and R weigh 1.0)."""
    size, step = round(seconds * rate), round(hop * rate)
    if len(weighted) < size:
        return np.array([])
    squared = np.concatenate([np.zeros((1, weighted.shape[1])), np.cumsum(weighted**2, axis=0)])
    starts = np.arange(0, len(weighted) - size + 1, step)
    power = ((squared[starts + size] - squared[starts]) / size).sum(axis=1)
    with np.errstate(divide="ignore"):
        return -0.691 + 10 * np.log10(power)


def _gated(loudness: np.ndarray, relative: float) -> float:
    kept = loudness[loudness > _ABSOLUTE_GATE]
    if kept.size == 0:
        return -math.inf
    threshold = _power_mean(kept) + relative
    kept = kept[kept > threshold]
    return _power_mean(kept) if kept.size else -math.inf


def _power_mean(loudness: np.ndarray) -> float:
    return float(10 * np.log10(np.mean(10 ** ((loudness + 0.691) / 10))) - 0.691)


def _db(value: float) -> float:
    return round(20 * math.log10(value), 2) if value > 0 else -math.inf


def measure(path: str | Path, sections: list[Section] | None = None, tempo: float = 0.0) -> dict[str, Any]:
    """Integrated/short-term loudness, range, true peak and low-end share of one file."""
    data, rate = soundfile.read(str(path), dtype="float64", always_2d=True)
    if data.shape[0] < rate:
        raise ValueError("音声が1秒未満のため測れません")
    weighted = data
    for b, a in _biquads(rate):
        weighted = lfilter(b, a, weighted, axis=0)

    momentary = _block_loudness(weighted, rate, _BLOCK_SECONDS, _BLOCK_SECONDS / 4)
    integrated = _gated(momentary, -10.0)
    short_term = _block_loudness(weighted, rate, _SHORT_TERM_SECONDS, 1.0)
    audible = short_term[short_term > _ABSOLUTE_GATE]
    loudness_range = 0.0
    if audible.size:
        ranged = audible[audible > _power_mean(audible) - 20.0]
        if ranged.size:
            loudness_range = float(np.percentile(ranged, 95) - np.percentile(ranged, 10))

    true_peak = float(np.max(np.abs(resample_poly(data, 4, 1, axis=0))))
    sample_peak = float(np.max(np.abs(data)))
    low = sosfilt(butter(4, LOW_END_HZ, "lowpass", fs=rate, output="sos"), data, axis=0)
    low_share = float(np.sum(low**2) / max(np.sum(data**2), 1e-20))

    report: dict[str, Any] = {
        "file": Path(path).name,
        "seconds": round(data.shape[0] / rate, 1),
        "sample_rate": rate,
        "channels": data.shape[1],
        "integrated_lufs": round(integrated, 1),
        "short_term_max_lufs": round(float(audible.max()), 1) if audible.size else -math.inf,
        "loudness_range_lu": round(loudness_range, 1),
        "true_peak_dbtp": _db(true_peak),
        "sample_peak_dbfs": _db(sample_peak),
        "plr_db": round(_db(true_peak) - integrated, 1),
        "low_end_share_db": round(10 * math.log10(max(low_share, 1e-12)), 1),
    }
    if sections and tempo > 0:
        bar_seconds = 4 * 60.0 / tempo
        rows = []
        for section in sections:
            start = int((section.start_bar - 1) * bar_seconds * rate)
            end = int((section.start_bar - 1 + section.bars) * bar_seconds * rate)
            window = weighted[start:end]
            if len(window) < _BLOCK_SECONDS * rate:
                continue
            loud = _gated(_block_loudness(window, rate, _BLOCK_SECONDS, _BLOCK_SECONDS / 4), -10.0)
            rows.append(
                {
                    "name": section.name,
                    "bars": f"{section.start_bar}-{section.start_bar + section.bars - 1}",
                    "lufs": round(loud, 1),
                }
            )
        report["sections"] = rows
    report["verdict"] = club_verdict(report)
    return report


def club_verdict(report: dict[str, Any]) -> list[str]:
    """Plain findings against the club target. Says nothing about musical quality."""
    low, high = CLUB_INTEGRATED_LUFS
    integrated = report["integrated_lufs"]
    lines = []
    if integrated < low:
        lines.append(
            f"音圧 {integrated} LUFS はクラブ目安 {low}〜{high} より {round(low - integrated, 1)} dB 小さい。"
            f"Limiter の Input Gain を約 +{round(low - integrated + 1, 1)} dB で目安に入ります（リミッターが効く分、実際はやや少なめに上がります）"
        )
    elif integrated > high:
        lines.append(
            f"音圧 {integrated} LUFS はクラブ目安より {round(integrated - high, 1)} dB 大きく、潰れ気味の可能性があります"
        )
    else:
        lines.append(f"音圧 {integrated} LUFS はクラブ目安 {low}〜{high} の範囲内です")
    peak = report["true_peak_dbtp"]
    if peak > CLUB_TRUE_PEAK_DBTP:
        lines.append(f"True Peak {peak} dBTP が上限 {CLUB_TRUE_PEAK_DBTP} を超えています")
    else:
        lines.append(f"True Peak {peak} dBTP は上限 {CLUB_TRUE_PEAK_DBTP} 以下です")
    if report["plr_db"] < 6:
        lines.append(f"ピークと平均の差 {report['plr_db']} dB は小さく、キックのアタックが潰れている可能性があります")
    return lines


def sections_of(candidate: Any) -> list[Section]:
    """Each section's bar span, from where the candidate's clips start.

    A section runs until the next one starts; names can repeat (two drops).
    """
    starts: dict[int, str] = {}
    end = 1
    for clip in candidate.clips:
        starts.setdefault(clip.start_bar, clip.section_name)
        end = max(end, clip.start_bar + clip.length_bars)
    ordered = sorted(starts.items())
    return [
        Section(name, start, (ordered[index + 1][0] if index + 1 < len(ordered) else end) - start)
        for index, (start, name) in enumerate(ordered)
    ]
