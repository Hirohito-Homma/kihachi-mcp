from fastmcp import FastMCP

from kihachi_mcp.tools import (
    create_ableton_plan,
    create_project_from_songspec,
    generate_audio,
    generate_songspec,
    hello,
    review_songspec,
)

mcp = FastMCP("KIHACHI MUSIC AI")
mcp.add_tool(hello)
mcp.add_tool(generate_songspec)
mcp.add_tool(create_project_from_songspec)
mcp.add_tool(create_ableton_plan)
mcp.add_tool(generate_audio)
mcp.add_tool(review_songspec)


def main() -> None:
    """Start the KIHACHI MUSIC AI MCP server."""
    mcp.run()


if __name__ == "__main__":
    main()
