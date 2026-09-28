"""Local, versioned measurements; no inference service or model downloads."""

from __future__ import annotations

import json
import math
import shutil
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np

VERSION = "reference-dsp/1"
RATE = 22050
WINDOW = 2048
HOP = 512
MAX_SECONDS = 1800
EXTENSIONS = {".wav", ".mp3", ".flac", ".aif", ".aiff", ".m4a", ".ogg"}


class ReferenceError(ValueError):
    """A safe, user-facing failure (never includes subprocess stderr)."""


def capabilities() -> dict:
    return {
        "ffmpeg": bool(shutil.which("ffmpeg")),
        "ffprobe": bool(shutil.which("ffprobe")),
        "analysis_version": VERSION,
        "max_seconds": MAX_SECONDS,
    }


def _run(args: list[str], timeout: int = 240) -> subprocess.CompletedProcess:
    try:
        result = subprocess.run(args, capture_output=True, timeout=timeout, check=False)
    except FileNotFoundError:
        raise ReferenceError(
            "FFmpeg / ffprobe が必要です。自動インストールはしません"
        ) from None
    except subprocess.TimeoutExpired:
        raise ReferenceError(
            "解析が時間上限に達しました。短い音源で試してください"
        ) from None
    if result.returncode:
        raise ReferenceError(
            "音源を読み取れません。形式・破損・保護の有無を確認してください"
        )
    return result


def _finite(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return round(number, 5) if math.isfinite(number) else None


def tempo_candidates(onset: np.ndarray, seconds: float) -> list[dict]:
    """Autocorrelation candidates, not probabilities or confirmed tempos."""
    if seconds < 8 or len(onset) < 8 or float(np.max(onset)) < 1e-6:
        return []
    centered = onset - onset.mean()
    norm = float(centered @ centered)
    if norm < 1e-8:
        return []
    fps = RATE / HOP
    low, high = math.ceil(fps * 60 / 200), math.floor(fps * 60 / 60)
    scores = {}
    for lag in range(low - 1, high + 2):
        if lag >= len(centered) // 2:
            continue
        left, right = centered[:-lag], centered[lag:]
        denom = float(np.linalg.norm(left) * np.linalg.norm(right))
        scores[lag] = float(left @ right) / denom if denom else 0.0
    peaks = [
        (score, lag)
        for lag, score in scores.items()
        if low <= lag <= high
        and score >= 0.25
        and score >= scores.get(lag - 1, 0)
        and score >= scores.get(lag + 1, 0)
    ]
    return [
        {"bpm": round(60 * fps / lag, 1), "periodicity": round(score, 3)}
        for score, lag in sorted(peaks, reverse=True)[:3]
    ]


def analyze(path: Path) -> dict:
    """Measure a managed copy. Original files are never opened for writing."""
    if path.suffix.lower() not in EXTENSIONS:
        raise ReferenceError("対応する音声ファイルを選んでください")
    probe = _run(
        [
            "ffprobe",
            "-v",
            "error",
            "-protocol_whitelist",
            "file,pipe",
            "-select_streams",
            "a:0",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(path),
        ]
    )
    try:
        metadata = json.loads(probe.stdout)
        stream = metadata["streams"][0]
        seconds = float(metadata["format"].get("duration") or stream["duration"])
        channels = int(stream["channels"])
    except (KeyError, IndexError, ValueError, TypeError):
        raise ReferenceError("音声の長さ・チャンネル情報を取得できません") from None
    if not math.isfinite(seconds) or not 0 < seconds <= MAX_SECONDS:
        raise ReferenceError("音源は0秒より長く、30分以内にしてください")
    if channels not in {1, 2}:
        raise ReferenceError("現在はモノラル／ステレオ音源に対応しています")
    base = [
        "ffmpeg",
        "-nostdin",
        "-hide_banner",
        "-v",
        "info",
        "-protocol_whitelist",
        "file,pipe",
        "-i",
        str(path),
        "-map",
        "0:a:0",
        "-t",
        str(MAX_SECONDS),
        "-vn",
    ]
    with TemporaryDirectory(prefix="kihachi-dsp-") as directory:
        raw = Path(directory) / "audio.f32"
        _run([*base, "-ar", str(RATE), "-acodec", "pcm_f32le", "-f", "f32le", str(raw)])
        if raw.stat().st_size < channels * 4:
            raise ReferenceError("音源に解析可能なサンプルがありません")
        samples = np.memmap(raw, dtype="<f4", mode="r").reshape(-1, channels)
        try:
            spectral = _measure(samples)
        finally:
            del samples
    loudness = _run(
        [
            *base,
            "-af",
            "loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json",
            "-f",
            "null",
            "-",
        ]
    )
    log = loudness.stderr.decode("utf-8", errors="replace")
    try:
        values = json.loads(log[log.rfind("{") : log.rfind("}") + 1])
    except (ValueError, TypeError):
        raise ReferenceError("ラウドネスの解析結果を取得できません") from None
    metrics = spectral.pop("metrics")
    metrics.update(
        integrated_lufs=_finite(values.get("input_i")),
        true_peak_dbtp=_finite(values.get("input_tp")),
    )
    warnings = [
        "波形・スペクトル等の信号分析ベース。音楽的品質や推奨音量ではありません",
        "BPMは60〜200の候補です。倍・半分の曖昧さがあり、確率ではありません",
        "帯域比率は22.05kHzに変換した信号の20Hz〜10kHz内の値です",
    ]
    if seconds < 3:
        metrics["integrated_lufs"] = None
        warnings.append("3秒未満はLUFS比較対象外です")
    if metrics["rms_dbfs"] is None:
        metrics["integrated_lufs"] = None
        warnings.append("無音のため、BPM・音量・帯域比率を確定できません")
    return {
        "version": VERSION,
        "basis": "signal_analysis",
        "duration_seconds": seconds,
        "sample_rate": int(stream["sample_rate"]),
        "channels": channels,
        "codec": stream.get("codec_name", ""),
        "ffmpeg_version": _run(["ffmpeg", "-version"]).stdout.decode().splitlines()[0],
        "settings": {
            "analysis_rate": RATE,
            "fft_window": WINDOW,
            "hop": HOP,
            "band_edges_hz": [20, 250, 2000, 10000],
            "segment_seconds": 10,
            "min_relative_spectral_flux": 0.02,
        },
        "metrics": metrics,
        **spectral,
        "warnings": warnings,
    }


def _measure(samples: np.ndarray) -> dict:
    length, channels = samples.shape
    window = np.hanning(WINDOW)
    freq = np.fft.rfftfreq(WINDOW, 1 / RATE)
    masks = [
        (freq >= lo) & (freq < hi) for lo, hi in [(20, 250), (250, 2000), (2000, 10000)]
    ]
    bands = np.zeros(3)
    total_power = centroid_sum = sum_sq = sum_lr = 0.0
    sum_channels, square_channels = np.zeros(channels), np.zeros(channels)
    previous = None
    onset, segments = [], []
    # Bounded memory: process short windows, never allocate a full-song STFT.
    for start in range(0, length, RATE * 10):
        chunk = np.asarray(samples[start : start + RATE * 10], dtype=np.float64)
        if not np.isfinite(chunk).all():
            raise ReferenceError("音源に非有限値が含まれています")
        energy = float(np.square(chunk).sum())
        sum_sq += energy
        sum_channels += chunk.sum(axis=0)
        square_channels += np.square(chunk).sum(axis=0)
        if channels == 2:
            sum_lr += float((chunk[:, 0] * chunk[:, 1]).sum())
        rms = math.sqrt(energy / chunk.size)
        segments.append(
            {
                "start_seconds": start / RATE,
                "end_seconds": (start + len(chunk)) / RATE,
                "rms_dbfs": round(20 * math.log10(rms), 3) if rms else None,
            }
        )
    for start in range(0, max(1, length - WINDOW + 1), HOP):
        frame = np.asarray(samples[start : start + WINDOW], dtype=np.float64)
        if len(frame) < WINDOW:
            frame = np.pad(frame, ((0, WINDOW - len(frame)), (0, 0)))
        power = np.square(np.abs(np.fft.rfft(frame * window[:, None], axis=0))).mean(
            axis=1
        )
        bands += np.array([power[mask].sum() for mask in masks])
        total_power += float(power.sum())
        centroid_sum += float(power @ freq)
        magnitude = np.sqrt(power)
        flux = (
            float(np.maximum(magnitude - previous, 0).sum())
            if previous is not None
            else 0
        )
        # A steady tone's small window/phase leakage can be highly periodic.
        # Require a material relative spectral change before treating it as onset.
        magnitude_sum = float(magnitude.sum())
        onset.append(
            flux if magnitude_sum > 1e-8 and flux / magnitude_sum > 0.02 else 0
        )
        previous = magnitude
    rms = math.sqrt(sum_sq / (length * channels))
    corr = None
    if channels == 2:
        variance = np.maximum(square_channels - sum_channels**2 / length, 0)
        denom = float(np.sqrt(variance.prod()))
        if denom > 1e-12:
            corr = round(
                float(np.clip((sum_lr - sum_channels.prod() / length) / denom, -1, 1)),
                5,
            )
    band_total = float(bands.sum())
    metrics = {
        "rms_dbfs": round(20 * math.log10(rms), 5) if rms else None,
        "spectral_centroid_hz": round(centroid_sum / total_power, 2)
        if total_power
        else None,
        "stereo_correlation": corr,
    }
    metrics.update(
        {
            name: round(float(value / band_total), 5) if band_total else None
            for name, value in zip(("low_ratio", "mid_ratio", "high_ratio"), bands)
        }
    )
    return {
        "metrics": metrics,
        "tempo_candidates": tempo_candidates(np.array(onset), length / RATE),
        "segments": segments,
    }
