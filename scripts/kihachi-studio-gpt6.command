#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
if [ ! -x "$ROOT/.venv/bin/python" ]; then
  echo "セットアップがまだです。先に scripts/インストール.command を開いてください。"
  exit 1
fi
if /usr/bin/nc -z 127.0.0.1 8765 >/dev/null 2>&1; then
  echo "Studio はすでに起動中です。終了してから、このファイルを開き直してください。"
  exit 1
fi

echo "KIHACHI Studio を起動します。APIキーなしでも画面を開けます。必要な場合は画面の設定でプロバイダーを選んでください。"
exec "$ROOT/.venv/bin/python" -m kihachi_mcp.studio
