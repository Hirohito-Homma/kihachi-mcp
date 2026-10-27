#!/bin/bash
set -euo pipefail

# Finder からダブルクリックする Studio 起動入口。
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

if [ ! -x "$ROOT/.venv/bin/python" ]; then
  echo "Studioの実行環境がありません。先に scripts/インストール.command を開いてください。"
  exit 1
fi

if /usr/bin/nc -z 127.0.0.1 8765 >/dev/null 2>&1; then
  echo "Studio はすでに起動中です。ブラウザーで http://127.0.0.1:8765/ を開いてください。"
  open "http://127.0.0.1:8765/"
  exit 0
fi

echo "KIHACHI Studio を起動します。起動エラーが出た場合は、このウィンドウの表示を確認してください。"
exec "$ROOT/.venv/bin/python" -m kihachi_mcp.studio
