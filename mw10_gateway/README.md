# MW10 Gateway (KIHACHI MUSIC AI component)

A standalone FastAPI service that is part of KIHACHI MUSIC AI and lives in
this repository. It is NOT part of the `kihachi_mcp` wheel and does not
import the MCP server; it runs as its own process (see the deploy package
in the KIHACHI_MUSIC_AI file collection, `16_MW10ゲートウェイ/deploy/`).

## What it does

- `POST /api/automation-log` — receive automation performance statistics
  and upsert them into the latest Obsidian session note
  (`Session_MW10_*.md` under `OBSIDIAN_VAULT_PATH`).
- `POST /api/midi-log` — same for MIDI performance statistics, including a
  per-track pitch distribution subsection.
- `GET /api/stream-mw10` — SSE stream of the music pipeline
  (Supervisor → Composer → Mixer). The Composer reads the accumulated
  pitch distribution back from the note and derives the next generation
  pitch range; the Mixer emits a DubDelayCue for the TypeScript side.
  Live writes happen only after explicit human approval on the TS side
  (Gateway approval rule); this service never writes to Live.

## Layout

- `automation_log.py` — note upsert engine + statistics models (stdlib only)
- `pitch_range.py` — pitch-range reader (98% coverage window, Live note
  naming convention C3 = 60, shared with the TS side)
- `music_pipeline.py` — sequential Supervisor/Composer/Mixer runner
- `app.py` — FastAPI wiring (optional `MW10_GATEWAY_TOKEN` via `X-Token`)
- `tests/` — script-style suites; run under pytest via
  `tests/test_mw10_gateway_suites.py` in the repo root, or directly:
  `python3 mw10_gateway/tests/test_pitch_range.py`

## Run

```bash
uv sync --group gateway
OBSIDIAN_VAULT_PATH=/path/to/vault \
  uv run --group gateway uvicorn app:app --host 127.0.0.1 --port 8000 \
  --app-dir mw10_gateway
```
