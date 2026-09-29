#!/bin/bash
# Finder からの .command でも uv の一般的な導入先を見つける。
set -euo pipefail
found="$(command -v uv || true)"
if [ -n "$found" ] && [ -x "$found" ]; then
  printf '%s\n' "$found"
  exit 0
fi
for candidate in "$HOME/.local/bin/uv" /opt/homebrew/bin/uv /usr/local/bin/uv; do
  if [ -x "$candidate" ]; then
    printf '%s\n' "$candidate"
    exit 0
  fi
done
exit 1
