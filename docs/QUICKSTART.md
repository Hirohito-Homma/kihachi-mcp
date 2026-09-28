# QUICKSTART

日本語の制作指示から MIDI 候補を作り、確認・承認してから Ableton Live の専用トラックへ送り、Live から読み戻して検証するまでの最短手順です。有料APIは使いません。

## 1. セットアップ（初回のみ）

```bash
./scripts/setup.sh
```

uv が必要です。Ollama は任意です（なくても「AIなし（既定解釈）」で制作できます）。KIHACHI はモデルを自動でダウンロードしません。→ [OLLAMA_SETUP.md](OLLAMA_SETUP.md)

## 2. Ableton Live の準備

Live を起動し、KIHACHI Live Device（Max for Live）を任意のトラックに置きます。→ [ABLETON_SETUP.md](ABLETON_SETUP.md)

## 3. 制作画面を開く

```bash
uv run kihachi start
```

ブラウザで http://127.0.0.1:8765/ が開きます。上部の表示が `Ollama: READY` / `Live: READY` になっていれば準備完了です。

## 4. 曲を作る

制作指示に書いて「候補を生成」を押します。例:

```text
110 BPM、D# minor。Mutation Funk × Dub × Tech House。
ファンキーなスラップベース。Ghost notes。Octave movement。
Dub chord。Mutation synth。Swing 54%。約5分。
```

## 5. 確認 → 承認 → 送信 → 検証

1. 「プロジェクト」で構成（セクション）とトラックを確認します。
2. 「AIレビュー」の指摘を見て、必要なら「選んだ指摘を修正」→ 修正案の BEFORE / AFTER を見て「採用」。
3. 「この候補を承認」。
4. 「適用内容を確認」（ドライラン。Live は変わりません）。
5. 「この内容でLiveへ適用」（1回だけ。自動再試行なし）。
6. 結果の `ABLETON VERIFICATION` がすべて PASS / SKIP なら `PROJECT READY` です。

## CLI だけで行う場合

```bash
uv run kihachi create "122 BPM、Dマイナー、64小節のTech House"
uv run kihachi review <候補ID>
uv run kihachi approve <候補ID>
uv run kihachi ableton dry-run <候補ID>
uv run kihachi ableton execute <候補ID>   # 確認の質問に y と答えると1回だけ送信
uv run kihachi ableton verify <候補ID>
```

Live に触れる手順（dry-run / execute / verify）は、起動中の制作画面を通して行います。候補IDは先頭数文字で指定できます。

困ったときは [TROUBLESHOOTING.md](TROUBLESHOOTING.md)。
