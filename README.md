## Status

Current milestone

Sprint 1 / ISSUE-0003 service layer

# KIHACHI Brain

Brain MCP for the KIHACHI MUSIC AI Platform.

KIHACHI MUSIC AI is not an AI music generator.

It is an AI music production platform.

The Brain creates musical plans.

Ableton builds projects.

Google Lyria generates audio.

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
| `create_midi_plan` | ProjectPlan から決定論的なMIDIクリップ計画を生成 |
| `prepare_ableton_handoff` | Ableton受け渡し前の静的検証 |
| `request_live_execution` | 承認待ちのLive実行要求を作成 |
| `execute_live_request` | 承認済み要求を実行アダプターへ渡す |
| `generate_audio` | ProjectPlan の対象トラックからGoogle Lyria音声を生成 |
| `review_songspec` | SongSpec を検証し ReviewResult を返す |
| `remember_song` | SongSpec とReview結果をプロセス内Memoryへ保存 |
| `search_memory` | ジャンル・全文・Review品質・順位でMemoryを検索 |
| `orchestrate_song` | 生成・Review・Memory・Project作成を一括実行 |

Memoryを再起動後も保持する場合は、保存先を設定します。未設定なら従来どおりプロセス内のみです。

```bash
export KIHACHI_MEMORY_PATH="$HOME/.kihachi/memory.json"
```
詳細は [docs/API.md](docs/API.md)。

## 必要環境

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)

```bash
uv sync --group dev
```

`generate_audio`を使う場合は、Google AIのAPIキーを環境変数へ設定します。キーはTool入力やJSON出力へ渡しません。

```bash
export GEMINI_API_KEY="..."
export LYRIA_MODEL="lyria-3.5"
```

設定例は `.env.example` を参照してください。実キーを `.env.example` やGitへ保存しないでください。

## 確認

```bash
uv run pytest
uv run ruff check src tests server.py
uv run fastmcp list server.py
```

`fastmcp list` の期待値: Tools (13) — `hello` / `generate_songspec` / `create_project_from_songspec` / `create_ableton_plan` / `create_midi_plan` / `generate_audio` / `review_songspec` / `remember_song` / `search_memory` / `orchestrate_song`

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
