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

mcp = FastMCP("KIHACHI MUSIC AI")
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
