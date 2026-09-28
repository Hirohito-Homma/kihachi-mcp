from fastmcp import FastMCP

from kihachi_mcp.tools import (
    create_ableton_plan,
    create_live_mutation_plan,
    create_midi_plan,
    create_project_from_songspec,
    execute_live_request,
    expand_session_to_arrangement,
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
    verify_live_execution,
)
from kihachi_mcp.tools.local_ai import preview_local_ai_song
from kihachi_mcp.tools.studio import (
    apply_ableton_effects,
    apply_ableton_master,
    apply_ableton_mix,
    apply_ableton_retune,
    apply_ableton_sidechain,
    approve_song,
    create_song,
    doctor,
    dry_run_ableton_plan,
    execute_ableton_plan,
    get_project,
    list_projects,
    measure_loudness,
    ollama_status,
    review_song,
    revise_song,
    verify_ableton_project,
)

mcp = FastMCP("KIHACHI MUSIC AI")
for _tool in (
    create_song,
    list_projects,
    get_project,
    review_song,
    revise_song,
    approve_song,
    dry_run_ableton_plan,
    execute_ableton_plan,
    apply_ableton_effects,
    apply_ableton_mix,
    apply_ableton_master,
    apply_ableton_retune,
    apply_ableton_sidechain,
    verify_ableton_project,
    measure_loudness,
    ollama_status,
    doctor,
):
    mcp.add_tool(_tool)
mcp.add_tool(preview_local_ai_song)
mcp.add_tool(hello)
mcp.add_tool(generate_songspec)
mcp.add_tool(create_project_from_songspec)
mcp.add_tool(create_ableton_plan)
mcp.add_tool(create_midi_plan)
mcp.add_tool(prepare_ableton_handoff)
mcp.add_tool(inspect_live_state)
mcp.add_tool(live_device_catalogue)
mcp.add_tool(create_live_mutation_plan)
mcp.add_tool(request_live_execution)
mcp.add_tool(execute_live_request)
mcp.add_tool(verify_live_execution)
mcp.add_tool(expand_session_to_arrangement)
mcp.add_tool(review_songspec)
mcp.add_tool(remember_song)
mcp.add_tool(search_memory)
mcp.add_tool(orchestrate_song)


def main() -> None:
    """Start the KIHACHI MUSIC AI MCP server."""
    mcp.run()


if __name__ == "__main__":
    main()
