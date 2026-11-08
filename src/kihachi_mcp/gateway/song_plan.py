"""Song plan for one confirmed Ableton write. Building it does not touch Live."""

from __future__ import annotations

import hashlib
import json

from kihachi_mcp.gateway.samples.rank import sample_requirements
from kihachi_mcp.gateway.song_patterns import (
    ROLES,
    generate_role_notes,
    harmonic_pitch,
    section_plan,
)
from kihachi_mcp.gateway.song_time import (
    BEATS_PER_BAR,
    MAX_NOTES_PER_CLIP,
    validate_note,
)
from kihachi_mcp.gateway.sound import sound_plan
from kihachi_mcp.models.songspec import SongSpec

PRESET_STYLE = "mutation-funk-dub-tech-house"
PRESET_KEY = "D# minor"
DEFAULT_TITLE = "KIHACHI MANUS TEST 01"
ALLOWED_BARS = (32, 64, 128)
_HARMONIC_ROLES = frozenset(
    {"SLAP BASS", "SUB BASS", "DUB CHORDS", "SYNTH STABS", "ARP", "VOCODER CARRIER", "VOCAL", "FX"}
)

# Live's mixer volume is 0.0–1.0. About 0.85 is unity. These sit under that.
VOLUME: dict[str, float] = {
    "KICK": 0.80,
    "DRUMS": 0.72,
    "PERCUSSION": 0.62,
    "SLAP BASS": 0.74,
    "SUB BASS": 0.70,
    "DUB CHORDS": 0.64,
    "SYNTH STABS": 0.60,
    "ARP": 0.58,
    "VOCODER CARRIER": 0.55,
    "VOCAL": 0.55,
    "FX": 0.48,
}
PAN: dict[str, float] = {
    "KICK": 0.0,
    "DRUMS": -0.04,
    "PERCUSSION": 0.18,
    "SLAP BASS": -0.08,
    "SUB BASS": 0.0,
    "DUB CHORDS": 0.22,
    "SYNTH STABS": -0.20,
    "ARP": 0.28,
    "VOCODER CARRIER": 0.0,
    "VOCAL": 0.06,
    "FX": 0.35,
}


def track_name(title: str, role: str) -> str:
    """Exact Live name. The separator is not a slash, so error text stays readable."""
    return f"{title} - {role}"


def build_song_plan(
    *,
    tempo: object = 110,
    key: object = PRESET_KEY,
    bars: object = 32,
    style: object = PRESET_STYLE,
    title: object = DEFAULT_TITLE,
    swing: object = 0.54,
    seed: object = 1,
) -> dict[str, object]:
    """Validate the preset and render patterns. This function does not call Live."""
    checked_title = _title(title)
    checked_tempo = _tempo(tempo)
    checked_key = _key(key)
    checked_style = _style(style)
    checked_bars = _bars(bars)
    checked_swing = _swing(swing)
    checked_seed = _seed(seed)
    length_beats = checked_bars * BEATS_PER_BAR
    notes_by_role: dict[str, list[dict[str, float | int]]] = {}
    for role in ROLES:
        rendered = generate_role_notes(role, checked_bars, checked_swing, checked_seed)
        if not rendered or len(rendered) > MAX_NOTES_PER_CLIP:
            raise ValueError(f"{role} のノート数がクリップに置けません")
        for note in rendered:
            validate_note(note, length_beats)
            if role in _HARMONIC_ROLES and not harmonic_pitch(int(note["pitch"])):
                raise ValueError(f"{role} が D# minor から外れています")
        notes_by_role[role] = rendered
    names = [track_name(checked_title, role) for role in ROLES]
    requirements = sample_requirements(checked_tempo, checked_key)
    spec = SongSpec.from_dict(
        {
            "genre": checked_style,
            "tempo": checked_tempo,
            "key": checked_key,
            "bars": checked_bars,
            "length_minutes": length_beats / checked_tempo / 60.0,
            "tracks": names,
        }
    )
    identity = {
        "title": checked_title,
        "tempo": checked_tempo,
        "key": checked_key,
        "bars": checked_bars,
        "style": checked_style,
        "swing": checked_swing,
        "seed": checked_seed,
    }
    plan_id = hashlib.sha256(json.dumps(identity, sort_keys=True).encode("utf-8")).hexdigest()[:16]
    clips = [
        {
            "track": track_name(checked_title, role),
            "clip": track_name(checked_title, role),
            "session_slot": 0,
            "arrangement_start_bar": 1,
            "arrangement_start_beats": 0.0,
            "length_bars": checked_bars,
            "length_beats": length_beats,
            "note_count": len(notes_by_role[role]),
        }
        for role in ROLES
    ]
    songspec = spec.to_dict()
    songspec["sample_requirements"] = requirements
    return {
        "plan_id": plan_id,
        "songspec": songspec,
        "sample_requirements": requirements,
        "sound_plan": sound_plan(),
        "song": {
            **identity,
            "signature": "4/4",
            "character": ["energetic", "deep", "funky", "spacey", "club-oriented"],
            "length_beats": length_beats,
            "tempo_scope": "set",
        },
        "timebase": {
            "unit": "beats",
            "beats_per_bar": BEATS_PER_BAR,
            "bar_numbering": "1-based; bar 1 starts at beat 0",
            "source": (
                "AbletonGPT create_midi_clip start_time/duration and "
                "copy_session_clip_to_arrangement destination_time_beats"
            ),
        },
        "tracks": [
            {
                "name": track_name(checked_title, role),
                "role": role,
                "note_count": len(notes_by_role[role]),
                "volume": VOLUME[role],
                "pan": PAN[role],
            }
            for role in ROLES
        ],
        "arrangement": section_plan(checked_bars),
        "clips": clips,
        "note_counts": {role: len(notes_by_role[role]) for role in ROLES},
        "total_notes": sum(len(notes_by_role[role]) for role in ROLES),
        "devices": {
            "policy": "midi_only",
            "vocoder": "VOCODER CARRIER は MIDI だけ置く。インストゥルメントは自動ロードしない",
        },
        "length_beats": length_beats,
        "notes_by_role": notes_by_role,
    }


def public_view(plan: dict[str, object]) -> dict[str, object]:
    """Drop the full note lists. The preview is enough to read the plan."""
    notes = plan.get("notes_by_role")
    view = {key: value for key, value in plan.items() if key != "notes_by_role"}
    previews: dict[str, list[object]] = {}
    if isinstance(notes, dict):
        for role, role_notes in notes.items():
            if isinstance(role_notes, list):
                previews[str(role)] = role_notes[:3]
    view["previews"] = previews
    return view


def _title(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("title は文字列です")
    title = value.strip()
    if not title or len(title) > 48:
        raise ValueError("title は 1 から 48 文字です")
    if "/" in title or "\\" in title:
        raise ValueError("title にパス区切りは使えません")
    return title


def _tempo(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("tempo は数値です")
    if abs(float(value) - round(float(value))) > 1e-6:
        raise ValueError("tempo は整数の BPM です")
    tempo = round(float(value))
    if tempo < 20 or tempo > 999:
        raise ValueError("tempo は 20 から 999 です")
    return tempo


def _key(value: object) -> str:
    if value != PRESET_KEY:
        raise ValueError("この vertical slice のキーは D# minor だけです")
    return PRESET_KEY


def _style(value: object) -> str:
    if value != PRESET_STYLE:
        raise ValueError("この vertical slice の style は mutation-funk-dub-tech-house だけです")
    return PRESET_STYLE


def _bars(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value not in ALLOWED_BARS:
        raise ValueError("bars は 32、64、128 のいずれかです")
    return value


def _swing(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("swing は数値です")
    swing = float(value)
    if swing < 0.5 or swing > 0.66:
        raise ValueError("swing は 0.50 から 0.66 です")
    return round(swing, 4)


def _seed(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > 1_000_000:
        raise ValueError("seed は 0 から 1000000 の整数です")
    return value
