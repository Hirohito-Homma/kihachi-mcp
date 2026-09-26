## ADR-0006: Ableton Live 自動操作を Max for Live 経由の承認制パイプラインにする

Status: Accepted

Supersedes: [ADR-0003](0003-plan-only-ableton-adapter.md), [ADR-0004](0004-audio-tool-boundary.md), [ADR-0005](0005-lyria-35-primary-audio.md)

### Context

KIHACHI MUSIC AI は AI 音楽生成器ではなく、AI 音楽制作プラットフォームです。従来は Brain が計画を作り、Google Lyria がリファレンス音声を生成し、Ableton へは人間が手作業で移す設計でした。

この設計には2つの問題がありました。

第一に、Lyria が生成するのは完成音声であり、編集可能な制作物ではありません。DAW 上で直せないものは制作プラットフォームの成果物になりません。加えて外部 API 依存、課金、キー管理、MP3 検証という重い境界を抱えていました。

第二に、Ableton 境界が計画止まりでした。[ADR-0003](0003-plan-only-ableton-adapter.md) の `AbletonExecutionAdapter` は承認後に `status="executed"` を返しますが、Live から何も読み戻していません。Live が実際に何をしたか分からないまま完了を主張する構造でした。

### Decision

Google Lyria を完全に廃止し、Max for Live を必須要件として KIHACHI MCP から Ableton Live を直接、ただし承認制で自動操作します。

境界は次の順で固定します。

```text
SongSpec / ProjectPlan / MidiPlan
  -> LiveStateInspector        Live から読むだけ
  -> LiveMutationPlanner       不活性な計画を作るだけ
  -> ApprovalGate              人間承認と冪等性
  -> LiveExecutionService      承認済み計画を1回だけ適用
  -> localhost Bridge          127.0.0.1 限定
  -> Max for Live Device       Live Object Model 操作
  -> Session View              パターン生成
  -> 検証
  -> Arrangement View          検証済みパターンの展開
  -> 読戻し
  -> Verified Execution Receipt
```

Brain 層から Live を直接操作しません。MCP Tool は薄い JSON アダプターで、計画・検証・実行規則はすべて `services` に置きます。

`verified` は「全操作が適用され、かつ全操作が読み戻して一致した」ことのみを意味します。読み戻せない操作は `verified` にしません。

### Consequences

**得られるもの**

- 成果物が編集可能な Live Set になります。MIDI もデバイスも後から直せます。
- 外部 API、API キー、課金、音声ファイル検証がすべて不要になります。秘密情報はループバックのセッショントークン1つだけです。
- 実際に Live が何をしたかが receipt に残ります。`partially_applied` と `verification_failed` を `verified` から区別できます。

**失うもの**

- Max for Live が必須になります。Live Intro では使えません。
- 音声生成機能がなくなります。リファレンスミックスは出力されません。
- CI で実機検証できません。fake transport の自動テストと、手動実機チェックリストの二層に分かれます。

**破壊的変更**

- MCP ツール `generate_audio` を削除しました。公開ツールは 13 個から 17 個になります。
- `AbletonExecutionAdapter`、`AbletonExecutionResult`、`LiveExecutionRequest`、`AudioService`、`AudioRenderRequest`、`AudioRenderResult`、`GoogleLyriaAdapter`、`LyriaPromptBuilder` を削除しました。
- `request_live_execution` と `execute_live_request` はツール名を維持しつつ、`LiveMutationPlan` と `LiveExecutionReceipt` を扱うよう再設計しました。`execute_live_request` は `approval_token` を要求するようになりました。
- `AbletonService.request_live_execution` を削除しました。`create_plan` / `create_midi_plan` / `prepare_handoff` は変更していません。

### 安全規則

計画と実行の両方で保証し、テストで固定します。

1. 録音中は変更しない
2. 再生中の構造変更はしない
3. Set fingerprint が計画時と違えば停止する
4. 同名トラックだけを根拠に既存トラックを再利用しない
5. KIHACHI 管理 ID がない既存要素は利用者所有とみなす
6. 既存 Clip を削除・上書きしない
7. Arrangement の使用中領域へ配置しない
8. 使用不能なデバイスを別デバイスへ自動置換しない
9. 計画変更後は以前の承認を無効にする
10. 同じ idempotency key を二重実行しない
11. 部分失敗後に残りの操作を続行しない
12. 自動再試行しない
13. 保存操作は別承認にする
14. 読戻し不一致を `verified` にしない

所有権は名前マーカーで表現します。Live のトラックとシーンは任意メタデータを持てないためです。トラックとシーンは ` [KIHACHI]`、クリップは ` [K:<8桁hash>]` を付けます。マーカーのない要素は読み取り以外行いません。

### デバイススコープ

初期対応は Live 標準デバイス 11 種のみです。Drum Rack / Simpler / Operator / Wavetable / Drift / Auto Filter / EQ Eight / Compressor / Saturator / Echo / Hybrid Reverb。

エディションとバージョンで同梱デバイスが異なるため、カタログは候補リストにすぎません。**判定根拠は実行中の Live から読み戻した利用可能一覧のみ**です。存在しないデバイスは `blocked` にし、自動フォールバックしません。macOS と Windows で共通に使える標準デバイスを優先し、AU を共通設計の前提にしません。

外部プラグイン（VST3 / AU / CLAP）は今回のスコープ外です。将来対応する場合は明示的な許可リスト方式にします。プラグインは名前の一意性がなく、パラメータ構造がベンダー依存で、プラットフォーム間で同一性が保証されないため、暗黙のロードは安全境界を壊します。

### 保存操作

`save_live_set` は今回実装しません。設計のみ [ISSUE-0021](../issues/ISSUE-0021.md) に文書化しています。保存は取り消せない唯一の操作であり、別の承認段階を必要とするためです。

### Alternatives considered

**Ableton Link / OSC 経由** — Link はテンポ同期のみで、トラックや Clip を作れません。

**`.als` ファイルを直接生成** — 未文書の gzip XML 形式で、バージョン間の互換保証がなく、既存 Set の安全なマージができません。Live 起動中の Set を扱えないため「既存 Setを扱う」要件を満たせません。

**Python Remote Script（MIDI Remote Scripts）** — Live Object Model へのアクセスは可能ですが、Live の内部 API に依存し、Ableton が公式にサポートしていません。Max for Live は公式にサポートされた LOM アクセス手段です。

**HTTP サーバーを Live 側に置く** — Max の `[js]` は HTTP サーバーを立てられません。Node for Max を使う選択肢はありますが、依存が重く、ポート管理とプロセス寿命が複雑になります。loopback UDP は依存ゼロで済みます。
