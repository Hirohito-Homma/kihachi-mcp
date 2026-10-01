# Manus から Ableton へ 1 曲置く

Manus は今まで通り Gateway だけを見ます。Live と AbletonGPT はループバックのままです。

```text
Manus
  → HTTPS + Bearer
  → Cloudflare Tunnel
  → 127.0.0.1:8787/mcp
  → AbletonGPT 127.0.0.1:9877
  → Ableton Live

Studio 127.0.0.1:8765 はこの経路に入れません。
```

Studio の `create_song` は別の Tool です。Live の Arrangement に置く Tool は `produce_ableton_song` です。

## 時間

AbletonGPT の時間は beats（4 分音符）です。

- クリップのノートは `start_time` と `duration`
- Arrangement の位置は `destination_time_beats`
- この経路は 4/4 だけを受けます。1 小節は 4 beats です
- 小節番号は 1 始まりです。1 小節目は beat 0 です

秒やサンプルへの換算はしていません。拍子が 4/4 でない Set には書きません。

## 計画

`produce_ableton_song(dry_run=true)` は Live を呼びません。戻り値には次があります。

- SongSpec（テンポ、キー、小節数、トラック名）
- パターンごとのノート数と先頭数音
- Arrangement の区間
- クリップの長さ（beats）
- `plan_id`

初期プリセットは次だけです。

| 項目 | 値 |
| --- | --- |
| title | KIHACHI MANUS TEST 01 |
| tempo | 110 |
| key | D# minor |
| 拍子 | 4/4 |
| bars | 32（64 と 128 も同じ形の繰り返し） |
| style | mutation-funk-dub-tech-house |
| swing | 0.54 |
| seed | 1 |

スタイルとキーを変える呼び出しは拒否します。別の曲にするときは `title` を変えます。同じ title は同じトラック名になり、2 回目は足しません。

## 中身

パターンは seed で決まります。乱数で毎回変えません。seed が変えるのはスラップのゴーストのベロシティだけです。

| トラック | 置き方 |
| --- | --- |
| KICK | 4 つ打ち。アウトロは前半だけ |
| DRUMS | 2 拍と 4 拍のスネア / クラップ。オフビートのハット。16 分の裏は swing |
| PERCUSSION | 小節ごとにずらしたシンコペーション |
| SLAP BASS | シンコペーション、ゴースト、オクターブ、休符 |
| SUB BASS | ルートの支え。スラップとは別の MIDI |
| DUB CHORDS | D# minor の短いスタブ。長いパッドにはしない |
| SYNTH STABS | メインのアクセント |
| ARP | 16 分。バスと同じリズムにはしない |
| VOCODER CARRIER | 単音の 8 分。インストゥルメントはロードしない |
| VOCAL | MIDI のガイド。録音ボーカルではない |
| FX | 短いヒット。デバイスは載せない |

空のトラックだけでは完成にしません。11 本ともノートがあります。

32 小節は INTRO（1–8）、GROOVE（9–16）、MAIN（17–24）、OUTRO（25–32）です。64 と 128 はこの 32 小節を繰り返します。

音量は Live の 0.0–1.0 で、0.85 前後のユニティより下に置きます。既存トラックの音量は変えません。

## 人が確認してから書く

1. Manus に、まず dry-run と伝える。
2. `produce_ableton_song` が `dry_run=true` で計画を返す。Live は変わらない。
3. 内容を見てから、`confirm=true` かつ `dry_run=false` で 1 回だけ実行する。
4. `confirm` が無い呼び出しは `confirmation_required` を返し、Live に送りません。

再生中は書きません。`ableton_stop` も自動では呼びません。テンポはセット全体に効きます。`confirm=true` のときだけ、目的の BPM と違う場合に変えます。既存トラックの削除はしません。

## 失敗したとき

操作ごとに `SUCCESS` / `FAILED` / `SKIPPED` が返ります。1 つでも失敗すると `ok` は false です。その時点より後は `SKIPPED` です。

削除コマンドは無いので、戻しはしません。できたトラックとクリップは残ります。同じ title でもう一度 `confirm=true` すると、同名トラックは増やさず、ノートが一致するクリップは上書きしません。一致しないクリップも上書きせず `FAILED` にします。

書き終えたあとに Live から読み戻します。テンポ、トラック名、クリップ、ノート数、Arrangement の長さが計画と揃ったときだけ `ok: true` です。

## 実機で見ること

Arrangement に、title で始まるトラックが beat 0 から 128 beats（32 小節）置いてあること。Session のスロット 0 に同じクリップがあること。

`rename_track` は Remote Script に無いので、名前は作成時に付けます。デバイスの自動ロード、既存 Set の削除、上書き保存はしません。
