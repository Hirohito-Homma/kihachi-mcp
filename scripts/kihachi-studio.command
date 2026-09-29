#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
if [ ! -x "$ROOT/.venv/bin/python" ]; then
  echo "セットアップがまだです。先に scripts/インストール.command を開いてください。"
  exit 1
fi
echo "KIHACHI 制作画面を起動します。Cursor や Codex は不要です。"
exec "$ROOT/.venv/bin/python" -m kihachi_mcp.studio
