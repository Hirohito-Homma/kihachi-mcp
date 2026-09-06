from kihachi_mcp.tools import create_project_from_songspec, generate_songspec, hello


def test_hello_is_unchanged() -> None:
    assert hello() == "Hello from KIHACHI MCP"


def test_generate_songspec_public_json_is_unchanged() -> None:
    assert generate_songspec(
        genre="dub techno",
        tempo=110,
        key="D#m",
        length_minutes=5,
        mood="hypnotic",
    ) == {
        "genre": "dub techno",
        "tempo": 110,
        "key": "D#m",
        "length_minutes": 5,
        "bars": 160,
        "tracks": ["Kick", "Bass", "Dub Chords", "Pad", "FX"],
    }


def test_create_project_from_songspec_public_json_is_unchanged() -> None:
    spec = generate_songspec(
        genre="dub techno",
        tempo=110,
        key="D#m",
        length_minutes=5,
        mood="hypnotic",
    )

    assert create_project_from_songspec(spec) == {
        "project_name": "Untitled",
        "genre": "dub techno",
        "tempo": 110,
        "key": "D#m",
        "length_minutes": 5,
        "bars": 160,
        "tracks": [
            {"name": "Kick", "type": "MIDI", "color": "Red"},
            {"name": "Bass", "type": "MIDI", "color": "Blue"},
            {"name": "Dub Chords", "type": "MIDI", "color": "Purple"},
            {"name": "Pad", "type": "MIDI", "color": "Gray"},
            {"name": "FX", "type": "Audio", "color": "Gray"},
        ],
    }
