"""Render a KIHACHI MIDI Lead track with one neutral tone for phrase comparison."""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import soundfile as sf

TICKS_PER_BEAT = 480
SAMPLE_RATE = 22_050


def _variable(data: bytes, cursor: int) -> tuple[int, int]:
    value = 0
    for _ in range(4):
        byte = data[cursor]
        cursor += 1
        value = (value << 7) | (byte & 0x7F)
        if not byte & 0x80:
            return value, cursor
    raise ValueError("Invalid MIDI variable length")


def read_tracks(path: Path) -> tuple[int, dict[str, list[tuple[int, int, int, int]]]]:
    """Return tempo and named note tracks from a KIHACHI SMF."""
    data = path.read_bytes()
    if data[:4] != b"MThd" or int.from_bytes(data[12:14], "big") != TICKS_PER_BEAT:
        raise ValueError("Expected a KIHACHI MIDI file at 480 ticks per beat")
    cursor = 14
    tempo = 500_000
    tracks: dict[str, list[tuple[int, int, int, int]]] = {}
    while cursor < len(data):
        if data[cursor:cursor + 4] != b"MTrk":
            raise ValueError("Invalid MIDI track")
        length = int.from_bytes(data[cursor + 4:cursor + 8], "big")
        track = data[cursor + 8:cursor + 8 + length]
        cursor += 8 + length
        offset = tick = 0
        name = ""
        active: dict[int, tuple[int, int]] = {}
        notes: list[tuple[int, int, int, int]] = []
        while offset < len(track):
            delta, offset = _variable(track, offset)
            tick += delta
            status = track[offset]
            offset += 1
            if status == 0xFF:
                kind = track[offset]
                offset += 1
                size, offset = _variable(track, offset)
                payload = track[offset:offset + size]
                offset += size
                if kind == 0x03:
                    name = payload.decode("ascii", "replace")
                elif kind == 0x51 and size == 3:
                    tempo = int.from_bytes(payload, "big")
                elif kind == 0x2F:
                    break
            elif status in (0x80, 0x90):
                pitch, velocity = track[offset:offset + 2]
                offset += 2
                if status == 0x90 and velocity:
                    active[pitch] = (tick, velocity)
                elif pitch in active:
                    start, original_velocity = active.pop(pitch)
                    notes.append((start, tick, pitch, original_velocity))
            else:
                raise ValueError(f"Unsupported MIDI event: {status:#x}")
        tracks[name] = notes
    return tempo, tracks


def read_lead(path: Path) -> tuple[int, list[tuple[int, int, int, int]]]:
    """Return tempo and (start tick, end tick, pitch, velocity) for Lead."""
    tempo, tracks = read_tracks(path)
    return tempo, tracks.get("Lead", [])


def render(path: Path, destination: Path, start_bar: int = 17, bars: int = 8) -> int:
    """Make a fresh mono WAV; returns the number of Lead notes heard."""
    if destination.exists():
        raise FileExistsError(destination)
    tempo, notes = read_lead(path)
    start_tick = (start_bar - 1) * 4 * TICKS_PER_BEAT
    end_tick = start_tick + bars * 4 * TICKS_PER_BEAT
    seconds_per_tick = tempo / 1_000_000 / TICKS_PER_BEAT
    audio = np.zeros(round((end_tick - start_tick) * seconds_per_tick * SAMPLE_RATE), dtype=np.float32)
    heard = 0
    for start, end, pitch, velocity in notes:
        if start >= end_tick or end <= start_tick:
            continue
        first = max(0, round((max(start, start_tick) - start_tick) * seconds_per_tick * SAMPLE_RATE))
        last = min(len(audio), round((min(end, end_tick) - start_tick) * seconds_per_tick * SAMPLE_RATE))
        count = last - first
        if count <= 0:
            continue
        t = np.arange(count, dtype=np.float32) / SAMPLE_RATE
        hz = 440.0 * 2 ** ((pitch - 69) / 12)
        tone = np.sin(2 * np.pi * hz * t) + 0.22 * np.sin(4 * np.pi * hz * t)
        envelope = np.minimum(1.0, t / 0.012) * np.minimum(1.0, (count / SAMPLE_RATE - t) / 0.05)
        audio[first:last] += tone * envelope * (velocity / 127) * 0.12
        heard += 1
    if not heard:
        raise ValueError("No Lead notes in the selected bars")
    peak = float(np.max(np.abs(audio)))
    if peak > 0.9:
        audio *= 0.9 / peak
    destination.parent.mkdir(parents=True, exist_ok=True)
    sf.write(destination, audio, SAMPLE_RATE, subtype="PCM_16")
    return heard


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("midi", type=Path)
    parser.add_argument("wav", type=Path)
    parser.add_argument("--start-bar", type=int, default=17)
    parser.add_argument("--bars", type=int, default=8)
    args = parser.parse_args()
    count = render(args.midi, args.wav, args.start_bar, args.bars)
    print(f"Rendered {count} Lead notes to {args.wav}")


if __name__ == "__main__":
    main()
