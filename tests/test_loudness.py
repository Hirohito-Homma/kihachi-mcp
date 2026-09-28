import math

import numpy as np
import soundfile

from kihachi_mcp.services.loudness import Section, measure


def _write(tmp_path, signal, rate=48000):
    path = tmp_path / "mix.wav"
    soundfile.write(str(path), signal, rate, subtype="FLOAT")
    return path


def test_a_full_scale_1khz_sine_in_one_channel_reads_minus_3_lufs(tmp_path) -> None:
    """BS.1770: a 0 dBFS 1 kHz sine in one channel measures -3.01 LKFS."""
    rate = 48000
    t = np.arange(rate * 10) / rate
    tone = np.sin(2 * math.pi * 1000 * t)
    report = measure(_write(tmp_path, np.column_stack([tone, np.zeros_like(tone)]), rate))
    assert abs(report["integrated_lufs"] - (-3.0)) <= 0.1
    assert abs(report["true_peak_dbtp"]) <= 0.1


def test_the_standard_coefficients_come_out_at_48k() -> None:
    from kihachi_mcp.services.loudness import _biquads

    (shelf_b, shelf_a), (_, pass_a) = _biquads(48000)
    assert np.allclose(shelf_b, [1.53512485958697, -2.69169618940638, 1.19839281085285])
    assert np.allclose(shelf_a, [1.0, -1.69065929318241, 0.73248077421585])
    assert np.allclose(pass_a, [1.0, -1.99004745483398, 0.99007225036621])


def test_a_quieter_section_measures_quieter(tmp_path) -> None:
    rate = 48000
    tempo = 120.0  # one bar is 2 seconds
    t = np.arange(rate * 16) / rate
    tone = np.sin(2 * math.pi * 440 * t) * np.where(t < 8, 0.1, 0.5)
    report = measure(
        _write(tmp_path, np.column_stack([tone, tone]), rate),
        sections=[Section("Intro", 1, 4), Section("Drop", 5, 4)],
        tempo=tempo,
    )
    intro, drop = report["sections"]
    assert abs((drop["lufs"] - intro["lufs"]) - 20 * math.log10(5)) <= 0.2
    assert report["verdict"]


def test_low_end_share_rises_with_a_sub_tone(tmp_path) -> None:
    rate = 48000
    t = np.arange(rate * 5) / rate
    high = np.sin(2 * math.pi * 2000 * t) * 0.3
    mixed = high + np.sin(2 * math.pi * 50 * t) * 0.3
    bright = measure(_write(tmp_path, np.column_stack([high, high]), rate))
    heavy = measure(_write(tmp_path, np.column_stack([mixed, mixed]), rate))
    assert heavy["low_end_share_db"] > bright["low_end_share_db"] + 20
