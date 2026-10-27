#!/bin/zsh
cd "${0:A:h}"
if /usr/bin/curl -fsS http://127.0.0.1:8421/api/status >/dev/null 2>&1; then
  open 'http://127.0.0.1:8421'
  exit 0
fi
ENGINE_DIR="${KIHACHI_ENGINE_DIR:-$HOME/MusicAI/kihachi-music-ai}"
"$ENGINE_DIR/.venv/bin/python" server.py &
studio_pid=$!
trap 'kill "$studio_pid" 2>/dev/null' EXIT INT TERM
for attempt in {1..30}; do
  if /usr/bin/curl -fsS http://127.0.0.1:8421/ >/dev/null 2>&1; then
    open 'http://127.0.0.1:8421'
    break
  fi
  sleep 0.2
done
wait "$studio_pid"
