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
uv run ruff check models services tools tests server.py
uv run fastmcp list server.py
```

`fastmcp list` の期待値: Tools (3) — `hello` / `generate_songspec` / `create_project_from_songspec`

## Python package structure

```
kihachi-mcp/
├── server.py
├── models/           # SongSpec / TrackSpec / ProjectPlan
├── services/         # SongService / ProjectService
├── tools/            # MCP adapters（JSON I/O）
├── tests/
└── docs/
    ├── ROADMAP.md
    ├── ARCHITECTURE.md
    └── API.md
```

- `models/` — dataclass。JSON 変換は `to_dict()` / `from_dict()`
- `services/` — 生成と変換のルール
- `tools/` — FastMCP 向け。Service を呼び、JSON だけ返す
- `server.py` — Tool を登録してサーバーを起動する

設計の説明は [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。今後の切片は [docs/ROADMAP.md](docs/ROADMAP.md)。
