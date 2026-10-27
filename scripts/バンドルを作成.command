#!/bin/bash
# KIHACHI MUSIC AI 配布用バンドル（ZIP）をワンクリックで作成するスクリプト
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "=========================================================="
echo " 🎵 KIHACHI MUSIC AI バンドル作成"
echo "=========================================================="
echo "リポジトリ: $ROOT"
echo

PYTHON_BIN="python3"
if [ -x "$ROOT/.venv/bin/python" ]; then
    PYTHON_BIN="$ROOT/.venv/bin/python"
fi

"$PYTHON_BIN" "$ROOT/scripts/bundle_kihachi.py" "$@"

if [ -d "$ROOT/bundles" ]; then
    echo
    echo "Finderで出力フォルダを開きます..."
    open "$ROOT/bundles"
fi

echo
read -r -p "Enter キーを押して終了します…" _
