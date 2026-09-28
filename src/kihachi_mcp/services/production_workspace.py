"""Project summary shared by Studio and the CLI. No Live connection required."""

from __future__ import annotations

from typing import Any

from kihachi_mcp.knowledge.genre_profiles import profile_for
from kihachi_mcp.models.midi_candidate import MidiCandidate
from kihachi_mcp.services.brief_parser import explicit_swing

_PART_LABELS = {
    "Kick": "KICK",
    "Snare": "SNARE / CLAP",
    "Hats": "HATS",
    "OpenHat": "OPEN HAT",
    "Perc": "PERC",
    "Sub": "SUB BASS",
    "Bass": "BASS",
    "Stab": "CHORDS",
    "Pad": "PAD",
    "Arp": "ARP",
    "Guitar": "GUITAR",
    "Horn": "HORN",
    "Lead": "LEAD",
    "Vocal": "VOCAL CHOP",
    "FX": "FX",
}
#: Names that describe what the genre's rules actually write for a part.
_GENRE_LABELS = {
    "mutation_funk": {"Bass": "SLAP BASS", "Stab": "DUB CHORDS", "Lead": "MUTATION SYNTH"},
    "dub_techno": {"Stab": "DUB CHORDS"},
}


def part_label(part: str, genre: str) -> str:
    return _GENRE_LABELS.get(genre, {}).get(part, _PART_LABELS.get(part, part))


def project_view(candidate: MidiCandidate) -> dict[str, Any]:
    """Return SongSpec, arrangement, and track facts for one candidate."""
    brief = candidate.brief
    swing = explicit_swing(brief.original_text)
    if swing is None:
        swing = profile_for(str(brief.genre.value)).swing or 0.5
    sections = []
    densities = []
    for section in brief.sections:
        clips = [clip for clip in candidate.clips if clip.section_name == section.name]
        notes = sum(len(clip.notes) for clip in clips)
        per_bar = notes / max(1, section.length_bars)
        densities.append(per_bar)
        active = []
        for part in candidate.parts:
            if any(clip.part == part and clip.notes for clip in clips):
                active.append(part)
        sections.append(
            {
                "name": section.name,
                "start_bar": section.start_bar,
                "length_bars": section.length_bars,
                "end_bar": section.end_bar,
                "notes": notes,
                "notes_per_bar": round(per_bar, 2),
                "active_tracks": active,
            }
        )
    peak = max(densities) if densities else 1.0
    for section, density in zip(sections, densities, strict=True):
        section["energy"] = round(density / peak, 2) if peak else 0.0
    tracks = []
    for part in candidate.parts:
        clips = candidate.clips_for_part(part)
        tracks.append(
            {
                "part": part,
                "label": part_label(part, str(brief.genre.value)),
                "clips": len(clips),
                "note_count": candidate.note_count(part),
                "sections": [clip.section_name for clip in clips if clip.notes],
            }
        )
    return {
        "candidate_id": candidate.candidate_id,
        "parent_candidate_id": candidate.parent_candidate_id,
        "songspec": {
            "tempo": brief.tempo.value,
            "key": brief.key.value,
            "scale": "minor" if str(brief.key.value).endswith("m") else "major",
            "time_signature": f"{brief.meter_numerator}/{brief.meter_denominator}",
            "genre": brief.genre.value,
            "style": brief.mood.value,
            "bars": brief.bars.value,
            "duration_minutes": round(brief.duration_minutes, 2),
            "swing": swing,
            "provider": brief.provider,
            "model": brief.model,
        },
        "arrangement": {
            "bars": brief.bars.value,
            "sections": sections,
        },
        "tracks": tracks,
        "note_count": candidate.note_count(),
    }


def local_ableton_plan(candidate: MidiCandidate) -> dict[str, Any]:
    """Describe the Ableton plan from the candidate before Live is contacted."""
    view = project_view(candidate)
    operations = [
        f"set tempo {view['songspec']['tempo']}" if True else "",
        "create tracks " + ", ".join(track["label"] for track in view["tracks"]),
        f"create MIDI clips {sum(track['clips'] for track in view['tracks'])}",
        f"insert MIDI notes {view['note_count']}",
        f"create arrangement {view['arrangement']['bars']} bars",
    ]
    return {
        "ok": True,
        "requires_live": False,
        "tempo": view["songspec"]["tempo"],
        "time_signature": view["songspec"]["time_signature"],
        "tracks": len(view["tracks"]),
        "track_names": [track["label"] for track in view["tracks"]],
        "clips": sum(track["clips"] for track in view["tracks"]),
        "midi_notes": view["note_count"],
        "arrangement_bars": view["arrangement"]["bars"],
        "operations": [item for item in operations if item],
        "verification_expectations": [
            "tempo",
            "track count",
            "track names",
            "clip count",
            "note count",
            "arrangement bounds",
        ],
        "musical_quality_claimed": False,
    }


def verification_report(receipt: dict[str, Any] | None) -> dict[str, Any]:
    """Turn a Live receipt into PASS/FAIL lines. Success is read-back, not send."""
    if not receipt:
        return {
            "ok": False,
            "status": "EXTERNAL VERIFICATION REQUIRED",
            "lines": [],
            "message": "Liveからの読戻しはまだありません。",
        }
    status = str(receipt.get("status") or "")
    passed = status == "verified"
    mismatches = receipt.get("mismatches") or []
    fields = ("tempo", "tracks", "clips", "notes", "arrangement")
    failed_fields = {str(item.get("field_name") or "") for item in mismatches}
    lines = []
    for name in fields:
        token = "FAIL" if any(name in field for field in failed_fields) or not passed else "PASS"
        if not passed and not mismatches:
            token = "FAIL"
        lines.append({"name": name, "status": token})
    return {
        "ok": passed,
        "status": "PROJECT READY" if passed else status or "verification_failed",
        "lines": lines,
        "message": "Liveの読戻しと計画が一致しました。" if passed else "読戻しが計画と一致していません。",
    }
