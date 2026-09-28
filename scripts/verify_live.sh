#!/bin/bash
# 実機 Ableton Live のスモークテスト（120 BPM / Cマイナー / 32小節）。
# Live への書き込みは確認のあと1回だけです。自動再試行はしません。
# 新しい KIHACHI 専用トラックを追加し、既存のトラックとクリップは変更しません。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

BRIEF="KIHACHI STUDIO SMOKE TEST。120 BPM、Cマイナー、32小節"

if ! curl -fsS -m 3 http://127.0.0.1:8765/api/health >/dev/null 2>&1; then
  echo "制作画面が起動していません。別のターミナルで uv run kihachi start を実行してから再実行してください。"
  exit 1
fi

echo "== 1/5 診断"
uv run kihachi doctor || true

echo "== 2/5 候補を作成（AIなし・固定seed）"
output="$(uv run kihachi create "$BRIEF" --offline --seed 120)"
echo "$output"
candidate="$(printf '%s\n' "$output" | sed -n 's/^候補ID: \([0-9a-f]*\).*/\1/p' | head -1)"
if [ -z "$candidate" ]; then
  echo "候補IDを読み取れませんでした。"
  exit 1
fi

echo "== 3/5 レビューと承認"
uv run kihachi review "$candidate"
uv run kihachi approve "$candidate"

echo "== 4/5 ドライラン（Liveは変わりません）"
uv run kihachi ableton dry-run "$candidate" --change-tempo

echo "== 5/5 送信と読み戻し検証"
echo "Live の再生を止めてください。Set のテンポが 120 BPM に変わります。"
uv run kihachi ableton execute "$candidate" --change-tempo

echo
echo "もう一度だけ読み戻す場合: uv run kihachi ableton verify ${candidate:0:8}"
