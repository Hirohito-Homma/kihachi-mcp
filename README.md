# KIHACHI MUSIC AI

**Human × AI Make Better Music**

KIHACHI MUSIC AI は、人間とAIが共同で楽曲を制作するための音楽制作システムです。
単なるワンショットの音声生成ではなく、楽曲設計（SongSpec）からパート別MIDI生成、アレンジ展開、Ableton Live への自動反映、そして読み戻し検証（Verification）までを一貫してサポートします。

---

## 制作画面（KIHACHI Studio）の起動

Studio はローカル専用（Loopback: `127.0.0.1:8765`）の Web UI です。クラウド不要・オフラインで動作します。

```bash
# 制作画面を起動（ブラウザが自動で開きます）
uv run python -m kihachi_mcp.studio
```

または CLI コマンドから：

```bash
uv run kihachi studio
```

ブラウザで [http://127.0.0.1:8765/](http://127.0.0.1:8765/) にアクセスしてください。

---

## 制作ワークフロー

1. **Ableton Live の準備**
   - Ableton Live 11 / 12 を起動します。
   - `maxforlive/` にある `KIHACHI Live Device.amxd` を Live の任意のトラックに配置します（UDP 17771 / 17772 で自動接続）。
2. **楽曲の指定・設計（Studio 画面）**
   - 自然言語（例：「DubとTech House。110 BPM、D#m。太いベースと余白のあるドラム」）で曲のイメージを入力。
   - AI（ローカル Ollama / Gemini / 決定論的アルゴリズム）が SongSpec とパート別 MIDI 候補を生成。
3. **試聴とレビュー**
   - Studio 内蔵の合成プレイヤーでドラムやシンセの試聴が可能。
   - 問題点（音域衝突、リズムの過密度など）の自動レビューを確認し、必要に応じて小節単位・パート単位で修正案を作成。
4. **承認（Human Approval）と Live 展開**
   - Live への書き込みはすべて **「DRY RUN（事前計画）→ 人間の承認（Approve）→ 1回だけ送信」** の順序で実行されます。勝手な上書きや削除は行われません。
   - 送信後、Live の状態を読み戻して計画通りにクリップやノートが配置されたかを自動検証（Verify）します。

---

## 主な構成

- **`src/kihachi_mcp/studio/`**: 制作画面本体（ポート 8765）
- **`src/kihachi_mcp/services/studio_runtime.py`**: 制作ロジック・候補管理・承認ゲートの正本
- **`src/kihachi_mcp/services/jarvis/`**: 制作オーケストレーター（読み取り・修正提案）
- **`src/kihachi_mcp/tools/`**: Cursor / Codex から利用可能な FastMCP ツール群
- **`maxforlive/`**: Ableton Live 接続用 Max for Live デバイス
- **`prototypes/`**: 開発初期の実験的プロトタイプ（旧 8421 ポート簡易UIなど）

---

## 安全設計（14の安全規則）

- 送信前の破壊的操作（トラック削除・クリップ上書き）は計画段階で拒否
- 承認トークン（Approval Gate）がない Live 書き込みは完全に遮断
- 送信後の自動読み戻し（Readback）による整合性検証
- 詳細は `docs/ARCHITECTURE.md` および `docs/KIHACHI_MUSIC_AI_MASTER_SPEC_20261022.md` を参照してください。
