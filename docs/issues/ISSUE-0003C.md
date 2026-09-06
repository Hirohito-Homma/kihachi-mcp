# ISSUE-0003C

# Brain API & Facade Layer

Status: Done

Priority: High

Milestone: Sprint 1

Depends on: ISSUE-0003B

---

# Goal

Add a Brain facade so MCP tools call Brain, and Brain calls services.

```
Tool → Brain → Service → Domain Model
```

Public MCP tools stay `hello`, `generate_songspec`, and
`create_project_from_songspec`.
