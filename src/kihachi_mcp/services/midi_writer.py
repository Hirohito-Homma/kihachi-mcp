"""Write a Standard MIDI File from a studio candidate. No extra packages."""

from pathlib import Path

from kihachi_mcp.models.midi_candidate import MidiCandidate

TICKS_PER_BEAT = 480


def candidate_to_midi_bytes(candidate: MidiCandidate) -> bytes:
    """Return a Format-1 SMF whose notes match the preview exactly."""
    tempo = int(candidate.brief.tempo.value)
    tracks = [_conductor_track(tempo)]
    for part in candidate.parts:
        events: list[tuple[int, bytes]] = []
        beats = candidate.brief.beats_per_bar
        for clip in candidate.clips_for_part(part):
            offset = (clip.start_bar - 1) * beats
            for note in clip.notes:
                start = _ticks(offset + note.start_beats)
                end = _ticks(offset + note.start_beats + note.duration_beats)
                events.append((start, _note_on(note.pitch, note.velocity)))
                events.append((end, _note_off(note.pitch)))
        events.sort(key=lambda item: (item[0], item[1][0]))
        tracks.append(_track_chunk(part, events))
    header = (
        b"MThd"
        + (6).to_bytes(4, "big")
        + (1).to_bytes(2, "big")
        + len(tracks).to_bytes(2, "big")
        + TICKS_PER_BEAT.to_bytes(2, "big")
    )
    return header + b"".join(tracks)


def write_candidate_midi(candidate: MidiCandidate, path: str | Path) -> Path:
    """Write the candidate to ``path`` and return the resolved location."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(candidate_to_midi_bytes(candidate))
    return destination


def _ticks(beats: float) -> int:
    return max(0, round(beats * TICKS_PER_BEAT))


def _conductor_track(tempo: int) -> bytes:
    microseconds = max(1, int(60_000_000 / tempo))
    events = [(0, b"\xff\x51\x03" + microseconds.to_bytes(3, "big"))]
    return _track_chunk("tempo", events)


def _track_chunk(name: str, events: list[tuple[int, bytes]]) -> bytes:
    payload = _meta_track_name(name)
    cursor = 0
    for tick, message in events:
        payload += _variable_length(tick - cursor) + message
        cursor = tick
    payload += _variable_length(0) + b"\xff\x2f\x00"
    return b"MTrk" + len(payload).to_bytes(4, "big") + payload


def _meta_track_name(name: str) -> bytes:
    encoded = name.encode("ascii", "replace")
    return _variable_length(0) + b"\xff\x03" + _variable_length(len(encoded)) + encoded


def _note_on(pitch: int, velocity: int) -> bytes:
    return bytes((0x90, max(0, min(127, pitch)), max(1, min(127, velocity))))


def _note_off(pitch: int) -> bytes:
    return bytes((0x80, max(0, min(127, pitch)), 0))


def _variable_length(value: int) -> bytes:
    if value < 0:
        raise ValueError("MIDI delta cannot be negative")
    chunks = [value & 0x7F]
    value >>= 7
    while value:
        chunks.append((value & 0x7F) | 0x80)
        value >>= 7
    return bytes(reversed(chunks))
