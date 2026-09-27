# ISSUE-0020 — Ableton Live 自動操作へ移行し Google Lyria を廃止

Status: 完了（**実機未検証**）

ADR: [adr/0006-ableton-live-automation.md](../adr/0006-ableton-live-automation.md)

## 目的

KIHACHI MUSIC AI の成果物を「生成済み音声」から「編集可能な Ableton Live Set」へ変えます。

1. Google Lyria を完全に廃止する
2. Max for Live を必須要件とする
3. KIHACHI MCP から Ableton Live を安全に自動操作する
4. Session View でパターンを作成する
5. 検証後、Arrangement View へ展開する
6. 新規 Set と既存 Set の両方を扱う
7. Live 標準デバイス／音源を自動ロードする
8. macOS と Windows へ対応する
9. 実行前の人間承認と、実行後の Live 状態読戻しを必須にする

## 削除したもの

| 種別 | 対象 |
| --- | --- |
| MCP tool | `generate_audio` |
| services | `GoogleLyriaAdapter`、`LyriaPromptBuilder`、`AudioService` |
| models | `AudioRenderRequest`、`AudioRenderResult` |
| tools | `tools/audio.py` |
| scripts | `scripts/lyria_smoke.py` |
| tests | `test_google_lyria_adapter.py`、`test_lyria_prompt_builder.py`、`test_audio_plan.py` |
| 環境変数 | `GEMINI_API_KEY`、`LYRIA_MODEL`、`LYRIA_BASE_URL`、`LYRIA_TIMEOUT` |
| 処理 | MP3 取得・検証・保存、base64 デコード、artifact 原子的書き込み |

汎用モデルは残しました。`GenerationService`、`models/generation.py`、`KnowledgeService`、`SongService`、`ProjectService` は Lyria 非依存だったため、`rg` で参照を確認した上で保持しています。`tests/test_generation_service.py` は Lyria 由来のテストのみ除去しました。

読戻しのない実行境界も撤去しました。`AbletonExecutionAdapter` は承認後に `status="executed"` を返しますが Live から何も読み戻していないため、安全規則14に反します。`AbletonExecutionResult` と `LiveExecutionRequest` も併せて削除しました。`AbletonService.create_plan` / `create_midi_plan` / `prepare_handoff` は変更していません。

## 追加したもの

### models

`live_contract.py`（スキーマ版数・状態・操作定数・正規化ハッシュ・所有権マーカー）、`live_paths.py`（Set パスのプラットフォーム正規化）、`live_state.py`（`LiveStateSnapshot` ほか）、`live_mutation.py`（`LiveMutationOperation` / `LiveMutationPlan` / `LiveConflict` / `LivePrecondition`）、`live_receipt.py`（`LiveExecutionReceipt` / `LiveOperationReadback` / `LiveReadbackMismatch`）。

### services

`live_transport.py`（`LiveTransport` Protocol、`NullLiveTransport`）、`live_transport_fake.py`（`FakeLiveTransport` シミュレータ）、`live_state_inspector.py`、`live_mutation_planner.py`、`session_pattern_builder.py`、`live_device_catalog.py`、`live_approval_gate.py`、`live_execution_service.py`、`arrangement_expander.py`、`live_bridge.py`、`live_paths.py`。

### tools

`tools/live.py` に `inspect_live_state`、`live_device_catalogue`、`create_live_mutation_plan`、`request_live_execution`、`execute_live_request`、`verify_live_execution`、`expand_session_to_arrangement`。

### Max for Live

`maxforlive/kihachi.device.js`（Live Object Model 操作、完成）、`maxforlive/README.md`（パッチ作成とパッケージ化手順）、`docs/MAXFORLIVE.md`（プロトコル仕様）。

## 公開 API への影響

公開ツールは 13 個から 17 個になりました。

`generate_audio` 削除は破壊的変更です。`request_live_execution` と `execute_live_request` はツール名を維持しつつ、`LiveMutationPlan` と `LiveExecutionReceipt` を扱うよう再設計しました。`execute_live_request` は `approval_token` を要求します。`prepare_ableton_handoff` は互換性のため維持しています。

## 安全規則

コードとテストで14項目を固定しました。一覧は [ADR-0006](../adr/0006-ableton-live-automation.md) の「安全規則」節。

所有権は名前マーカーで表現します。Live のトラックとシーンは任意メタデータを持てないためです。トラックとシーンは ` [KIHACHI]`、クリップは ` [K:<8桁hash>]`。マーカーのない要素は利用者所有として読み取り以外行いません。同名であることは所有の根拠になりません。

## 完了条件の結果

- `uv run pytest` — 264 passed
- `uv run ruff check src tests server.py` — All checks passed
- `uv run fastmcp list server.py` — Tools (17)
- Lyria の runtime 参照が `rg` で残っていない（`ai-company-os/` は別プロジェクトのためスコープ外）
- 公開ツール一覧と文書が一致
- fake transport による正常系が `verified`
- 未承認操作が拒否される
- fingerprint 不一致が拒否される
- 二重実行が拒否される
- readback 不一致が `verification_failed`
- 既存 Set の占有領域を変更しない

## 未完了部分

1. **実機未検証。** Ableton Live 実機および Max for Live 実機での実行は行っていません。[MANUAL_LIVE_TESTS.md](../MANUAL_LIVE_TESTS.md) を人間が実行するまで実機動作は主張できません。
2. **`.amxd` バイナリなし。** Max の独自バイナリ形式をテキストから正しく生成できないため、偽のデバイスファイルを置きませんでした。JavaScript ソース、プロトコル仕様、パッケージ化手順を提供しています。
3. **`load_live_device` の Max パッチ配線が未完了。** JavaScript から Live のブラウザを操作できないため、パッチ側の配線が必要です（ISSUE-0023）。配線しない限り、この操作は失敗として報告され `verified` になりません。
4. **`save_live_set` は未実装。** 設計のみ [ISSUE-0021](ISSUE-0021.md) に文書化しています。
5. **外部プラグインはスコープ外。** 将来の許可リスト方式として文書化しています（ISSUE-0022）。
6. **デバイス利用可能一覧が Max デバイス側のハードコード。** Live には `[js]` から使える公式のブラウザ列挙 API がないため、`kihachi.device.js` の `AVAILABLE_DEVICES` を実機のエディションに合わせて調整する必要があります。
