from fastmcp import FastMCP

from kihachi_mcp.tools import (
    create_ableton_plan,
    create_project_from_songspec,
    generate_audio,
    generate_songspec,
    hello,
    orchestrate_song,
    remember_song,
    review_songspec,
    search_memory,
)

mcp = FastMCP("KIHACHI MUSIC AI")
mcp.add_tool(hello)
mcp.add_tool(generate_songspec)
mcp.add_tool(create_project_from_songspec)
mcp.add_tool(create_ableton_plan)
mcp.add_tool(generate_audio)
mcp.add_tool(review_songspec)
mcp.add_tool(remember_song)
mcp.add_tool(search_memory)
mcp.add_tool(orchestrate_song)


def main() -> None:
    """Start the KIHACHI MUSIC AI MCP server."""
    mcp.run()


if __name__ == "__main__":
    main()
