# ISSUE-0023 — Live純正デバイスローダー

Status: コードと契約テストは完了。**実機未検証**（[MANUAL_LIVE_TESTS.md](../MANUAL_LIVE_TESTS.md) の段階8は未実施）。

関連: [ISSUE-0020](ISSUE-0020.md)「未完了部分」3、[ROADMAP.md](../ROADMAP.md)、[maxforlive/README.md](../../maxforlive/README.md)「純正デバイスローダー」

## 目的

`load_live_device` 操作で、許可リスト内の Live 純正デバイスを対象トラックへ自動ロードします。パッチ側の非公式ブラウザー配線は使わず、公式 Live Object Model だけで行います。

## 実装状況

- `maxforlive/kihachi.device.js` の `loadDevice` は、Live 12.3 で公式 Live Object Model に追加された `Track.insert_device` を呼び、対象トラックの末尾へ挿入します。挿入前後のデバイス数と、挿入されたデバイス名を読み戻します。デバイス数が増えなければ失敗として返します。
- Live 11〜12.2 では利用可能一覧を `available: false` とし、`device_available` 前提条件によって計画段階で拒否します。
- 許可リスト（`AVAILABLE_DEVICES`）にないデバイスや、そのエディションの Live に存在しないデバイスは失敗します。**別デバイスへの自動置換は行いません。**
- 対象は Live 純正デバイスだけです。Max for Live デバイスと外部プラグイン（ISSUE-0022）は対象外です。
- `AVAILABLE_DEVICES` は候補許可リストです。Live には `[js]` から使える公式ブラウザ列挙 API がないため、一覧だけではエディションごとの実在を保証しません。

## 契約テストの範囲

テストは fake transport と JavaScript ソースの静的検査だけです。Ableton Live も Max も起動しません。

- `tests/test_maxforlive_contract.py::test_stock_device_loading_uses_the_official_live_12_3_api` — 12.3 の下限定数、`track.call("insert_device", ...)` の使用、旧 `load_device` 出力が残っていないこと
- `tests/test_maxforlive_contract.py::test_the_device_reports_only_catalogued_stock_devices` — デバイス側の許可リストが Python 側のカタログと一致すること
- `tests/test_live_mutation_planner.py::test_a_missing_stock_device_blocks_the_plan_without_substitution` — 存在しないデバイスで計画が `blocked` になり、置換しないこと

これらは Live が `insert_device` を受け付けること、挿入後の読戻しが正しいことを証明しません。

## 実機未確認の境界

次は実機での証拠がなく、未検証です。

- Live 12.3 以降で `insert_device` が実際にデバイスを挿入し、`receipt.readback` の `device_name` が Live の表示と一致すること
- 存在しないデバイスを要求したとき、実機で `blocked` になり別デバイスに置き換わらないこと
- Live 11〜12.2 の実機で計画段階の拒否が働くこと
- Live のエディション差による挿入可否

2026-10-18 の実機記録（段階9・10、衝突プレビュー）にデバイスロードの確認は含まれていません。

## 手動確認手順

[MANUAL_LIVE_TESTS.md](../MANUAL_LIVE_TESTS.md) の「段階8: Live標準デバイスロード」を、テスト専用の新規 Set で実行します。実施したら同文書の「記録」に日付・Live バージョンとエディション・OS・各チェック項目の結果を残します。全項目の結果が記録されるまで、この Issue を実機確認済みとは扱いません。
