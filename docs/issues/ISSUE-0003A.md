# ISSUE-0003A

# Repository Consolidation

Status: Ready

Priority: High

Milestone: Sprint 1

Branch: `feature/issue-002-domain-models`

---

# Goal

Complete ISSUE-0003A Repository Consolidation.

Move all implementation into `src/kihachi_mcp` and keep public MCP
behaviour unchanged.

---

# Requirements

1. Consolidate implementation into

   - `src/kihachi_mcp/models/`
   - `src/kihachi_mcp/services/`
   - `src/kihachi_mcp/tools/`

2. Update every import to `from kihachi_mcp....`

   Never use `from models...`, `from services...`, or `from tools...`.

3. Remove duplicate implementation from the repository root.

4. Preserve compatibility for

   - `uv run server.py`
   - `uv run fastmcp list server.py`

5. Do not change public MCP behaviour.

   These tools must still exist:

   - `hello`
   - `generate_songspec`
   - `create_project_from_songspec`

6. Run `uv run ruff check`, `uv run pytest`, and
   `uv run fastmcp list server.py`. Fix every failure.

---

# Definition of Done

- Implementation lives only under `src/kihachi_mcp/`
- Imports use `kihachi_mcp.*`
- Root `server.py` remains a compatibility entry
- Ruff passes
- Pytest passes
- Public MCP tools unchanged
