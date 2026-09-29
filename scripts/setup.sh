#!/bin/bash
# KIHACHI MUSIC AI セットアップ。Apple Silicon / Intel Mac のどちらでも同じ手順です。
# モデルのダウンロードや有料APIの設定は行いません。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

ok() { printf '  READY    %s\n' "$1"; }
warn() { printf '  WARNING  %s\n' "$1"; }
fail() { printf '  OFFLINE  %s\n' "$1"; }

echo "KIHACHI MUSIC AI セットアップ（$(uname -m)）"

UV_BIN="$(bash "$ROOT/scripts/find-uv.sh")" || {
  fail "uv が見つかりません。https://docs.astral.sh/uv/ の手順で導入してから再実行してください。"
  exit 1
}
ok "uv $("$UV_BIN" --version | awk '{print $2}')"

echo "依存関係を入れています（uv sync --locked --no-dev --inexact）…"
"$UV_BIN" sync --locked --no-dev --inexact
ok "Python 依存関係"

if "$ROOT/.venv/bin/kihachi" --help >/dev/null 2>&1; then
  ok "kihachi コマンド"
else
  fail "kihachi コマンドを起動できません。uv sync のエラーを確認してください。"
  exit 1
fi

OLLAMA_URL="${KIHACHI_OLLAMA_URL:-http://127.0.0.1:11434}"
if curl -fsS -m 3 "$OLLAMA_URL/api/tags" >/dev/null 2>&1; then
  models="$(curl -fsS -m 3 "$OLLAMA_URL/api/tags" | "$ROOT/.venv/bin/python" -c 'import json,sys; print(" ".join(m["name"] for m in json.load(sys.stdin).get("models", [])))')"
  if [ -z "$models" ]; then
    warn "Ollama は動いていますが、モデルがありません。KIHACHI は自動でダウンロードしません。docs/OLLAMA_SETUP.md を参照してください。"
  else
    ok "Ollama モデル: $models"
  fi
elif command -v ollama >/dev/null 2>&1; then
  warn "Ollama が $OLLAMA_URL で応答しません。別のターミナルで ollama serve を実行してください。"
else
  warn "Ollama が応答しません。AIなし（既定解釈）でも制作できます。導入は docs/OLLAMA_SETUP.md"
fi

echo
echo "環境診断:"
"$ROOT/.venv/bin/kihachi" doctor || true

echo
echo "次の手順:"
echo "  1. scripts/kihachi-studio.command を開く … 制作画面 http://127.0.0.1:8765/"
echo "  2. Liveへ送るときだけ KIHACHI Live Device を置く（docs/ABLETON_SETUP.md）"
