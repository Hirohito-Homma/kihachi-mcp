# ISSUE-0021 — save_live_set（設計のみ、実装しない）

Status: 設計のみ。**このリポジトリに実装は含まれていません。**

## なぜ分離するか

保存は KIHACHI が行える操作のうち、唯一取り消せないものです。

Session View と Arrangement View の変更はすべて Live の Undo で戻せます。未保存であれば、最悪の場合でも Live を保存せずに閉じれば利用者の Set は無傷です。これが現在の復旧手順（[RECOVERY.md](../RECOVERY.md)）の土台になっています。

保存するとその土台が消えます。利用者のディスク上の `.als` が上書きされ、Undo 履歴も意味を失います。したがって保存は Session / Arrangement の承認とは別の承認段階を必要とします。同じ承認で保存まで通してしまうと、「パターンを作ってよい」という承認が「制作物を上書きしてよい」という承認に化けます。

安全規則13「保存操作は別承認にする」がこれに対応します。

## 現在の動作

KIHACHI は Set を保存しません。`kihachi.device.js` には保存呼び出しが存在せず、`tests/test_maxforlive_contract.py::test_the_device_never_saves_the_set` がこれを固定しています。

保存は Live 側で人間が行います。

## 設計

### 操作

`save_live_set` を `SUPPORTED_OPS` へ追加し、専用の計画種別として扱います。他の操作と同じ計画に混ぜません。混ぜると1回の承認で構造変更と保存が同時に通るためです。

```text
op:                 save_live_set
target:             {"scope": "song"}
arguments:          {"expected_set_path": "<正規化済みパス>"}
preconditions:      not_recording
                    transport_stopped
                    set_path_matches      新規
                    set_has_no_unmanaged_pending_changes  新規（下記）
destructive:        true
expected_readback:  {"set_path": "...", "saved_at_fingerprint": "..."}
```

### MCP tool

```text
request_live_save(live_state=None) -> 承認要求
execute_live_save(request, approved=False, approval_token="") -> receipt
```

既存の `request_live_execution` / `execute_live_request` を使い回しません。ツールを分けることで、承認トークンの用途が保存に限定されます。保存用トークンは別ファイル（`pending-save-approval.json`）へ書き出します。

### 追加が必要な precondition

**`set_path_matches`** — 計画時と実行時で `set_path` が同一であること。利用者が途中で別の Set を開いていた場合に、意図しない Set を上書きしないためです。

**未保存 Set の扱い** — `set_path` が空の Set（Live で一度も保存していない新規 Set）では、保存先を KIHACHI が決めることになります。これは利用者のファイルシステムへの新規書き込みであり、上書きとは別の判断です。現時点の設計では**未保存 Set の保存は拒否**します。利用者が先に Live で一度保存し、保存先を自分で決めた Set のみを対象にします。

### 冪等性

保存は他の操作と冪等性の意味が違います。同じ内容を2回保存しても結果は同じですが、「1回目の保存後に利用者が編集し、2回目の保存で巻き戻る」ことが起こり得ます。したがって保存の `idempotency_key` も他と同様に1回で使い切り、再実行には新しい承認を要求します。

### 読戻し

保存の readback は原理的に弱いです。Live Object Model から「保存が成功したか」を直接確認する手段が限られています。確認できるのは次の程度です。

- `set_path` のファイルが存在し、mtime が実行時刻以降であること
- 保存後の `set_fingerprint` が保存前と一致すること（保存は構造を変えないため）

mtime の確認は Python 側（ファイルシステム）で行えます。ただし Live が実際にそのファイルへ書いたかは推論にとどまります。**読戻しが弱いことを踏まえ、receipt では `verified` を返さず、新しい状態 `saved_unverified` を導入するか、`verification_failed` 相当として扱うかを実装時に決める必要があります。** 安全規則14に従い、確認できないものを `verified` にはしません。

これが「設計のみで実装しない」最大の理由です。読戻しの設計が固まっていません。

### バックアップ

保存前に `.als` のコピーを作る案があります。ただしこれは利用者のディスクへの書き込みを増やし、「音声ファイル、生成物、既存 Ableton Set を削除しない」規則とは別に、「勝手にファイルを増やさない」という期待に触れます。実装する場合は明示的なオプトインにします。

## 実装前に決めること

1. 読戻しの根拠。`verified` を返せるのか、専用状態を作るのか
2. 未保存 Set を拒否し続けるか、保存先指定を承認対象に含めるか
3. バックアップコピーを作るか、作る場合の保存先と削除方針
4. `Save As` を扱うか（別 Issue にすべき可能性が高い）

## 実装しない判断

上記4点が未決のまま実装すると、取り消せない操作に対して「確認できていないのに完了と報告する」経路ができます。それは安全規則14の違反そのものです。

読戻しの設計が固まるまで、保存は人間が Live 上で行います。
