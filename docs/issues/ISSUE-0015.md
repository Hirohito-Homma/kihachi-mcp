# ISSUE-0015 — GitHub Actions CI

## Status

Implemented.

The workflow runs on pushes to `main` and pull requests. It installs the locked uv environment, runs pytest and Ruff, and verifies FastMCP tool registration. It never calls Google Lyria or Ableton Live.
