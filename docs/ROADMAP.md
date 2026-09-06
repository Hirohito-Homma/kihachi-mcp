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

### ISSUE-0005 — Knowledge-driven Song Generation

- SongSpec のテンポ、キー、トラックを Genre YAML から補完
- 既存の引数指定と JSON 形を維持

### ISSUE-0006 — Arrangement Integration

- Brain が Genre YAML から Arrangement を生成
- ProjectPlan が型付きセクションを保持
- MCP は明示指定時だけ Arrangement を返す

### ISSUE-0007 — DAW Adapter

- ProjectPlan をAbletonProjectPlan へ変換
- TrackSpec をAbleton track、Arrangement を locator へ変換
- Live操作やMIDI生成は行わない

### ISSUE-0008 — Audio Tool

- ProjectPlan からAudioRenderRequestを構築
- Google Lyria adapterでInteractions API、MP3 decode、検証を実装
- `generate_audio`をMCP Toolとして追加
- `127 passed`、Ruff、FastMCP Tools (12)を確認済み

### ISSUE-0009 — Review Integration


- 既存の Brain Review API を `review_songspec` MCP Tool として公開
- SongSpec JSON を ReviewResult JSON へ変換
- 外部APIなしのローカル検証を追加

### ISSUE-0010 — Memory Integration

- `MemoryEntry` と `MemoryService` を追加
- `remember_song` / `search_memory` をMCP Toolとして公開
- 未設定時はプロセス内、`KIHACHI_MEMORY_PATH`設定時はJSONへ原子的に永続化
- 外部DBなしのままOrchestratorから差し替え可能な境界を確保

### ISSUE-0015 — GitHub Actions CI

- push / pull requestでpytest、Ruff、FastMCP登録確認を実行
- Lyria実機やAbleton Liveには接続しない

### ISSUE-0014 — Ableton Execution Adapter

- 承認状態を検査し、`approval_required` / `blocked` / `unavailable`を区別
- Transport注入点を提供
- 未設定時は実機実行を行わない

### ISSUE-0013 — Live Execution Boundary

- `request_live_execution`で承認待ちの1回分要求を作成
- 検証エラーは`blocked`、正常時は`approval_required`
- Live接続・変更は実行しない

### ISSUE-0012 — Ableton Integration Preparation

- `prepare_ableton_handoff`でトラック重複・テンポ・Locator範囲を静的検証
- Live操作なしで`ready`、エラー、警告、計画を返す

### ISSUE-0011 — Orchestrator Design

- `generate_song` → Review → Memory → Project作成の順序を固定
- `OrchestrationResult`で一連の結果をJSON化
- `orchestrate_song`を追加し、既存Toolの契約は維持
- Review不合格時の停止ポリシーをオプション化

## 進行中

なし。Sprint 1 の公開 API は安定対象。

## 次の候補

公開 API を壊さない前提で、次を検討する。

1. Google Lyria実機接続と成果物の運用検証
3. Google Lyria実機接続と成果物の運用検証

## 互換方針

既存 Tool の名前と JSON 形は変えない。

- `hello`
- `generate_songspec`
- `create_project_from_songspec`
- `review_songspec`

内部の dataclass / service は自由にリファクタしてよい。

## 長期フェーズ

[VISION.md](VISION.md) の Phase 1–6。いまは Phase 1（Brain Foundation）。

### ISSUE-0007 — DAW Adapter

- ProjectPlan を AbletonProjectPlan へ変換
- TrackSpec を Ableton track、Arrangement を locator へ変換
- Live操作やMIDI生成は行わない
