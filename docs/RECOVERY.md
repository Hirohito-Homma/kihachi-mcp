## 障害時の復旧手順

KIHACHI は自動再試行しません。失敗した状態は人間が判断して解消します。

前提として、KIHACHI は Set を保存しません。Live の Undo 履歴と未保存状態が最大の味方です。

### 判断の起点は receipt の `status`

| `status` | Set の状態 | 最初にやること |
| --- | --- | --- |
| `approval_required` | 変更なし | 承認トークンを渡して実行、または計画を破棄 |
| `blocked` | 変更なし | `error` と `conflicts` を読み、原因を解消して再計画 |
| `unavailable` | 変更なし | Live とデバイスの起動状態を確認 |
| `partially_applied` | **一部変更あり** | 下記「部分適用からの復旧」 |
| `verification_failed` | 変更あり、期待と不一致 | 下記「読戻し不一致からの復旧」 |
| `verified` | 変更あり、確認済み | 復旧不要 |

`blocked` と `unavailable` と `approval_required` では Set は変わっていません。実行前のガードで止まっているためです。

### 部分適用からの復旧

`partially_applied` は「`completed_operations` までは適用され、`failed_operation` 以降は適用していない」という意味です。残りは自動実行されません。

1. `receipt.completed_operations` を読み、どこまで進んだかを確認します。
2. `receipt.failed_operation` と `receipt.error` を読み、なぜ止まったかを確認します。
3. Live で `Cmd+Z` / `Ctrl+Z` を必要回数押し、KIHACHI が作った要素を戻します。回数は `completed_operations` の件数が目安ですが、Live の Undo 粒度は操作と1対1ではないため、**目視で確認しながら戻してください。**
4. Undo で戻しきれない場合、`[KIHACHI]` および `[K:` マーカーの付いた要素だけを手で削除します。マーカーのない要素は KIHACHI が作ったものではありません。
5. 原因を解消してから `inspect_live_state` → `create_live_mutation_plan` をやり直します。

同じ計画を再実行することはできません。`idempotency_key` は実行試行の時点で使用済みになっているため、`blocked` になります。これは意図した動作です。部分適用の上に同じ計画を重ねると二重生成になるためです。

### 読戻し不一致からの復旧

`verification_failed` は「操作は全部通ったが、Live が返した値が計画と違った」という意味です。Set は変更されています。

1. `receipt.mismatches` を読みます。`field_name` / `expected` / `observed` が出ています。
2. `observed` が妥当なら、Live 側の丸めや非同期更新が原因の可能性があります。`verify_live_execution` を呼び直して、時間差で一致するかを確認します。
3. それでも一致しない場合、計画の前提が間違っています。Set を Undo で戻し、`inspect_live_state` から再計画します。
4. `verification_failed` を `verified` として扱わないでください。

### 接続が切れた

`unavailable` または `disconnected`。

1. Live が起動しているか確認します。
2. デバイスがトラックにロードされているか確認します。
3. `[js kihachi.device.js]` にエラーが出ていないか、Max コンソールを確認します。
4. サーバーを再起動した場合、トークンが変わっています。パッチから `reload` メッセージを `[js]` へ送ってください。
5. ポートが他プロセスに使われていないか確認します。`cannot bind` が出る場合は `KIHACHI_LIVE_REPLY_PORT` を変更し、パッチの `[udpsend]` も合わせます。

`disconnected` が**操作の適用中**に起きた場合は、その操作が適用されたかどうかが原理的に不明です。この場合 receipt は `partially_applied` になります。適用済みとも未適用とも仮定せず、`inspect_live_state` で実際の状態を読んでから判断してください。

### 承認トークンを失った

1. `approval_token_path` のファイルを確認します。
2. ファイルがない場合、その計画は実行できません。`request_live_execution` を呼び直して新しい計画と新しいトークンを作ります。
3. 古いトークンは再利用されません。`execute_live_request` の試行時に破棄されます。

### 承認状態が壊れた

`KIHACHI_LIVE_APPROVAL_PATH` を設定している場合、そのファイルが承認記録と使用済み `idempotency_key` を持っています。

- ファイルを削除すると、使用済みキーの記録も消えます。**削除すると二重実行の防止が効かなくなります。** 削除する場合は、先に Live の状態を `inspect_live_state` で確認してください。
- 設定していない場合、記録はプロセス内のみです。サーバー再起動で消えます。

### Live が応答しなくなった

1. Live を強制終了する前に、保存するかどうかを判断してください。KIHACHI は保存していないので、**強制終了すれば KIHACHI の変更は消えます。**
2. 保存したい場合は Live 側で手動保存します。
3. 再起動後、`inspect_live_state` で状態を読み直してから再計画します。

### 利用者の制作物が変わってしまった

設計上こうならないようになっています（`[KIHACHI]` マーカーのない要素は読み取り専用扱い、既存 Clip は上書きせず `conflict`、使用中の Arrangement 領域へは配置しない）。それでも起きた場合は不具合です。

1. Live を保存せずに Undo で戻します。
2. `receipt` と `plan` の JSON を保存します。
3. どの `operation_id` が原因かを `readback` から特定し、Issue として記録してください。
