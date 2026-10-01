#!/bin/bash
# macOS Finder からダブルクリックする初回セットアップ。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
echo "KIHACHI MUSIC AI インストール"
echo "場所: $ROOT"
echo
if ./scripts/setup.sh; then
  echo
  echo "セットアップ完了。次回は scripts/kihachi-studio.command を開いてください。"
  echo "最初の曲は docs/QUICKSTART.md を参照してください。"
else
  echo
  echo "セットアップに失敗しました。表示されたエラーと docs/TROUBLESHOOTING.md を確認してください。"
  read -r -p "Enter キーで閉じます…" _
  exit 1
fi
read -r -p "Enter キーで閉じます…" _
