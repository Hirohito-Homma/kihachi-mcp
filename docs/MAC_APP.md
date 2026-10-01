# macOSアプリのビルドと配布

KIHACHI MUSIC AIの既存Studioを、Intel Mac上で `.app` にまとめる手順です。アプリはローカルの制作画面をブラウザで開きます。Ableton LiveやローカルOllamaを使う機能には、それぞれ別途インストールと設定が必要です。

## このMacで試す

```bash
cd /Users/user/MusicAI/kihachi-mcp
uv pip install --python .venv/bin/python 'pyinstaller>=6.18,<7'
.venv/bin/python scripts/build_macos_app.py
```

生成先は `dist/macos/<バージョン>-<CPU>/KIHACHI MUSIC AI.app` です。Finderでアプリを開くとStudioが起動します。開けない場合はまずターミナルから実行し、表示されるエラーを確認します。

```bash
'dist/macos/0.1.0-x86_64/KIHACHI MUSIC AI.app/Contents/MacOS/KIHACHI MUSIC AI' --no-browser
```

この手順で作るアプリはアドホック署名です。ほかのMacに信頼されたアプリとして配布するためのDeveloper ID署名・Apple公証とは異なります。

## ほかのMacへ配布する

前提はApple Developer Programの利用権限、秘密鍵と対になった有効な **Developer ID Application** 証明書、Apple公証用の認証情報です。証明書と認証情報はチャットやリポジトリに貼らず、macOSのキーチェーンに設定してください。

1. `security find-identity -v -p codesigning` でDeveloper ID Applicationの署名IDを確認します。
2. `xcrun notarytool store-credentials KIHACHI_NOTARY --apple-id <Apple ID> --team-id <Team ID>` を対話的に実行し、アプリ専用パスワードをキーチェーンに保存します。App Store Connect APIキー方式を使う場合は `xcrun notarytool help store-credentials` を参照してください。
3. 以下を実行します。署名版は `-developer-id` が付く別フォルダに作られ、試用版を上書きしません。同じ種類の既存ビルドも上書きしないため、再ビルド時は新しいバージョンに更新してください。

```bash
.venv/bin/python scripts/build_macos_app.py --signing-identity 'Developer ID Application: <名前> (<Team ID>)'
bash scripts/notarize_macos_app.sh 'dist/macos/0.1.0-x86_64-developer-id/KIHACHI MUSIC AI.app' KIHACHI_NOTARY
```

公証が承認されるとアプリにチケットをstapleし、同じフォルダに `KIHACHI MUSIC AI-notarized.zip` を作ります。配布前に別のMacでインストール・起動・制作画面の表示を確認してください。Intel版とApple Silicon版は、それぞれ対応するMacで別途ビルド・テストします。公証エラーでは `xcrun notarytool log <submission-id> --keychain-profile KIHACHI_NOTARY` を確認します。

Appleの[Developer ID案内](https://developer.apple.com/developer-id/)と[公証手順](https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution)も参照してください。
