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

printf 'OpenAI PlatformのAPIキーを入力してください（画面には表示されません）: '
IFS= read -r -s OPENAI_API_KEY
printf '\n'
if [ -z "$OPENAI_API_KEY" ]; then
  echo "APIキーが空です。起動を中止します。"
  exit 1
fi
if [[ "$OPENAI_API_KEY" =~ [[:space:]] ]]; then
  echo "APIキーに空白や改行が含まれています。前後の空白を除いて入力し直してください。"
  exit 1
fi
export OPENAI_API_KEY
echo "KIHACHI Studio を起動します。システム設定で OpenAI とモデルを選んでください。"
exec "$ROOT/.venv/bin/python" -m kihachi_mcp.studio
