# ISSUE-0015 — GitHub Actions CI

## Status

Implemented.

The workflow runs on pushes to `main` and pull requests. It installs the locked uv environment, runs pytest and Ruff, and verifies FastMCP tool registration. **CIでは実機接続しません。** Ableton Live も Max for Live も起動しません。Live 関連のテストは fake transport のみを使います。
