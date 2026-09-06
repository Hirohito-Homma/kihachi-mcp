from kihachi_mcp.tools import (
    create_ableton_plan,
    create_project_from_songspec,
    generate_audio,
    generate_songspec,
    hello,
    orchestrate_song,
    prepare_ableton_handoff,
    remember_song,
    review_songspec,
    search_memory,
)


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


def test_generate_songspec_uses_knowledge_defaults_when_omitted() -> None:
    assert generate_songspec(genre="dub techno", length_minutes=5) == {
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


def test_create_project_can_include_arrangement() -> None:
    spec = generate_songspec(
        genre="dub techno",
        length_minutes=5,
    )

    plan = create_project_from_songspec(spec, include_arrangement=True)

    assert plan["arrangement"] == [
        {"name": "Intro", "start_bar": 1, "length_bars": 32},
        {"name": "Build", "start_bar": 33, "length_bars": 32},
        {"name": "Drop", "start_bar": 65, "length_bars": 64},
        {"name": "Breakdown", "start_bar": 129, "length_bars": 16},
        {"name": "Outro", "start_bar": 145, "length_bars": 16},
    ]


def test_create_ableton_plan_public_json() -> None:
    project = create_project_from_songspec(
        generate_songspec(genre="dub techno", length_minutes=5),
        include_arrangement=True,
    )

    assert create_ableton_plan(project) == {
        "set_name": "Untitled",
        "genre": "dub techno",
        "tempo": 110,
        "key": "D#m",
        "length_minutes": 5,
        "bars": 160,
        "tracks": [
            {"name": "Kick", "track_type": "MIDI", "color": "Red"},
            {"name": "Bass", "track_type": "MIDI", "color": "Blue"},
            {"name": "Dub Chords", "track_type": "MIDI", "color": "Purple"},
            {"name": "Pad", "track_type": "MIDI", "color": "Gray"},
            {"name": "FX", "track_type": "Audio", "color": "Gray"},
        ],
        "locators": [
            {"name": "Intro", "start_bar": 1, "length_bars": 32},
            {"name": "Build", "start_bar": 33, "length_bars": 32},
            {"name": "Drop", "start_bar": 65, "length_bars": 64},
            {"name": "Breakdown", "start_bar": 129, "length_bars": 16},
            {"name": "Outro", "start_bar": 145, "length_bars": 16},
        ],
    }


def test_generate_audio_blocks_without_google_credentials(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    project = create_project_from_songspec(
        generate_songspec(genre="dub techno", length_minutes=1)
    )

    result = generate_audio(project, "Kick", str(tmp_path / "kick.wav"))

    assert result["status"] == "blocked"
    assert "API_KEY" in result["error"]


def test_review_songspec_public_json() -> None:
    spec = generate_songspec(genre="dub techno", length_minutes=5)

    result = review_songspec(spec)

    assert result == {"approved": True, "score": 1.0, "comments": []}


def test_memory_tools_share_process_store() -> None:
    spec = generate_songspec(genre="dub techno", length_minutes=5)

    remembered = remember_song(spec)

    assert remembered["genre"] == "dub techno"
    assert search_memory("dub techno")[-1]["songspec"] == spec


def test_orchestrate_song_public_json() -> None:
    result = orchestrate_song("dub techno", length_minutes=5)

    assert result["songspec"]["tempo"] == 110
    assert result["review"]["approved"] is True
    assert result["project"]["arrangement"][0]["name"] == "Intro"


def test_orchestrate_song_accepts_review_failure_policy() -> None:
    result = orchestrate_song(
        "dub techno", length_minutes=5, stop_on_review_failure=True
    )

    assert result["review"]["approved"] is True
    assert result["project"] is not None


def test_search_memory_supports_limit() -> None:
    assert len(search_memory("dub", limit=1)) == 1


def test_search_memory_supports_text_query() -> None:
    assert search_memory(query="dub", limit=1)[0]["genre"] == "dub techno"


def test_prepare_ableton_handoff_public_json() -> None:
    project = create_project_from_songspec(
        generate_songspec("dub techno", length_minutes=5), include_arrangement=True
    )

    result = prepare_ableton_handoff(project)

    assert result["ready"] is True
    assert result["errors"] == []
