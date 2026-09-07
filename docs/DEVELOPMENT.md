# KIHACHI Brain Development Guide

## Philosophy

KIHACHI Brain is designed as a long-term maintainable platform.

Every implementation should prioritize

- readability
- maintainability
- backward compatibility
- testability

over short-term speed.

---

# Architecture

Always follow

```
FastMCP
    ↓
Tool
    ↓
Brain
    ↓
Service
    ↓
Domain Model
```

Business logic must never exist inside MCP tools.

---

# Repository Layout

```
src/
    kihachi_mcp/
        models/
        services/
        tools/
        shared/
```

Repository root should contain only

- README
- pyproject.toml
- server.py
- docs
- tests

---

# Git Workflow

Every issue

↓

feature branch

↓

review

↓

merge

Never implement directly on main.

Example

```
feature/issue-0003-service-layer
```

---

# Commit Style

Use Conventional Commits.

Examples

```
feat:
fix:
refactor:
docs:
test:
ci:
chore:
```

---

# Testing

Before every commit

Run

```bash
uv run pytest
uv run ruff check
uv run fastmcp list server.py
```

---

# Documentation

Every architectural decision

↓

ADR

Every implementation

↓

Issue

Every release

↓

CHANGELOG

---

# Code Style

Prefer

- dataclass
- type hints
- docstrings

Avoid

- global state
- duplicated logic
- circular imports

---

# Imports

Always use package imports.

Good

```python
from kihachi_mcp.models import SongSpec
```

Avoid

```python
from models import SongSpec
```

---

# Services

Services own business logic.

Examples

- SongService
- ProjectService
- ReviewService
- AudioService
- GoogleLyriaAdapter
- LyriaPromptBuilder
- MemoryService
- AbletonService

---

# MCP Tools

Tools should only

- receive arguments
- call services
- return results

Nothing else.

---

# Reviews

Every issue requires

- Architecture Review
- Code Review
- Testing Review
- Documentation Review

before merge.

---

# Definition of Done

- Ruff passes
- Pytest passes
- Documentation updated
- Tests updated
- Existing MCP tools unchanged
- Architecture respected
