## Status

Current milestone

Sprint 1 / ISSUE-0003 service layer

# KIHACHI MCP

KIHACHI MUSIC AI 用の FastMCP サーバー。曲の仕様（SongSpec）を作り、そこからプロジェクト計画（ProjectPlan）を返す。

## 公開 Tool

| Tool | 役割 |
| --- | --- |
| `hello` | 接続確認 |
| `generate_songspec` | SongSpec を JSON で返す |
| `create_project_from_songspec` | SongSpec から ProjectPlan を JSON で返す |

詳細は [docs/API.md](docs/API.md)。

## 必要環境

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)

```bash
uv sync --group dev
```

## 確認

```bash
uv run pytest
uv run ruff check src tests server.py
uv run fastmcp list server.py
```

`fastmcp list` の期待値: Tools (3) — `hello` / `generate_songspec` / `create_project_from_songspec`

## Python package structure

```
kihachi-mcp/
├── server.py                 # 互換エントリ
├── src/kihachi_mcp/          # 実装（ADR-0001）
│   ├── server.py
│   ├── models/
│   ├── services/
│   └── tools/
├── tests/
└── docs/
    ├── ROADMAP.md
    ├── ARCHITECTURE.md
    ├── API.md
    └── adr/0001-use-src-layout.md
```

設計は [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。src-layout の決定は [docs/adr/0001-use-src-layout.md](docs/adr/0001-use-src-layout.md)。
