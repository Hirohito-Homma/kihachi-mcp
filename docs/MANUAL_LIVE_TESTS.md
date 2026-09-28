## 手動実機テスト手順

CI と `uv run pytest` は fake transport のみを使います。**Ableton Live 実機での検証はこの手順を人間が実行するまで完了しません。** 自動テストが全て通っても「実機動作確認済み」とは言えません。

現在の状態: **実機未検証**（このリポジトリの変更時点で、Ableton Live 実機および Max for Live 実機での実行は行われていません）。

### 事前準備

1. Ableton Live 11 以降 + Max for Live を用意します。
2. [maxforlive/README.md](../maxforlive/README.md) の手順で `KIHACHI Live Device.amxd` を作成します。
3. **バックアップ用に、テスト専用の新規 Set を作ります。** 既存の制作物では実行しないでください。
4. `uv run python -c "from kihachi_mcp.services import LiveBridgeSession; print(LiveBridgeSession().write_handshake())"` でハンドシェイクを書き出します。

各段階で、次へ進む前に必ず結果を確認してください。失敗した段階で止めます。自動リトライは行いません。

### 段階1: 接続ヘルスチェック

制作操作は行いません。

- [ ] Live で新規 Set を開き、デバイスを1トラックへロードする
- [ ] `inspect_live_state` を呼ぶ
- [ ] `health.connected` が `true`
- [ ] `health.live_version` が実際の Live バージョンと一致
- [ ] `health.protocol_supported` が `true`
- [ ] Set が何も変わっていない

**弱点**: この段階では制作操作はできません。接続できただけです。

### 段階2: Live状態読取り

- [ ] `inspect_live_state` の `snapshot.tempo` が Live の表示と一致
- [ ] `snapshot.time_signature` が Live の拍子と一致
- [ ] `snapshot.set_path` が実際のファイルパス（未保存 Set では空文字の可能性あり）
- [ ] 手でトラックを1本追加 → 再度呼ぶ → `snapshot.tracks` に現れる
- [ ] `snapshot.devices` が、実際にインストールされている Live エディションの stock デバイスと一致

**弱点**: Live のバージョン差・エディション差で `devices` の内容が変わります。一致しない場合は `maxforlive/kihachi.device.js` の `AVAILABLE_DEVICES` を実機に合わせて修正してください。

### 段階3: Mutation Plan生成

- [ ] `create_live_mutation_plan` を呼ぶ
- [ ] `status` が `approval_required`
- [ ] `operations` の件数が期待どおり
- [ ] `destructive_operation_count` を目視確認
- [ ] **この時点で Set が何も変わっていない**

**弱点**: 差分規則が複雑です。既存 Set では `conflicts` と `warnings` を必ず読んでください。

### 段階4: 承認・冪等性

- [ ] `request_live_execution` を呼ぶ
- [ ] 戻り値に承認トークンが**含まれていない**
- [ ] `approval_token_path` のファイルを開き、トークンを読む
- [ ] `execute_live_request(request, approved=false)` → `approval_required` になり、Set は変わらない
- [ ] 誤ったトークンで `approved=true` → `approval_required` になり、Set は変わらない

**弱点**: 永続状態の管理が必要です。`KIHACHI_LIVE_APPROVAL_PATH` を設定しない場合、承認記録はプロセス内のみです。

### 段階5: MIDI Track作成

- [ ] 正しいトークンで `execute_live_request(request, approved=true)`
- [ ] `status` が `verified`
- [ ] Live に `Kick [KIHACHI]` のようなトラックができている
- [ ] 手で作った既存トラックの名前・内容が変わっていない
- [ ] 同じリクエストを再実行 → `blocked`（`already used`）になり、トラックが二重にできない

**弱点**: 既存 Set との衝突があります。既存に同名トラックがある場合、KIHACHI は `[KIHACHI]` 付きの別トラックを作り、警告を返します。

### 段階6: Scene／Session Clip作成

- [ ] `Intro [KIHACHI]` などのシーンができている
- [ ] 各 MIDI トラック × 各シーンに Clip ができている
- [ ] Clip 名が `Intro [K:xxxxxxxx]` 形式
- [ ] Clip の長さが `length_beats` と一致
- [ ] Loop が有効
- [ ] 既存の Clip が1つも削除・上書きされていない

**弱点**: 空き Slot の判定が必要です。利用者の Clip がある Slot は `conflict` になり、計画全体が `blocked` になります。

### 段階7: MIDIノート配置

- [ ] Clip をダブルクリックし、ノートが入っていることを目視確認
- [ ] `receipt.readback` の `note_count` が Live の実ノート数と一致
- [ ] Kick 系トラックが 4 分音符グリッドに乗っている
- [ ] Bass / Pad のピッチが Set のキーに対して妥当

**弱点**: ノート API の差を吸収する必要があります。Live 11 と 12 で `add_new_notes` / `get_notes_extended` の挙動差が出た場合はここで判明します。

### 段階8: Live標準デバイスロード

- [ ] 各 MIDI トラックに instrument がロードされている
- [ ] `receipt.readback` の `device_name` が Live の表示と一致
- [ ] 存在しないデバイスを要求した場合、`blocked` になり、別デバイスに置き換わらない

**弱点**: エディション差があります。自動ロードは公式 `Track.insert_device` を使うためLive 12.3以降が必要です。Live 11〜12.2では計画段階で拒否されます。12.3以降でも、そのエディションに存在しないデバイスは失敗し、別デバイスには置換されません。

### 段階9: Arrangement展開

- [ ] 段階5〜8 の receipt が `verified` であることを確認
- [ ] `expand_session_to_arrangement` を呼ぶ
- [ ] `status` が `approval_required`
- [ ] 承認して実行 → `verified`
- [ ] Locator が指定小節に作られている
- [ ] Arrangement Clip が指定小節範囲に置かれている
- [ ] 既存の Arrangement Clip が移動・トリムされていない
- [ ] 意図的に既存 Clip と重なる範囲を要求 → `blocked` になり、既存が保持される

**弱点**: 既存配置への影響が大きい段階です。必ずテスト専用 Set で先に確認してください。

### 段階10: 読戻し検証

- [ ] `verify_live_execution` を呼ぶ → `verified`
- [ ] Live 側で手動でトラック名を変える → 再度呼ぶ → `verification_failed`
- [ ] `mismatches` に変更されたフィールドが出る
- [ ] Live を終了する → 呼ぶ → `unavailable`

**弱点**: Live 側の非同期更新を扱う必要があります。`verify_live_execution` は直後の receipt を後から独立に確認するための2回目の読戻しです。

### 拒否されることの確認

- [ ] 録音を開始して実行 → `blocked`（`recording`）、Set は変わらない
- [ ] 再生を開始して実行 → `blocked`（`transport is running`）、Set は変わらない
- [ ] 計画後に Set を手で変更して実行 → `blocked`（fingerprint 不一致）、Set は変わらない
- [ ] 計画を1文字編集して実行 → `blocked`（`plan changed`）

### 記録

実施したら、日付・Live バージョン・OS・各段階の結果を残してください。実施していない段階は「未検証」と明記してください。部分成功を完全成功として報告しないでください。
