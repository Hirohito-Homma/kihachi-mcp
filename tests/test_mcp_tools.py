from kihachi_mcp.tools import (
    create_ableton_plan,
    create_live_mutation_plan,
    create_project_from_songspec,
    discover_live_capabilities,
    execute_live_request,
    generate_songspec,
    hello,
    inspect_live_state,
    live_device_catalogue,
    orchestrate_song,
    prepare_ableton_handoff,
    remember_song,
    request_live_execution,
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


def test_inspect_live_state_reports_unconfigured_transport() -> None:
    result = inspect_live_state()

    assert result["health"]["connected"] is False
    assert result["snapshot"] is None


def test_live_capabilities_hide_unavailable_actions() -> None:
    result = discover_live_capabilities()
    assert result["connected"] is False
    assert result["executable_capability_ids"] == []
    assert any(row["id"] == "warp" and row["status"] == "unsupported" for row in result["capabilities"])


def test_live_device_catalogue_excludes_external_plugins() -> None:
    result = live_device_catalogue()

    names = [device["name"] for device in result["devices"]]

    assert result["external_plugins_supported"] is False
    assert "Drum Rack" in names
    assert "Operator" in names


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


def test_create_live_mutation_plan_is_unavailable_without_a_transport() -> None:
    project = create_project_from_songspec(
        generate_songspec("dub techno", length_minutes=5), include_arrangement=True
    )

    result = create_live_mutation_plan(project)

    assert result["status"] == "unavailable"
    assert result["operations"] == []


def test_request_live_execution_does_not_reach_live_without_a_transport() -> None:
    project = create_project_from_songspec(
        generate_songspec("dub techno", length_minutes=5), include_arrangement=True
    )

    result = request_live_execution(project)

    assert result["status"] == "unavailable"
    assert result["approval_required"] is False
    assert "approval_token_path" not in result


def test_execute_live_request_refuses_a_plan_it_never_approved() -> None:
    result = execute_live_request({"schema_version": 1, "operations": []})

    assert result["status"] == "blocked"
