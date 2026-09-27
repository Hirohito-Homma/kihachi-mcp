## KIHACHI Live Device

`kihachi.device.js` は Ableton Live を KIHACHI MCP から安全に自動操作するための Max for Live デバイス本体です。

### 未完了部分の明示

**`.amxd` バイナリはこのリポジトリに含まれていません。** `.amxd` は Max が生成する独自バイナリ形式で、テキストから正しく生成できないため、偽のデバイスファイルを置くことを避けました。含まれているのは次の3点です。

1. Live Object Model を操作する JavaScript 実装（`kihachi.device.js`、完成）
2. メッセージプロトコル仕様（[docs/MAXFORLIVE.md](../docs/MAXFORLIVE.md)、完成）
3. 下記のパッチ作成・パッケージ化手順（手作業、**利用者側で1回だけ実施が必要**）

あわせて、下記「デバイスローダー」節のパッチ配線も未完了部分です。`load_live_device` は JavaScript からブラウザを操作できないため、パッチ側の配線が必須です。

### 必要環境

- Ableton Live 11 以降（Live 12 で設計、`MIN_SUPPORTED_LIVE_MAJOR = 11`）
- **Max for Live（必須要件）**。Live Suite 同梱、または Live Standard + Max for Live アドオン
- macOS または Windows

Live Intro には Max for Live がないため、KIHACHI の Live 自動操作は利用できません。

### パッチ作成手順

1. Live で MIDI トラックを1つ作り、`Max Audio Effect` をロードします。
2. デバイスの編集ボタン（鉛筆アイコン）で Max エディタを開きます。
3. 既存オブジェクトを削除し、次のオブジェクトを配置します。

```
[udpreceive 17771]
|
[fromsymbol]
|
[js kihachi.device.js]
|                        \
[tosymbol]                [route status load_device]
|                         |
[prepend set]             (下記「デバイスローダー」へ)
|
[udpsend 127.0.0.1 17772]
```

4. `kihachi.device.js` を Max の検索パスへ置きます。最も確実なのは、デバイスと同じフォルダに置くことです。
5. `[js kihachi.device.js]` をダブルクリックし、エラーが出ないことを確認します。
6. デバイスを `KIHACHI Live Device.amxd` として保存します。保存先は Live の User Library 配下を推奨します。
   - macOS: `~/Music/Ableton/User Library/Presets/Audio Effects/Max Audio Effect/`
   - Windows: `%USERPROFILE%\Documents\Ableton\User Library\Presets\Audio Effects\Max Audio Effect\`

### デバイスローダー（未完了・パッチ側で配線が必要）

`load_live_device` だけは JavaScript から実行できません。Live Object Model には `[js]` から使えるブラウザ列挙 API がないためです。`kihachi.device.js` は右アウトレットへ `load_device <track_index> <device_name>` を送り、デバイス数が増えたかを検査します。増えていなければ操作は失敗として報告され、`verified` にはなりません。

パッチ側では次のいずれかで配線してください。

- `[live.object]` + `[live.path]` で対象トラックを選び、事前に用意した `.adg` / `.adv` プリセットを `[live.browser]` 相当の機構からロードする
- 各 stock デバイスの `.adv` プリセットを User Library に用意し、パッチからパス指定でロードする

自動フォールバックは実装しないでください。存在しないデバイスは `blocked` として返すのが仕様です。

### トークン受け渡し

Python 側が起動ごとにセッショントークンを生成し、所有者のみ読める（`0600`）ハンドシェイクファイルへ書き出します。デバイスはリクエストごとにこれを読み、トークンが一致しなければ `unauthorized` を返します。

- macOS: `~/Library/Application Support/KIHACHI/live-bridge.json`
- Windows: `%LOCALAPPDATA%\KIHACHI\live-bridge.json`

このファイルは Git にコミットされません。サーバーを再起動したら、パッチから `reload` メッセージを `[js]` へ送ってトークンキャッシュを破棄してください。

### ポート

既定は loopback 固定の `127.0.0.1` のみです。外部インターフェースへは bind しません。

| 方向 | 既定ポート | 環境変数 |
| --- | --- | --- |
| KIHACHI から Live | 17771 | `KIHACHI_LIVE_BRIDGE_PORT` |
| Live から KIHACHI | 17772 | `KIHACHI_LIVE_REPLY_PORT` |

ポートを変更した場合は、パッチの `[udpreceive]` と `[udpsend]` も同じ値へ変更してください。

### 動作確認

1. Live で Set を開き、デバイスを任意のトラックへロードします。
2. KIHACHI MCP から `inspect_live_state` を呼びます。
3. `health.connected` が `true`、`snapshot.live_version` が実際の Live バージョンになることを確認します。

この時点では制作操作は何も行われません。以降の手順は [docs/MANUAL_LIVE_TESTS.md](../docs/MANUAL_LIVE_TESTS.md) を参照してください。

### 制約

- CI では Max も Live も起動しません。`tests/test_maxforlive_contract.py` はメッセージ契約の一致のみを検証します。
- このデバイスは Set を保存しません。保存は別 Issue（[docs/issues/ISSUE-0021.md](../docs/issues/ISSUE-0021.md)）として設計のみ文書化しています。
- 外部プラグイン（VST3 / AU / CLAP）は対象外です。
