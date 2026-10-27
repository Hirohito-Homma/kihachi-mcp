# KIHACHI Studio

ローカル専用のWeb UI。`Start.command`をダブルクリックし、http://127.0.0.1:8421 を開きます。起動したターミナルを閉じるとサーバーが停止します。

1. Liveを起動し、Control SurfaceにAbletonGPTを選択します。
2. 曲の指定（例：Dub、110 BPM、D#m、32小節）を入力して「曲を生成する」。
3. 「Liveへの追加内容を確認」でテンポと追加パートを確認。
4. Liveを停止し「この内容でLiveに追加」。終了後はLiveで再生します。

MIDI作曲とLive内蔵音源のワークフローです。ACE-Stepの音声生成やボーカル生成はこのUIには含みません。Live全体のテンポを変更し、新規トラックを追加します。既存トラックの削除や上書きは行いません。

曲はこのフォルダの `songs/<固有ID>/` に保存。画面のMIDIリンクからダウンロードできます。画面再読込後の履歴復元は未対応ですが、保存したファイルは残ります。追加が途中で失敗すると再実行を禁止し、各曲の `execution.txt` と `job.json` に調査用の記録を残します。手動でattempt.jsonを消して再実行しないでください。

使用する既存エンジン：
- `KIHACHI_ENGINE_DIR` (default: `~/MusicAI/kihachi-music-ai`)
- `ABLETONGPT_DIR` (default: `~/MusicAI/abletongpt`)

エンジンの開発中ファイルは変更していません。このUIは独立した補助アプリです。別の場所を使う場合は `KIHACHI_ENGINE_DIR` と `ABLETONGPT_DIR` を設定してください。

検証：既存Webテスト12件、新規ガードテスト4件が成功。画面から実際に32小節の3パートMIDIを生成し、接続中Liveの1トラックに対する追加計画の取り込みまで成功。Liveへの実追加と発音は未検証です。例の曲を自動追加せず、ユーザーの曲指定と追加操作を待つ状態です。
