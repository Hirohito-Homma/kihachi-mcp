# OLLAMA SETUP

KIHACHI はローカルの Ollama だけを AI として使います。有料APIへのフォールバックはありません。Ollama がなくても、制作指示に明示された値（テンポ・キー・小節・スイングなど）と既定値で制作できます。

## 導入

1. https://ollama.com から Ollama を入れます（Apple Silicon / Intel Mac 共通）。
2. 別のターミナルで `ollama serve`（アプリ版なら起動するだけ）。
3. モデルは**自分で**選んで入れます。KIHACHI は自動でダウンロードしません。

```bash
ollama pull gemma4:latest   # 開発時に使ったモデル（約9.6 GB）。容量と時間に注意
ollama list
```

目安: Intel i9 / 32 GB で1回の解釈に約50〜60秒かかりました。小さいモデルほど速く、JSON の崩れが増えます。

## 設定

優先順位は「制作画面で保存した設定 → 環境変数 → 既定値」です。

| 環境変数 | 既定値 | 意味 |
| --- | --- | --- |
| `KIHACHI_AI_PROVIDER` | `ollama` | `ollama` または `deterministic`（AIなし） |
| `KIHACHI_OLLAMA_URL` | `http://127.0.0.1:11434` | ローカル（127.0.0.1 / localhost）のURLだけ受け付けます |
| `KIHACHI_OLLAMA_MODEL` | `gemma4:latest` | 導入済みのモデル名 |

制作画面の「システム」→「AI」で、導入済みのモデルだけを選べます。「接続テスト」で応答を確認し、「保存」で `~/Library/Application Support/KIHACHI/settings.json` に書きます（秘密情報は含みません）。

## AI の応答が崩れたとき

構造化出力は次の順で扱います。

1. JSON として読む
2. コードフェンスや前後の文章を取り除いて1つのオブジェクトだけを読む（修復）
3. スキーマで検証する
4. だめならもう一度問い合わせる（制作画面は合計2回。コード上の上限は3回）
5. それでもだめなら、明示指定と既定値で組み立て、その旨を画面の「未対応・矛盾・曖昧」に出す

キャンセルは再試行しません。Ollama が止まっていても制作画面は落ちません。

## 状態の確認

```bash
uv run kihachi doctor
```

`Ollama` と `Model` の行が `PASS` なら使えます。MCP からは `ollama_status` ツールで同じ情報を読めます。
