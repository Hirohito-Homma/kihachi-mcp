from fastmcp import FastMCP

from tools.brain import generate_songspec
from tools.hello import hello
from tools.project_builder import create_project_from_songspec

mcp = FastMCP("KIHACHI MUSIC AI")
mcp.add_tool(hello)
mcp.add_tool(generate_songspec)
mcp.add_tool(create_project_from_songspec)

if __name__ == "__main__":
    mcp.run()
