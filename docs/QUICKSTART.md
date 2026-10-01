# QUICKSTART

日本語の制作指示から MIDI 候補を作り、確認・承認してから Ableton Live の専用トラックへ送り、Live から読み戻して検証するまでの最短手順です。有料APIは使いません。

## 1. セットアップ（初回のみ）

Finder で `scripts/インストール.command` をダブルクリックします。ターミナルからなら次と同じです。

```bash
./scripts/setup.sh
```

uv は初回セットアップに必要です。`~/.local/bin`、`/opt/homebrew/bin`、`/usr/local/bin` も探します。Ollama は任意です（なくても「AIなし（既定解釈）」で制作できます）。KIHACHI はモデルを自動でダウンロードしません。→ [OLLAMA_SETUP.md](OLLAMA_SETUP.md)

## 2. 制作画面を開く

```bash
./scripts/kihachi-studio.command
```

または Finder で `scripts/kihachi-studio.command` をダブルクリックします。起動時はセットアップ済みの `.venv` を使うため、uv の再実行は不要です。ブラウザで http://127.0.0.1:8765/ が開きます。上部の表示が `Ollama: READY` / `Live: READY` になっていれば準備完了です。

Live が OFFLINE でも候補の生成と8小節の試聴はできます。

## 3. Ableton Live の準備（送信するときだけ）

Live に送るときは、Live を起動し、KIHACHI Live Device（Max for Live）を任意のトラックに置きます。→ [ABLETON_SETUP.md](ABLETON_SETUP.md)

## 4. 曲を作る

制作指示に書いて「候補を生成」を押します。例:

```text
110 BPM、D# minor。Mutation Funk × Dub × Tech House。
ファンキーなスラップベース。Ghost notes。Octave movement。
Dub chord。Mutation synth。Swing 54%。約5分。
```

## 5. 確認 → 承認 → 送信 → 検証

1. 「MIDI候補」の再生ボタンで最初のDropまたはChorusの8小節を聴き、「プロジェクト」で構成（セクション）とトラックを確認します。試聴は簡易音色で、Liveには触れません。
2. 「AIレビュー」の指摘を見て、必要なら「選んだ指摘を修正」→ 修正案の BEFORE / AFTER を見て「採用」。
3. 「この候補を承認」。
4. 「適用内容を確認」（ドライラン。Live は変わりません）。
5. 「この内容でLiveへ適用」（1回だけ。自動再試行なし）。
6. 結果の `ABLETON VERIFICATION` がすべて PASS / SKIP なら `PROJECT READY` です。

## 6. 音作り → MIX → マスタリング → 音圧の測定

`PROJECT READY` の後、制作画面の下の欄を上から順に使います。どれも「確認」で内容を見てから送る（1回だけ）形で、Live の再生・録音中は止まります。既存のデバイスは消しません。

1. 「エフェクト処理」: 各トラックの音源の後ろにパートごとの EQ・コンプ・サチュレーター・リバーブ・ディレイを追加。
2. 「MIX」: 各トラックの音量(dB)とパンを揃える（キックが最大、120Hz以下は中央）。
3. 「サイドチェイン」: Sub・Bass・Pad の最後に、キックで音量を沈めるコンプを追加。
4. 「マスタリング」: マスタートラックの最後に EQ → Glue Compressor → Saturator → Utility → Limiter を追加。
5. Live で WAV を書き出し、「音圧を測る」にパスを入れて音圧（LUFS）・True Peak・セクション別音圧を測ります（Live には触れません）。CLI なら `uv run kihachi measure <WAVのパス> --project <候補ID>`。

レシピ（`src/kihachi_mcp/knowledge/part_sounds.py`）を変えたときは、制作画面を再起動してから「エフェクトのつまみを設定し直す」か、「マスタリング」の「つまみを設定し直す」で送り直します。どちらもつまみだけを変え、デバイスの追加・削除はしません。CLI では次のとおりです。

```bash
uv run kihachi ableton retune <候補ID> --part Kick   # パートのエフェクト
uv run kihachi ableton master --retune               # マスタリング
```

## CLI だけで行う場合

```bash
uv run kihachi create "122 BPM、Dマイナー、64小節のTech House"
uv run kihachi review <候補ID>
uv run kihachi approve <候補ID>
uv run kihachi ableton dry-run <候補ID>
uv run kihachi ableton execute <候補ID>   # 確認の質問に y と答えると1回だけ送信
uv run kihachi ableton verify <候補ID>
uv run kihachi ableton effects <候補ID>
uv run kihachi ableton mix <候補ID>
uv run kihachi ableton sidechain <候補ID>
uv run kihachi ableton master
uv run kihachi measure <WAVのパス> --project <候補ID>
```

Live に触れる手順（`ableton` の各コマンド）は、起動中の制作画面を通して行います。`measure` は Live も制作画面も不要です。候補IDは先頭数文字で指定できます。

困ったときは [TROUBLESHOOTING.md](TROUBLESHOOTING.md)。
