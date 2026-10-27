# macOSアプリのビルドと配布

KIHACHI MUSIC AIの既存Studioを、Intel Mac上で `.app` にまとめる手順です。アプリはローカルの制作画面をブラウザで開きます。Ableton LiveやローカルOllamaを使う機能には、それぞれ別途インストールと設定が必要です。

## このMacで試す

```bash
cd /Users/user/MusicAI/kihachi-mcp
uv pip install --python .venv/bin/python 'pyinstaller>=6.18,<7'
.venv/bin/python scripts/build_macos_app.py
```

生成先は `dist/macos/<バージョン>-<CPU>/KIHACHI MUSIC AI.app` です。同じフォルダの `scripts/Liveデバイスをインストール.command` と `maxforlive/` に、Live用の実デバイスと2つのJSも入ります。Liveを使うMacでは、この `.command` をFinderから開き、Ableton User Libraryを選んで導入してください。導入先に異なるファイルがあればバックアップします。Finderでアプリを開くとStudioが起動します。開けない場合はまずターミナルから実行し、表示されるエラーを確認します。

既存ビルドを残したまま検証する場合は `--output-root /private/tmp/kihachi-build-check` を指定できます。作成先はそのフォルダ内の `<バージョン>-<CPU>/` です。

ビルド後、次のスモークテストでアプリ自身の起動、AIなしの8小節候補生成、保存、再起動後の読戻し、試聴WAV取得を確認できます。一時データフォルダと空きポートを使い、Liveへの送信や有料APIは行いません。

```bash
.venv/bin/python scripts/smoke_macos_app.py 'dist/macos/0.1.0-x86_64/KIHACHI MUSIC AI.app'
```

長尺の構成も確認するときは末尾に `--bars 128` を付けます。スモークテストは明示したテンポ・キー・小節数、セクションの連続性、ノートの存在も確認します。音楽的な出来やLiveの再生品質の判定には、別途試聴が必要です。

公証前にZIPの中身だけ確認する場合は、`bash scripts/package_macos_release.sh '<作成先>/KIHACHI MUSIC AI.app' /private/tmp/kihachi-release-preview.zip` を実行します。このプレビューZIPはアドホック署名のままで、公証済み配布物にはなりません。`ditto -x -k /private/tmp/kihachi-release-preview.zip /private/tmp/kihachi-release-unpacked` で展開し、アプリ・`scripts/`・`maxforlive/` を確認できます。

展開後も `.venv/bin/python scripts/smoke_macos_app.py '/private/tmp/kihachi-release-unpacked/KIHACHI MUSIC AI.app'` を実行し、ZIPにした後のアプリが候補を生成・保存・再読込できることを確認してください。

```bash
'dist/macos/0.1.0-x86_64/KIHACHI MUSIC AI.app/Contents/MacOS/KIHACHI MUSIC AI' --no-browser
```

この手順で作るアプリはアドホック署名です。ほかのMacに信頼されたアプリとして配布するためのDeveloper ID署名・Apple公証とは異なります。

## ほかのMacへ配布する

前提はApple Developer Programの利用権限、秘密鍵と対になった有効な **Developer ID Application** 証明書、Apple公証用の認証情報です。証明書と認証情報はチャットやリポジトリに貼らず、macOSのキーチェーンに設定してください。

`security find-identity -v -p codesigning` が `0 valid identities found` と表示する場合、このMacではDeveloper ID署名版をまだ作れません。証明書と秘密鍵をキーチェーンへ導入した後に再確認してください。アドホック署名のプレビューZIPはこの条件がなくてもローカル評価できますが、正式配布の代用にはなりません。

1. `security find-identity -v -p codesigning` でDeveloper ID Applicationの署名IDを確認します。
2. `xcrun notarytool store-credentials KIHACHI_NOTARY --apple-id <Apple ID> --team-id <Team ID>` を対話的に実行し、アプリ専用パスワードをキーチェーンに保存します。App Store Connect APIキー方式を使う場合は `xcrun notarytool help store-credentials` を参照してください。
3. 以下を実行します。署名版は `-developer-id` が付く別フォルダに作られ、試用版を上書きしません。同じ種類の既存ビルドも上書きしないため、再ビルド時は新しいバージョンに更新してください。

```bash
.venv/bin/python scripts/build_macos_app.py --signing-identity 'Developer ID Application: <名前> (<Team ID>)'
bash scripts/notarize_macos_app.sh 'dist/macos/0.1.0-x86_64-developer-id/KIHACHI MUSIC AI.app' KIHACHI_NOTARY
```

公証が承認されるとアプリにチケットをstapleし、同じフォルダに `KIHACHI MUSIC AI-notarized.zip` を作ります。ZIPには `.app` とLiveデバイス導入用の `scripts/`・`maxforlive/` が入ります。配布前に別のMacでZIP展開、アプリ起動、Liveデバイス導入、制作画面とLive接続を確認してください。Intel版とApple Silicon版は、それぞれ対応するMacで別途ビルド・テストします。公証エラーでは `xcrun notarytool log <submission-id> --keychain-profile KIHACHI_NOTARY` を確認します。

Appleの[Developer ID案内](https://developer.apple.com/developer-id/)と[公証手順](https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution)も参照してください。
