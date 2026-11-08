import json

import pytest

from kihachi_mcp.models import GrooveSpec, SectionSpec, SongSpec

V2_DATA = {
    "genre": "dub techno",
    "tempo": 110,
    "key": "D#m",
    "length_minutes": 2.9,
    "bars": 80,
    "tracks": ["Kick", "Bass", "Pad", "Lead"],
    "sections": [
        {
            "name": "Intro",
            "bars": 8,
            "energy": 0.3,
            "chords": ["D#m"],
            "tracks": ["Kick", "Pad"],
            "note": "",
        },
        {
            "name": "Groove",
            "bars": 16,
            "energy": 0.6,
            "chords": ["D#m", "B", "D#m", "C#"],
            "tracks": [],
            "note": "",
        },
        {
            "name": "Drop",
            "bars": 16,
            "energy": 0.85,
            "chords": ["D#m", "D#m", "F#", "C#"],
            "tracks": [],
            "note": "",
        },
        {
            "name": "Groove",
            "bars": 16,
            "energy": 0.6,
            "chords": ["D#m", "B", "D#m", "C#"],
            "tracks": [],
            "note": "",
        },
        {
            "name": "Drop",
            "bars": 16,
            "energy": 0.85,
            "chords": ["D#m", "D#m", "F#", "C#"],
            "tracks": [],
            "note": "",
        },
        {
            "name": "Outro",
            "bars": 8,
            "energy": 0.25,
            "chords": ["D#m"],
            "tracks": ["Pad"],
            "note": "",
        },
    ],
    "groove": {
        "swing": 0.54,
        "kick_style": "four_on_the_floor",
        "bass_density": 6.3,
        "syncopation": 0.35,
        "rest_ratio": 0.52,
        "preserve_space": True,
        "dynamic_profile": "stable",
        "profile_id": "kihachi_style_v1",
    },
}


def test_v1_dict_still_roundtrips_without_v2_keys() -> None:
    data = {
        "genre": "house",
        "tempo": 124,
        "key": "Am",
        "length_minutes": 4.0,
        "bars": 128,
        "tracks": ["Kick", "Bass"],
    }

    spec = SongSpec.from_dict(data)

    assert spec.sections == ()
    assert spec.groove is None
    assert spec.to_dict() == data


def test_v2_json_roundtrip_preserves_object() -> None:
    spec = SongSpec.from_dict(V2_DATA)
    restored = SongSpec.from_dict(json.loads(json.dumps(spec.to_dict())))

    assert restored == spec
    assert restored.to_dict()["sections"][1]["chords"] == ["D#m", "B", "D#m", "C#"]
    assert restored.groove == GrooveSpec(
        swing=0.54,
        kick_style="four_on_the_floor",
        bass_density=6.3,
        syncopation=0.35,
        rest_ratio=0.52,
        preserve_space=True,
        dynamic_profile="stable",
        profile_id="kihachi_style_v1",
    )


def test_v2_bars_derived_from_sections_when_missing() -> None:
    data = {k: v for k, v in V2_DATA.items() if k != "bars"}

    assert SongSpec.from_dict(data).bars == 80


def test_v2_section_total_mismatch_is_rejected() -> None:
    data = dict(V2_DATA, bars=79)

    with pytest.raises(ValueError, match="does not match"):
        SongSpec.from_dict(data)


def test_v2_section_and_chord_lookup() -> None:
    spec = SongSpec.from_dict(V2_DATA)

    assert spec.section_at(0).name == "Intro"  # type: ignore[union-attr]
    assert spec.section_at(8).name == "Groove"  # type: ignore[union-attr]
    assert spec.section_at(79).name == "Outro"  # type: ignore[union-attr]
    assert spec.section_at(80) is None

    # Intro: single chord for all 8 bars.
    assert spec.chord_at(0) == "D#m"
    assert spec.chord_at(7) == "D#m"
    # Groove (bars 8-23): four chords over 16 bars -> four bars each.
    assert spec.chord_at(8) == "D#m"
    assert spec.chord_at(12) == "B"
    assert spec.chord_at(16) == "D#m"
    assert spec.chord_at(20) == "C#"
    assert spec.chord_at(80) is None


def test_v2_no_sections_means_no_chord() -> None:
    spec = SongSpec.from_dict({"tempo": 110, "length_minutes": 5})

    assert spec.section_at(0) is None
    assert spec.chord_at(0) is None


def test_section_validation() -> None:
    with pytest.raises(ValueError):
        SectionSpec(name="", bars=8)
    with pytest.raises(ValueError):
        SectionSpec(name="Intro", bars=0)
    with pytest.raises(ValueError):
        SectionSpec(name="Intro", bars=8, energy=1.5)

    section = SectionSpec(name="Intro", bars=8, chords=["D#m"])  # type: ignore[arg-type]
    assert section.chords == ("D#m",)


def test_groove_validation() -> None:
    with pytest.raises(ValueError):
        GrooveSpec(swing=1.2)
    with pytest.raises(ValueError):
        GrooveSpec(syncopation=-0.1)
    with pytest.raises(ValueError):
        GrooveSpec(bass_density=0)

    assert GrooveSpec().swing == 0.5
    assert GrooveSpec().bass_density is None
