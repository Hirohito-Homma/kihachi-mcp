## Status

Current milestone

Sprint 1 / ISSUE-0003 service layer

# KIHACHI Brain

Brain MCP for the KIHACHI MUSIC AI Platform.

KIHACHI MUSIC AI is not an AI music generator.

It is an AI music production platform.

The Brain creates musical plans.

Ableton builds projects.

ACE-Step generates audio.

Memory learns from every song.

Review improves every iteration.

このリポジトリは Brain（FastMCP）です。SongSpec と ProjectPlan を返します。詳細は [docs/BRAIN.md](docs/BRAIN.md) と [docs/VISION.md](docs/VISION.md)。

## 公開 Tool

| Tool | 役割 |
| --- | --- |
| `hello` | 接続確認 |
| `generate_songspec` | SongSpec を JSON で返す |
| `create_project_from_songspec` | SongSpec から ProjectPlan を JSON で返す |
| `create_ableton_plan` | ProjectPlan を Ableton 向け構造へ変換 |
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

`fastmcp list` の期待値: Tools (4) — `hello` / `generate_songspec` / `create_project_from_songspec` / `create_ableton_plan`

## Python package structure

```
kihachi-mcp/
├── server.py                 # 互換エントリ
├── src/kihachi_mcp/          # 実装（ADR-0001）
│   ├── server.py
│   ├── api/
│   ├── models/
│   ├── services/
│   └── tools/
├── tests/
└── docs/
    ├── BRAIN.md
    ├── VISION.md
    ├── DEVELOPMENT.md
    ├── ROADMAP.md
    ├── ARCHITECTURE.md
    ├── API.md
    ├── issues/ISSUE-0003A.md
    ├── issues/ISSUE-0003B.md
    ├── issues/ISSUE-0003C.md
    ├── issues/ISSUE-0004A.md
    └── adr/0001-use-src-layout.md
```

開発ルールは [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md)。設計は [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。変更履歴は [CHANGELOG.md](CHANGELOG.md)。src-layout の決定は [docs/adr/0001-use-src-layout.md](docs/adr/0001-use-src-layout.md)。
