# ROADMAP

ビジョンは [VISION.md](VISION.md)。KIHACHI MUSIC AI の MCP サーバーを、曲の仕様（SongSpec）からプロジェクト計画（ProjectPlan）まで一貫して生成する基盤にする。

## 完了

### Sprint 1 / VS1 — Brain Foundation

- FastMCP サーバー `KIHACHI MUSIC AI` を起動
- `hello` で接続確認
- `generate_songspec` でジャンル・テンポ・キー・尺から SongSpec を生成

### Sprint 1 / VS2 — Project Builder

- `create_project_from_songspec` で SongSpec を ProjectPlan に変換
- トラック名を type / color 付きの TrackSpec に展開
- models / services / tools の 3 層に分離
- 公開 MCP API は JSON のまま維持

### ADR-0001 — src-layout

- 実装を `src/kihachi_mcp/` に集約
- ルートは互換エントリ（`server.py`）とメタデータのみ

### ISSUE-0003 — Service Layer

- ルートの重複パッケージを削除
- `SongService` / `ProjectService` / `ReviewService` を `src/kihachi_mcp/services/` に置く
- Tool は Service 呼び出しと JSON 変換だけを行う

### ISSUE-0003B — Service Layer Enhancement

- SongService に generate / validate / arrangement / 小節換算を追加
- ProjectService に name / output / metadata / created_at を追加
- ReviewService に score / comments / warnings / suggestions を追加

### ISSUE-0003C — Brain API & Facade

- `kihachi_mcp.api.Brain` を追加
- Tool は Brain だけを呼ぶ

### ISSUE-0004A — Knowledge Engine (Genre)

- Genre YAML と Knowledge Engine を追加
- SongService はファイルを読まない

## 進行中

なし。Sprint 1 の公開 API は安定対象。

## 次の候補

公開 API を壊さない前提で、次を検討する。

1. Arrangement — ProjectPlan にセクション（Intro / Drop など）を足す
2. DAW Adapter — ProjectPlan を Ableton など外部ツールへ渡す
3. Audio Tool — ACE-Step など生成系と連携する

## 互換方針

既存 Tool の名前と JSON 形は変えない。

- `hello`
- `generate_songspec`
- `create_project_from_songspec`

内部の dataclass / service は自由にリファクタしてよい。

## 長期フェーズ

[VISION.md](VISION.md) の Phase 1–6。いまは Phase 1（Brain Foundation）。
