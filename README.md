## Status

Current milestone

Ableton Live 自動操作（ADR-0006）／**実機未検証**

# KIHACHI Brain

Brain MCP for the KIHACHI MUSIC AI Platform.

KIHACHI MUSIC AI is not an AI music generator.

It is an AI music production platform.

The Brain creates musical plans.

KIHACHI builds them inside Ableton Live.

Memory learns from every song.

Review improves every iteration.

このリポジトリは Brain（FastMCP）です。ジャンル知識から SongSpec と ProjectPlan を作り、承認を経て Ableton Live の Session View と Arrangement View を組み立てます。詳細は [docs/BRAIN.md](docs/BRAIN.md) と [docs/VISION.md](docs/VISION.md)。

## 前提

この4点は仕様です。

- **Google Lyria は廃止済み**です。音声生成機能はありません。成果物は編集可能な Live Set です。
- **Live への変更には人間承認が必要**です。承認トークンは帯域外（所有者のみ読めるファイル）で渡します。
- **既存内容を自動上書きしません。** 利用者所有のトラック・Clip・Arrangement 領域は変更しません。
- **自動再実行しません。読戻し成功まで完了扱いしません。CIでは実機接続しません。**

## 公開 Tool

| Tool | 役割 |
| --- | --- |
| `hello` | 接続確認 |
| `generate_songspec` | SongSpec を JSON で返す |
| `create_project_from_songspec` | SongSpec から ProjectPlan を JSON で返す |
| `create_ableton_plan` | ProjectPlan を Ableton 向け構造へ変換 |
| `create_midi_plan` | ProjectPlan から決定論的なMIDIクリップ計画を生成 |
| `prepare_ableton_handoff` | Live へ渡す前の静的検証 |
| `inspect_live_state` | Live の接続確認と状態読取り（変更しない） |
| `live_device_catalogue` | KIHACHI がロードを許可する Live 標準デバイス一覧 |
| `create_live_mutation_plan` | Session View の変更計画を作る（実行しない） |
| `request_live_execution` | 承認待ちの Live 実行要求を作る |
| `execute_live_request` | 承認済み要求を1回だけ実行し receipt を返す |
| `verify_live_execution` | Live を読み直して receipt を再確認 |
| `expand_session_to_arrangement` | 検証済み Session を Arrangement へ展開する計画を作る |
| `review_songspec` | SongSpec を検証し ReviewResult を返す |
| `remember_song` | SongSpec とReview結果をMemoryへ保存 |
| `search_memory` | ジャンル・全文・Review品質・順位でMemoryを検索 |
| `orchestrate_song` | 生成・Review・Memory・Project作成を一括実行 |

`save_live_set` は実装していません。設計のみ [docs/issues/ISSUE-0021.md](docs/issues/ISSUE-0021.md) にあります。

詳細は [docs/API.md](docs/API.md)。

## 必要環境

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)
- Live 自動操作を使う場合: **Ableton Live 11 以降 + Max for Live（必須）**

```bash
uv sync --group dev
```

Live 自動操作は外部 API キーを必要としません。秘密情報はループバック用のセッショントークン1つだけで、起動ごとに生成され、Git には入りません。

Memoryを再起動後も保持する場合は保存先を設定します。未設定ならプロセス内のみです。

```bash
export KIHACHI_MEMORY_PATH="$HOME/.kihachi/memory.json"
```

承認記録と使用済み idempotency key を再起動後も保持する場合は次を設定します。未設定ならプロセス内のみです。

```bash
export KIHACHI_LIVE_APPROVAL_PATH="$HOME/.kihachi/live-approvals.json"
```

設定例は `.env.example` を参照してください。

## Ableton Live セットアップ

1. [maxforlive/README.md](maxforlive/README.md) の手順で `KIHACHI Live Device.amxd` を作ります。**`.amxd` バイナリはリポジトリに含まれていません**（偽のデバイスファイルを置かないため）。JavaScript ソース、プロトコル仕様、パッケージ化手順が揃っています。
2. Live で Set を開き、デバイスをトラックへロードします。
3. `inspect_live_state` で `health.connected` が `true` になることを確認します。

macOS と Windows の両方に対応しています。パス差は `kihachi_mcp.services.live_paths` と `kihachi_mcp.models.live_paths` に閉じています。

| 用途 | macOS | Windows |
| --- | --- | --- |
| ハンドシェイク | `~/Library/Application Support/KIHACHI/` | `%LOCALAPPDATA%\KIHACHI\` |

プロトコル仕様は [docs/MAXFORLIVE.md](docs/MAXFORLIVE.md)。

## 実行の流れ

```text
inspect_live_state          Live を読む
create_live_mutation_plan   計画を作る（何も変わらない）
request_live_execution      承認トークンをファイルへ書く（何も変わらない）
                            ← ここで人間がファイルを開いてトークンを読む
execute_live_request        1回だけ実行し readback で検証
verify_live_execution       Live を読み直して再確認
expand_session_to_arrangement   verified のときだけ Arrangement へ展開
```

`verified` は「全操作が適用され、かつ全操作が読み戻して一致した」ことのみを意味します。`partially_applied` と `verification_failed` は `verified` ではありません。復旧手順は [docs/RECOVERY.md](docs/RECOVERY.md)。

## 確認

```bash
uv run pytest
uv run ruff check src tests server.py
uv run fastmcp list server.py
```

`fastmcp list` の期待値: Tools (17) — `hello` / `generate_songspec` / `create_project_from_songspec` / `create_ableton_plan` / `create_midi_plan` / `prepare_ableton_handoff` / `inspect_live_state` / `live_device_catalogue` / `create_live_mutation_plan` / `request_live_execution` / `execute_live_request` / `verify_live_execution` / `expand_session_to_arrangement` / `review_songspec` / `remember_song` / `search_memory` / `orchestrate_song`

テストは fake transport のみを使い、Ableton Live も Max も起動しません。**実機検証は [docs/MANUAL_LIVE_TESTS.md](docs/MANUAL_LIVE_TESTS.md) を人間が実行するまで完了しません。** 現時点は実機未検証です。

## Python package structure

```
kihachi-mcp/
├── server.py                 # 互換エントリ
├── src/kihachi_mcp/          # 実装（ADR-0001）
│   ├── server.py
│   ├── api/
│   ├── models/               # live_state / live_mutation / live_receipt ほか
│   ├── services/             # inspector / planner / gate / executor / bridge
│   └── tools/                # 薄いJSONアダプター
├── maxforlive/
│   ├── kihachi.device.js     # Live Object Model 操作
│   └── README.md             # パッチ作成とパッケージ化手順
├── tests/
└── docs/
    ├── BRAIN.md
    ├── VISION.md
    ├── DEVELOPMENT.md
    ├── ROADMAP.md
    ├── ARCHITECTURE.md
    ├── API.md
    ├── MAXFORLIVE.md         # プロトコル仕様
    ├── MANUAL_LIVE_TESTS.md  # 手動実機テスト手順
    ├── RECOVERY.md           # 障害時の復旧手順
    ├── issues/
    └── adr/
```

開発ルールは [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md)。設計は [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。変更履歴は [CHANGELOG.md](CHANGELOG.md)。Live 自動操作の決定は [docs/adr/0006-ableton-live-automation.md](docs/adr/0006-ableton-live-automation.md)。
