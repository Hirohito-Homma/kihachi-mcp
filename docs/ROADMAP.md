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
- KnowledgeService が知識を取得し、GenerationContext として Generator へ渡す
- 生成結果は使用した knowledge id / version / source を保持できる
- 既存の引数指定と JSON 形を維持

### ISSUE-0006 — Arrangement Integration

- Brain が Genre YAML から Arrangement を生成
- ProjectPlan が型付きセクションを保持
- MCP は明示指定時だけ Arrangement を返す

### ISSUE-0007 — DAW Adapter

- ProjectPlan をAbletonProjectPlan へ変換
- TrackSpec をAbleton track、Arrangement を locator へ変換
- Live操作やMIDI生成は行わない

### ISSUE-0008 — Audio Tool（**撤回済み**）

ISSUE-0020 で完全に削除されました。`AudioRenderRequest`、`GoogleLyriaAdapter`、`generate_audio` は存在しません。

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
- **CIでは実機接続しない。** Ableton Live も Max も起動しない

### ISSUE-0016 — Deterministic MIDI Plan

- ProjectPlanのMIDIトラックとArrangementから決定論的なクリップ配置計画を生成
- `create_midi_plan`をMCP Toolとして追加
- Live接続、音符生成、プロジェクト変更は行わない

### ISSUE-0014 — Ableton Execution Adapter（**ISSUE-0020 で置換**）

読戻しなしで `executed` を返していたため撤去しました。`LiveExecutionService` が後継です。

### ISSUE-0013 — Live Execution Boundary（**ISSUE-0020 で再設計**）

ツール名 `request_live_execution` は維持し、`LiveMutationPlan` と承認トークンを扱うよう再設計しました。

### ISSUE-0012 — Ableton Integration Preparation

- `prepare_ableton_handoff`でトラック重複・テンポ・Locator範囲を静的検証
- Live操作なしで`ready`、エラー、警告、計画を返す

### ISSUE-0011 — Orchestrator Design

- `generate_song` → Review → Memory → Project作成の順序を固定
- `OrchestrationResult`で一連の結果をJSON化
- `orchestrate_song`を追加し、既存Toolの契約は維持
- Review不合格時の停止ポリシーをオプション化

### ISSUE-0019 — Lyria 3.5 Production API（**ISSUE-0020 で撤回**）

MP3 検証と HTTP 境界の堅牢化は完了しましたが、方針変更により機能全体を削除しました。判断の記録は [adr/0005-lyria-35-primary-audio.md](adr/0005-lyria-35-primary-audio.md)（superseded）。

### ISSUE-0020 — Ableton Live 自動操作（実装完了、実機部分検証済み）

- Google Lyria を完全に廃止（tool / adapter / prompt builder / models / tests / 環境変数 / 文書）
- Max for Live を必須要件化
- `LiveStateSnapshot` / `LiveMutationOperation` / `LiveMutationPlan` / `LiveExecutionReceipt` を追加
- `LiveStateInspector` → `LiveMutationPlanner` → `ApprovalGate` → `LiveExecutionService` の境界を確立
- Session View でパターン生成、検証後に Arrangement View へ展開
- Live 標準デバイス11種の自動ロード、利用不能時は `blocked`、自動フォールバックなし
- `127.0.0.1` 限定 Bridge、起動ごとのセッショントークン、サイズ・操作数・時間上限
- 安全規則14項目をコードとテストで固定
- macOS / Windows のパス差を分離
- 公開ツール 13 → 17
- 実装時の完了条件: `264 passed`、Ruff 緑、FastMCP Tools (17)
- 2026-10-18 に Live 12.4.5 で Arrangement 展開と独立読戻しを部分確認。64/64 操作、58 クリップ、6 Locator が計画どおりで、Locator 改名も読戻しで検出した。証跡と範囲は [MANUAL_LIVE_TESTS.md](MANUAL_LIVE_TESTS.md)。
- これは全手順の実機完了を意味しない。既存 Arrangement Clip との衝突は読み取り専用 preview で拒否され、クリップ保持も確認したが、衝突状態で execute API を呼ぶ実機試験は行っていない。Live 終了後の unavailable 判定と音楽的な聴感評価も未確認。段階1〜8も手順書の個別項目を完了したとは扱わない。

詳細は [adr/0006-ableton-live-automation.md](adr/0006-ableton-live-automation.md) と [issues/ISSUE-0020.md](issues/ISSUE-0020.md)。

## 進行中

フェイク transport で、既存 Clip との衝突時に追加操作がないことと、読戻し中の切断時に `unavailable` となり再送しないことを確認済み。Live 12.4.5 では衝突プレビューの拒否と既存 Clip の保持も確認済み。Live 停止後の `unavailable` は未確認。

## 次の候補

### ISSUE-0021 — save_live_set（設計のみ）

保存は取り消せない唯一の操作のため、別の承認段階として分離します。実装は含みません。[issues/ISSUE-0021.md](issues/ISSUE-0021.md)。

### ISSUE-0022 — 外部プラグイン許可リスト（未着手）

VST3 / AU / CLAP は現在スコープ外です。将来対応する場合は明示的な許可リスト方式にします。理由は [adr/0006-ableton-live-automation.md](adr/0006-ableton-live-automation.md) の「デバイススコープ」節。

### ISSUE-0023 — Live純正デバイスローダー（コード完了、実機未検証）

Live 12.3以降の公式 `Track.insert_device` を使用します。パッチ側の非公式ブラウザー配線は不要です。Live 11〜12.2ではロード可能一覧を無効化して計画段階で拒否します。コードと契約テストは完了していますが、実機確認は [MANUAL_LIVE_TESTS.md](MANUAL_LIVE_TESTS.md) の段階8が終わるまで未検証です。[issues/ISSUE-0023.md](issues/ISSUE-0023.md)。

### さらに先

1. Review が `LiveExecutionReceipt` を評価し、`verified` のときだけ Memory へ採用する境界
2. Session パターンのバリエーション生成（Groove A / Groove B の差分規則）
3. Live の automation / mixer 操作。読戻しの設計が固まってから

## 互換方針

次の Tool の名前と JSON 形は変えない。

- `hello`
- `generate_songspec`
- `create_project_from_songspec`
- `create_ableton_plan`
- `create_midi_plan`
- `prepare_ableton_handoff`
- `review_songspec`
- `remember_song`
- `search_memory`
- `orchestrate_song`

`request_live_execution` と `execute_live_request` は名前を維持したまま ISSUE-0020 で再設計した（破壊的変更）。内部の dataclass / service は自由にリファクタしてよい。

## 長期フェーズ

[VISION.md](VISION.md) の Phase 1–6。いまは Phase 2（Ableton Integration）。**実機は部分確認のみ。** Live 12.4.5 での Arrangement 展開・読戻しと、読み取り専用の衝突プレビューを確認しました。段階8、Live 停止後の `unavailable`、再生・試聴、衝突状態での execute は未確認です（[MANUAL_LIVE_TESTS.md](MANUAL_LIVE_TESTS.md)）。
