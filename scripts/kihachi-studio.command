#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
if ! command -v uv >/dev/null 2>&1; then
  echo "uv が見つかりません。https://docs.astral.sh/uv/ を見て導入してください。"
  exit 1
fi
echo "KIHACHI 制作画面を起動します。Cursor や Codex は不要です。"
exec uv run python -m kihachi_mcp.studio
