# ISSUE-0006

# Arrangement Integration

Status: Done

Priority: High

Milestone: v0.2

---

Brain derives Arrangement sections from genre knowledge and SongSpec bars.
ProjectPlan stores the typed sections internally.
The MCP adapter exposes them only with `include_arrangement=true`.
Existing JSON output remains unchanged by default.
Unknown genres keep producing projects with an empty arrangement.
