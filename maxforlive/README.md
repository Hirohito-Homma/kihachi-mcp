## KIHACHI Live Device

`kihachi.device.js` は Ableton Live を KIHACHI MCP から安全に自動操作するための Max for Live デバイス本体です。現在の版は `kihachi-live-device/0.2.9` です。Live 12.4 以降では空の Drum Rack パッドへ同梱サンプルを `insert_chain` と `replace_sample` で載せます。大きな Set では `get_state` に `include_arrangement=false`、`count_session_notes=false`、音源確認時は `include_session_clips=false` を付けて状態取得します。JS を更新したら Max で `reload` するかデバイスを入れ直してください。

### 未完了部分の明示

**`.amxd` バイナリはこのリポジトリに含まれていません。** `.amxd` は Max が生成する独自バイナリ形式で、テキストから正しく生成できないため、偽のデバイスファイルを置くことを避けました。含まれているのは次の4点です。

1. Live Object Model を操作するJavaScript実装（`kihachi.device.js`）
2. 生JSON UDPを中継するNode for Max実装（`kihachi.bridge.js`）
3. メッセージプロトコル仕様（[docs/MAXFORLIVE.md](../docs/MAXFORLIVE.md)、完成）
4. 下記のパッチ作成・パッケージ化手順（手作業、**利用者側で1回だけ実施が必要**）

純正デバイスの自動ロードには、公式 Live Object Model の `Track.insert_device` が必要なため、Ableton Live 12.3 以降が必要です。Live 11〜12.2では他の操作は利用できますが、デバイスロードを含む計画は `blocked` になります。

### 必要環境

- Ableton Live 11 以降（Live 12 で設計、`MIN_SUPPORTED_LIVE_MAJOR = 11`）
- **Max for Live（必須要件）**。Live Suite 同梱、または Live Standard + Max for Live アドオン
- macOS または Windows

Live Intro には Max for Live がないため、KIHACHI の Live 自動操作は利用できません。

### パッチ作成手順

1. Live で MIDI トラックを1つ作り、`Max Audio Effect` をロードします。
2. デバイスの編集ボタン（鉛筆アイコン）で Max エディタを開きます。
3. 既存の音声／MIDI入出力は削除せず、次のオブジェクトを追加します。

```
[node.script kihachi.bridge.js @autostart 1]
|
[route request status]
|               \
|                [print KIHACHI-NODE]
[prepend msg_string]
|
[js kihachi.device.js]
|                    \
[prepend response]    [route status]
|                     |
`---- node.script     [print KIHACHI]
```

`[prepend response]` の出力は `[node.script ...]` の入口へ戻します。

4. `kihachi.device.js` と `kihachi.bridge.js` をデバイスと同じフォルダに置きます。
5. `[js kihachi.device.js]` をダブルクリックし、エラーが出ないことを確認します。
6. Max Consoleに `raw UDP ready on 127.0.0.1:17771` が出ることを確認します。
7. デバイスを `KIHACHI Live Device.amxd` として保存します。保存先は Live の User Library 配下を推奨します。
   - macOS: `~/Music/Ableton/User Library/Presets/Audio Effects/Max Audio Effect/`
   - Windows: `%USERPROFILE%\Documents\Ableton\User Library\Presets\Audio Effects\Max Audio Effect\`

### Studio を Live 内で開く（任意）

KIHACHI デバイスに「Studio」ボタンを足すと、Live の上に Studio（`http://127.0.0.1:8765`）の別ウィンドウを開けます。Max の `jweb`（組み込みブラウザ）で表示するだけなので、Studio 側の変更は要りません。Studio は先に `python -m kihachi_mcp.studio` で起動しておきます。

送受信名の `---` は、デバイス内では `001` のような番号に置き換わって表示されます。正常です。

デバイス欄の中への埋め込みは高さ（169px）が足りないため、別ウィンドウにしています。Learn ビューのレッスンは静的な文章しか表示できないため、Studio の置き場所には使えません。

#### 0. バックアップ

作業前に今の `.amxd` をコピーしておきます。User Library の外に置くと Live のブラウザに出てきません。

```bash
mkdir -p ~/Music/KIHACHI/backups && cp -p "/Volumes/NO NAME/User Library/User Library/Presets/Audio Effects/Max Audio Effect/KIHACHI Live Device.amxd" ~/Music/KIHACHI/backups/
```

#### 1. 再生を止めてエディタを開く

Live の再生を止め、KIHACHI デバイスの編集ボタン（鉛筆アイコン）で Max エディタを開きます。既存のオブジェクトと接続（`node.script` / `js` / `route` / `prepend`）には触りません。

#### 2. ウィンドウ用のサブパッチを作る

図の `[名前]` はオブジェクト（`N` キーで箱を出して名前を入力）、右端が `(` の `[内容(` はメッセージボックス（`M` キーで箱を出して内容だけを入力）です。`open(` を `N` キーの箱に入力すると、Console に `No such object` が出て動きません。

空いている場所に `p kihachi-studio` と入力してサブパッチを作り、ダブルクリックで開きます。中に次を置きます。

```
[inlet]

[loadbang]
|
[window size 80 80 1180 900, window exec(
|
[thispatcher]

[r ---kihachi-studio-url]
|
[url http://127.0.0.1:8765/(
|
[jweb @url http://127.0.0.1:8765/]
```

- `[inlet]` はどこにもつなぎません。外の `[pcontrol]` を接続するためだけに要ります。`I` キーでも置けます。置くときは ⌥⌘E でプレゼンテーション表示を切っておきます。
- `jweb` は角をドラッグして幅 1100 × 高さ 820 程度に広げます。
- `jweb` を選んで「Add to Presentation」（⌘⇧P）し、プレゼンテーションモードで左上 (0, 0) に合わせます。
- サブパッチの Patcher Inspector で「Open in Presentation」をオンにします。
- `---` で始まる送受信名は、Max がデバイスごとに置き換えます。同じ名前が他のデバイスと混ざりません。

サブパッチのウィンドウを閉じます。

#### 3. ボタンをつなぐ

メインパッチに戻り、次を置きます。

```
[button]
|
[t b b]
|     \
|      [s ---kihachi-studio-url]
[open(
|
[pcontrol]
|
[p kihachi-studio]
```

- `[t b b]` は右から出るので、先に URL を読み直し、次にウィンドウを開きます。Studio を後から起動した場合も、ボタンを押し直せば表示されます。
- 取り消し履歴やオートメーションに載せないため、`live.text` ではなく普通の `[button]` を使います。
- `[button]` と、その横に置いた `[comment]`（本文「Studio」）を選んで「Add to Presentation」し、デバイス欄に見える位置へ置きます。

#### 4. 保存と確認

1. ⌘S で保存し、エディタを**閉じます**。エディタを開いている間は、エディタ側のコピーも `node.script` を起動するため、`udp error: bind EADDRINUSE 127.0.0.1:17771` が出ます。`kihachi.bridge.js` はポートの確保をやり直さないので、閉じるまでは異常ではありません。
2. Max Console に `raw UDP bridge v2 ready on 127.0.0.1:17771` が再び出ることを確認します。エディタを閉じても `EADDRINUSE` が続く場合は、KIHACHI デバイスが Set に2つ入っていないか確認します。1つだけなのに Studio から Live につながらない場合は、デバイス内の `script stop`、続けて `script start` をクリックして起動し直します。
3. デバイスの「Studio」ボタンを押し、Studio の画面が別ウィンドウに出ることを確認します。
4. Studio を止めた状態でボタンを押すと、接続できない旨のページが出ます。Studio を起動してからボタンを押し直してください。

元に戻すときは、手順0のバックアップを元の場所へコピーし直します。

### 純正デバイスローダー

Live 12.3で公式 Live Object Model に [`Track.insert_device`](https://docs.cycling74.com/apiref/lom/track/#insert_device) が追加されました。`kihachi.device.js` はこのAPIで許可リスト内のLive純正デバイスを対象トラック末尾へ挿入し、デバイス数と実際のデバイス名を読み戻します。

追加のブラウザー配線や `.adv` プリセットは不要です。次の制約があります。

- Live 12.3以降のみ。Live 11〜12.2では利用可能一覧を `available: false` とし、計画段階で拒否します。
- Live純正デバイスのみ。Max for Liveデバイスと外部プラグインは対象外です。
- Liveのエディションに存在しないデバイスは挿入に失敗し、`verified` にはなりません。
- 別デバイスへの自動フォールバックは行いません。

現在の `AVAILABLE_DEVICES` は候補許可リストです。実機で挿入可能かは `insert_device` の結果と読戻しで確定します。

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

現在のNodeブリッジは上記ポートを固定使用します。環境変数で変更する場合は `kihachi.bridge.js` の値も合わせてください。

### 動作確認

1. Live で Set を開き、デバイスを任意のトラックへロードします。
2. KIHACHI MCP から `inspect_live_state` を呼びます。
3. `health.connected` が `true`、`snapshot.live_version` が実際の Live バージョンになることを確認します。

この時点では制作操作は何も行われません。以降の手順は [docs/MANUAL_LIVE_TESTS.md](../docs/MANUAL_LIVE_TESTS.md) を参照してください。

### 制約

- CI では Max も Live も起動しません。`tests/test_maxforlive_contract.py` はメッセージ契約の一致のみを検証します。
- このデバイスは Set を保存しません。保存は別 Issue（[docs/issues/ISSUE-0021.md](../docs/issues/ISSUE-0021.md)）として設計のみ文書化しています。
- 外部プラグイン（VST3 / AU / CLAP）は対象外です。
