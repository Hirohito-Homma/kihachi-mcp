# KIHACHI MUSIC AI
## 総合マスター情報
### 2026年10月22日 現在

---

# 1. KIHACHI MUSIC AIとは

**KIHACHI MUSIC AI** は、人間とAIが共同で楽曲を制作するための音楽制作システムである。

コンセプトは、

**Human × AI Make Better Music**

一般的なAI音楽生成サービスのように、

「指示を出す → 完成した音源が1本生成される」

ことを最終目的にはしていない。

KIHACHI MUSIC AIが目指しているのは、

**AIそのものをDAWの制作工程へ参加させること**

である。

AIが担当するのは、

- 楽曲設計
- SongSpec作成
- アレンジ設計
- MIDI生成
- ドラムパターン生成
- ベース生成
- コード生成
- メロディ生成
- Vocoder用素材
- 歌詞
- 楽曲解析
- 問題点検出
- 修正案作成
- Ableton Live反映計画
- DRY RUN
- Live反映
- 読み戻し
- Verification

など。

最終的に、

**人間が聴き、判断し、採用する。**

ここがKIHACHI MUSIC AIの中心思想である。

---

# 2. 製品としての目的

KIHACHI MUSIC AIは単なる「自動作曲AI」ではない。

目標は、

**AIと一緒に音楽を作りながら、人間自身も作曲・編曲・DAW操作を学べる制作環境**

を作ること。

特に重要なターゲットは、

### 作曲初心者

である。

初心者が、

「何をしたら曲になるのか分からない」

状態から、

AIの提案を見ながら、

Brief
↓
SongSpec
↓
Drums
↓
Bass
↓
Chord
↓
Arrangement
↓
Mix
↓
完成

という流れを体験し、

徐々に自分で判断できるようになることを目指す。

一方で、

### 上級者

には、

反復作業・MIDI作成・アレンジ候補作成・分析・DAW操作などをAIに任せ、人間が音楽的判断へ集中できる環境を提供する。

したがってKIHACHI MUSIC AIは、

**教育ツール**
+
**制作支援AI**
+
**Ableton Liveオートメーション**
+
**音楽制作エージェント**

を統合したシステムとして設計されている。

---

# 3. 一般的なAI音楽生成との違い

一般的なAI音楽生成：

Prompt
↓
AI
↓
WAV / MP3

KIHACHI MUSIC AI：

日本語の制作指示
↓
Music Brain
↓
SongSpec
↓
Arrangement Plan
↓
MIDI Composer
↓
Validation
↓
Human Review
↓
修正
↓
DRY RUN
↓
Human Approval
↓
Ableton Live
↓
Readback
↓
Verification
↓
人間が仕上げる

重要なのは、

**完成音源ではなく「編集可能な制作工程」を生成すること。**

生成されたBass、Drums、Chord、ARPなどはAbleton Live上で人間が自由に編集できる。

---

# 4. KIHACHI MUSIC AIの基本思想

製品の中心原則は以下。

**AIが決めるのではなく、AIが提案する。**

AI：
設計・生成・分析・修正候補作成・操作計画

人間：
試聴・評価・採用・却下・修正指示・最終判断

Ableton Live：
実際の音楽制作環境

この関係を維持する。

---

# 5. 現在の全体アーキテクチャ

現在の基本構成は、

**Studio**
↓
**Jarvis / Orchestrator**
↓
**KIHACHI MUSIC AI modules**
↓
**kihachi-mcp**
↓
**AbletonGPT**
↓
**Ableton Live**

である。

概念的には、

```text
USER
  ↓
KIHACHI MUSIC AI Studio
  ↓
Jarvis
  ↓
Music Brain
  ↓
SongSpec
  ↓
Composer / Analyzer / Critic
  ↓
Production Plan
  ↓
kihachi-mcp
  ↓
MCP Gateway
  ↓
AbletonGPT
  ↓
Ableton Live
```

となる。

---

# 6. Studio

StudioはKIHACHI MUSIC AIの統合UI。

単なる画面ではなく、

**Production Control Centre**

として設計されている。

既存の主なファイル：

- index.html
- app.js
- server.py
- coordinator.py

など。

Studioから将来的に、

制作指示
↓
SongSpec
↓
候補生成
↓
レビュー
↓
修正
↓
承認
↓
Ableton送信
↓
Verification

まで操作する。

現在はStudio自体は存在しているが、

**すべてを一貫してワンクリック実行できる状態にはまだ達していない。**

---

# 7. Jarvis

Studio内部のOrchestratorとしてJarvisを統合する設計。

Jarvisの役割は、

```text
Intent
↓
Context
↓
Plan
↓
Approval
↓
Execution
↓
Verification
```

の管理。

重要なのは、

**Jarvis自身が勝手にAbleton Liveを書き換えるのではない**

という点。

Jarvisは制作工程の指揮役。

Music Brainが音楽判断。

kihachi-mcpが安全性と実行計画。

AbletonGPTがLive操作。

という責任分離を行う。

---

# 8. Music Brain

KIHACHI MUSIC AIの音楽判断部分。

ユーザーの自然言語から、

- Genre
- BPM
- Key
- Groove
- Swing
- Instruments
- Bass Style
- Drum Style
- Arrangement
- Vocals
- Sound Design
- Section構成

などを解釈する。

その情報をSongSpecへ変換する。

---

# 9. SongSpec

SongSpecはKIHACHI MUSIC AIの楽曲設計図。

楽曲制作の「正本」となる構想。

例：

```text
Genre:
Mutation Funk × DUB × Tech House

Tempo:
110 BPM

Key:
D# minor

Swing:
54%

Bass:
Funky Slap Bass

Vocal:
Vocoder

Chord:
Dub Chord Stabs

Drums:
Tight Snare
```

さらに、

- sections
- instrument roles
- patterns
- seed
- revision history
- arrangement
- variation
- density

などを保存していく設計。

---

# 10. 現在の代表的固定Music Brief

現在のKIHACHI MUSIC AI開発用基準曲は、

**Mutation Funk × DUB × Tech House**

Tempo：

**110 BPM**

Key：

**D# minor**

Swing：

**54%**

主要要素：

- Funky Slap Bass
- syncopation
- ghost note
- octave
- Vocoder
- Dub Chord Stabs
- Tight Snare
- Tech House groove
- Dub space
- Mutation
- Funk

ターゲットイメージ：

**Bootsy Collins的なspace bass**

ターゲット層：

主に米国20代を想定。

---

# 11. MIDI Composer

MIDI Composerは、

SongSpecから編集可能なMIDI素材を生成する部分。

対象：

- Drums
- Percussion
- Slap Bass
- Sub Bass
- Chords
- Synth Stabs
- ARP
- Melody
- Vocoder MIDI

将来的には、

「全曲を一発生成」

ではなく、

例えば、

**Bassだけ修正**

**Snareだけ変更**

**Dropだけ再生成**

のような差分編集を行えるシステムを目指す。

---

# 12. Analyzer

Analyzerは楽曲を解析するモジュール。

予定している分析対象：

- Groove
- Density
- Note Distribution
- Register
- Timing
- Section Balance
- Repetition
- Arrangement
- Mix
- Loudness
- Peak

など。

現時点では、

**全制作フローへ完全統合された状態ではない。**

---

# 13. Critic

Criticは、

「この曲の何が弱いか」

をAIが評価する部分。

例：

- Bassが単調
- Dropの変化が弱い
- 高域Movement不足
- Section差が小さい
- KickとBassの役割が衝突

など。

現在は構想・部分実装段階で、

**Studio → Critic → Revision → Live**

の完全ループは未完成。

---

# 14. Memory

制作判断を学習・保持するモジュール。

例えば、

「KIHACHIはこういうBassを好む」

「この曲ではARPありが良かった」

「Synth Stabsは-6 dBが承認された」

のような制作履歴を次の判断へ利用する構想。

プロジェクト全体のMemory統合は、

**まだ完成していない。**

---

# 15. kihachi-mcp

kihachi-mcpは、

**Production Gateway**

として設計されている。

場所：

`kihachi-mcp` リポジトリ

を中心として開発。

役割は、

AIが直接Ableton Liveを操作することを防ぎ、

操作内容を、

Plan
↓
Validate
↓
DRY RUN
↓
Approval
↓
Execution
↓
Readback
↓
Verify

へ変換すること。

KIHACHI MUSIC AIの安全性の中核である。

---

# 16. AbletonGPT

AbletonGPTは、

**Ableton Live実行エンジン**

として使用。

Custom Control Surfaceと通信し、

Liveを操作する。

実装・検証してきた主な機能：

- Live接続
- Tempo取得
- Tempo変更
- MIDI Clip作成
- MIDI Note作成
- Arrangement MIDI作成
- Arrangement MIDI削除
- Arrangement MIDI置換
- Clip移動
- Clip削除
- Simpler操作
- Simpler sample replacement
- Drum Rack pad assignment
- Audio One-shot import
- Track状態取得
- Playback状態取得
- Arrangement配置

など。

---

# 17. Ableton Live

現在の最終制作環境。

過去にはLive 12.4.2 / 12.4.3で制作。

その後の実機確認では、

**Ableton Live 12.4.5**

が確認されている。

したがって現在把握している最新Live環境は、

**Live 12.4.5系**

となる。

---

# 18. MCP Gateway

外部AIとkihachi-mcpを接続するGatewayを実装。

Endpoint：

`127.0.0.1:8787/mcp`

Health：

HTTP 200確認。

MCP endpointはBearer認証。

Manusからも接続テストを実施。

確認済み：

```text
server_status = ok
ableton_ping = connected true
```

Tempo：

120 BPM
↓
110 BPM

への変更も実証した。

---

# 19. 通信構成

開発履歴で確認されている通信経路には、

- UDP 17771
- UDP 17772
- HTTP 8765
- TCP 9877
- Gateway 8787

などが存在する。

Remote Script：

AbletonGPT

Remote Script関連パス：

`~/Music/Ableton/User Library/Remote Scripts/AbletonGPT`

または

`AbletonGPT_MCP`

など。

---

# 20. Human Approval安全設計

これはKIHACHI MUSIC AIでもっとも重要なルールの一つ。

基本フロー：

```text
PLAN
↓
DRY RUN
↓
VALIDATE
↓
USER APPROVAL
↓
WRITE
↓
READBACK
↓
VERIFY
```

ユーザー承認なしで、

- Save
- Export
- destructive edit
- clip deletion
- track deletion

を実行しない。

Live書き込みも、

**DRY RUN → 人間承認 → 1回だけ実行**

が原則。

失敗しても、

**自動リトライで勝手に追加書き込みしない。**

---

# 21. disarm_live_write.py

Antigravity / Studio開発時には、

Liveへの意図しない書き込みを防ぐため、

`disarm_live_write.py`

によるロック運用も採用。

代表状態：

```text
DRY_RUN
approved=false
write_enabled=false
LIVE CHANGED=NO
```

Studio開発中に誤ってLiveを変更しないための安全機構。

---

# 22. Ableton Liveで既に実証されたこと

KIHACHI MUSIC AIはすでに、

**「AIからAbleton Liveを実際に操作できる」**

段階まで到達している。

確認済みの代表例：

- Live接続
- Live状態取得
- Tempo変更
- MIDI生成
- Arrangement MIDI配置
- Audio one-shot配置
- Simplerへのサンプルロード
- Drum Rackへのサンプル配置
- Clip move
- Clip delete
- Note readback
- Live readback
- Verification

つまり、

**単なる設計構想ではなく、実際のAbleton Live Arrangement上で音を鳴らしている。**

---

# 23. Mutant Dub

現在もっとも重要な実証曲。

Ableton Set：

**Mutant Dub.als**

この曲が、

KIHACHI MUSIC AIのProduction Validation用リファレンスになっている。

---

# 24. Mutant Dub 基本設定

Tempo：

**110 BPM**

Time Signature：

**4/4**

Key：

**D# minor**

スタイル：

**Mutation Funk × DUB × Tech House**

---

# 25. Core Groove 7レイヤー

人間が試聴・評価したCore Groove。

対象：

beat 160–224
16 bars
bar 41–56

7レイヤー：

| Layer | Event / Note |
|---|---:|
| KICK AUDIO | 64 |
| DRUMS | 109 |
| PERCUSSION | 46 |
| SLAP BASS | 55 |
| SUB BASS | 19 |
| DUB CHORDS | 42 |
| SYNTH STABS | 24 |

合計：

**359 events**

内訳：

Rhythm：
219

Bass：
74

Chord / Synth：
66

---

# 26. SYNTH STABS

Core Grooveで追加・承認済み。

Production Track：

**Mutant Dub Circuit - SYNTH STABS**

音源：

**Perc Blip Bleep / Operator**

Arrangement：

160–224

MIDI：

24 notes

人間試聴結果：

- Mutation：良好
- Funk：良好
- Dub Space：良好
- Phrase：良好

音量調整：

-10 dB
↓
-8 dB
↓
**-6 dB**

最終的に、

**-6.0 dBで「ちょうどいい」**

と人間が承認。

状態：

**HUMAN AUDIBLE VERIFIED**

**MUSICALLY APPROVED**

---

# 27. ARP

7レイヤーを試聴した結果、

160–224のGrooveについて、

**「少しスカスカ」**

という評価が出た。

そこで、

空間を残しながら高域Movementを追加するため、

ARP追加を決定。

ARP：

**Mutant Dub Circuit - ARP**

Key：

D# minor

Tempo：

110 BPM

Swing：

54%

Range：

D#4–C#6

Velocity：

55–85

初期レベル：

約 -12 dB

Core v1：

**52 notes**

Instrument：

**FM Basic Pluck**

人間評価では、

**「ARPありの方が明確に良い」**

という結論になった。

したがって現在は、

7レイヤーCore Groove
+
ARP

という形まで発展している。

---

# 28. Core GrooveにおけるKICK

KICKでは重要な実証を行った。

MIDI：

112 notes

Simpler：

OriginalSimpler

Sample：

`T_TSOTT2_kick_oneshot_funky.wav`

MIDI
↓
Simpler
↓
Sample Path

まで一致確認。

Arrangementで実際にKICKが鳴るところまで検証した。

---

# 29. Splice Sample Library

Spliceライブラリ：

`/Volumes/NO NAME/splice`

約：

**1,024 files**

容量：

約 **2.47 GB**

Kick / Snare / Clap / Hat候補の検索にも利用。

Drum RackのPad配置にも一部使用している。

---

# 30. 5分アレンジメント

Core Grooveから、

約5分のArrangementへ展開した。

メイン範囲：

beat 256–576

約：

**4分56秒**

構成：

### Intro
256–288

KICK
DRUMS
PERCUSSION

### First Full Groove
288–352

+ SLAP
+ SUB

### Breakdown
352–384

KICK
SLAP
SUB

### Build
384–416

+ PERC
+ STABS

### Drop
416–448

+ DRUMS

### ARP Section
448–512

+ ARP

### Pre-break
480–496

DRUMSの変化

### SUB Break
496–512

SUBを休ませる

### Re-entry
512–544

KICK
DRUMS
SLAP
SUB
STABS

### Mutation Peak
544–576

最終ピーク。

---

# 31. Re-entry修正

当初：

516–548

にあったRe-entryを、

512–544

へ移設。

安全手順として、

旧516–548を削除
↓
512–544へ再作成

を行った。

移設対象：

DRUMS 58
SLAP 28
SUB 10
STABS 12

衝突なしを確認。

---

# 32. Mutation Peak

範囲：

544–576

最終承認構成：

KICK：
8 BAR

DRUMS：
58

SLAP：
29

SUB：
10

STABS：
14

PERC：
14
560–576

ARP：
6
568–576

ARPについて、

「少し大きい」
↓
調整
↓
「ちょうどいい」

まで人間が試聴確認。

---

# 33. 現在確認されているArrangement上の空白

256–576のメインアレンジで、

DUB CHORDS：
未配置

DRUMS：
352–416空白

PERC：
512–560空白

ARP：
512–568空白

SUB：
496–512空白

これらは必ずしもバグではなく、

Arrangement上の意図的な密度設計を含む。

ただし、

**DUB CHORDSが5分本編へまだ展開されていない**

点は今後の判断対象。

---

# 34. Synthesizer V / SVP

SynthV連携も検証済み。

Studio Step 4で、

**SynthV: LOADED VERIFIED**

まで到達。

SVP readback：

Tracks：
2

Lead：
12 notes

Vocoder：
3 notes

合計：

**15 notes**

確認項目：

- tempo
- meter
- track name
- lyrics
- onset
- duration
- pitch

すべて本番SVPと一致。

一度、

`svp_path`
`svp_export`
export history

が消失する状態が発生。

ただしapproval hashは残っていた。

既存SVPを再登録し、

**ファイルを書き換えずread-back verification**

を実施して復旧した。

この事件は、

**State Recovery設計の必要性**

を明確にした。

---

# 35. Studio / Antigravity統合

Antigravityを利用して、

Studio内部の実装・監査を進めてきた。

実装・確認してきたもの：

- Step 4A
- Step 4B
- Step 5A
- Step 5B
- SynthV verification
- carry_over_candidate_b_selection
- coordinator更新
- composer更新
- Live read-only inspection
- State recovery

一部では、

**UIボタンを押しても見た目上変化しない**

という問題も確認されている。

現在の最優先領域は、

**UI → Backend**

**Backend → UI**

**State Recovery**

の安定化。

---

# 36. 現在の開発フェーズ

現在の焦点は、

## Phase 2
### Studio / Jarvis統合

である。

過去のロードマップ：

### v0.1
Natural Language
→ SongSpec
→ Prompt / Lyrics
→ Audio reference

### v0.2
MIDI

### v0.3
Arrangement

### v0.4
Analyzer / Critic

### v0.5
Memory

### v1.0
Ableton自動配置を含む統合Production Workflow

現在は、

単純なフェーズ番号だけならArrangement/Ableton実証までかなり進んでいる。

しかし製品としては、

**Studioから一貫して操作できないためv1.0ではない。**

---

# 37. v1.0の本当の完成条件

単にLiveを操作できることではない。

Studio上で、

```text
楽曲を読み込む
↓
AIが制作提案
↓
MIDI / Arrangement生成
↓
DRY RUN
↓
ユーザーが内容確認
↓
承認
↓
Ableton Liveへ反映
↓
Liveから読み戻す
↓
予定した内容と一致しているか検証
↓
VERIFIED
```

までを、

**一つの安定したProduction Workflow**

として通せること。

これが現在の最重要完成条件。

---

# 38. Ollama

ローカルAI基盤としてOllamaを採用する方針。

クラウドAIだけに依存せず、

- Local
- Private
- Fast
- Replaceable

なAI構成を目指す。

Studio / Music Brain / Ollamaの完全統合については、

**再確認・未完成部分が残っている。**

---

# 39. Astra連携構想

AstraをComposer / Arrangerとして利用する設計も検討。

役割案：

Music Brain：
Director

Astra：
Composer / Arranger

kihachi-mcp：
Protocol / Safety

AbletonGPT：
Live Executor

想定フロー：

SongSpec
↓
MusicPlan
↓
Validator
↓
LivePlan
↓
Human Approval
↓
Commit
↓
Readback
↓
Verify

重要原則：

**AstraからAbleton Liveを直接操作させない。**

まだ製品の確定Production Pathとして完成したわけではなく、

**統合設計・検討領域。**

---

# 40. ACE-Step

KIHACHI MUSIC AIの初期段階では、

ACE-StepをAudio Generatorとして利用。

ACE-Step 1.5 APIで音源生成を実証。

さらに、

**KIHACHI_LORA_v1**

を作成。

学習素材：

**15曲**

LoRA成果物保存済み。

しかしIntel Mac環境では、

x86系wheel / torchcodecなどの問題があり、

ローカルACE-Step 1.5運用が難しくなった。

その後Lyria APIへの移行案も検討したが、

現在の製品コアはさらに方向転換している。

つまり、

**完成WAV生成を中心にするのではなく、編集可能MIDI＋Ableton Automationを中心にする。**

そのためAudio Generatorは今後、

**参考音源・アイデア生成**

としての位置付けが適切。

---

# 41. Music Genre Master Database

KIHACHI MUSIC AI用に、

**Music Genre Master Database v0.1**

を作成済み。

ジャンル数：

約 **1,020ジャンル / サブジャンル**

構成：

- Genre_Master
- README
- Schema
- Controlled_Vocab
- Genre_Index

情報：

- BPM
- Rhythm
- Instruments
- Sound Design
- Mood
- Related Genre
- Fusion suitability
- Prompt Keywords

など。

将来的には、

Genre DB
↓
SongSpec
↓
Composer

というMusic Brainの知識ベースとして使用する。

今後の拡張候補：

- Drum Pattern DB
- Bass Pattern DB
- Chord Grammar
- Arrangement Template
- Genre Mixing Ratio

---

# 42. Mac環境

現在の主要開発機：

**MacBook Pro 16,1**

CPU：

Intel Core i9
2.4 GHz
8 Core

RAM：

**32 GB**

GPU：

Intel UHD 630
+
Radeon Pro 5300M 4GB

macOS：

26.6系の環境情報あり。

このIntel Macで、

Ableton Live
KIHACHI MUSIC AI
kihachi-mcp
Studio

を開発してきた。

---

# 43. Apple Siliconへの移行

現在の重要な経営・開発課題。

Manus、ローカルAI、新しい開発環境などを組み合わせる上で、

Intel Macがボトルネックになってきている。

そのため、

会社へMacBook Pro貸与申請を行うための資料を作成。

希望：

**Apple M1 または M2搭載 MacBook Pro**

RAM：

**32 GB**

16GBではなく、

**32GBを希望。**

目的：

- KIHACHI MUSIC AI
- Ableton Live
- Studio
- Manus
- MCP
- Local AI

を同一環境で安定運用するため。

現在のプロジェクト判断では、

**Apple Silicon環境への移行が次の開発基盤として非常に重要。**

---

# 44. Manus

ManusとMCP Gatewayの接続実験も実施。

確認済み：

```text
server_status ok
ableton_ping connected true
```

Tempo変更：

120 → 110 BPM

まで実証。

ただしLiveへの大量書き込みなどは行わず、

安全な最小テストに限定。

`produce_ableton_song`

についても、

dry-runを中心に検証。

現在はApple Silicon環境への移行と合わせて、

ManusをKIHACHI MUSIC AI開発へ本格活用する構想。

---

# 45. 最新リポジトリ上の注意点

2026年10月22日時点の開発通知では、

**PR #12 のCIが全ジョブ成功状態にはなっていない。**

レビューでは、

- drum role preservation
- sample identity verification
- request body size
- Origin forwarding

など複数の修正ポイントが指摘されている。

したがって、

現在のコードベースを、

**「すべて完成・CI Green・Production Ready」**

とは評価できない。

これは現在の重要な技術課題。

---

# 46. Mix / Master

まだKIHACHI MUSIC AIの完全自動化領域には入っていない。

特にAbletonGPT側では、

Master Peak
Limiter
Compressor
EQ
Clipping

などを完全取得できないケースがある。

過去のExport Pre-flightでも、

MASTER関連項目がUNKNOWNになっている。

したがって、

現在はMix / MasterをAIが完全自動で決定する段階ではない。

---

# 47. Export

AbletonGPTでは、

- Export range
- Format
- Sample Rate
- Bit Depth
- Normalize
- Master Render

などの完全制御は未対応。

そのため、

**最終ExportはAbleton Live UIからの手動操作**

が基本。

またExport自体、

ユーザーの明示承認が必要。

---

# 48. ZOOM L-6max

実機制作環境として、

**ZOOM L-6max**

を購入済み。

Ableton Liveとの接続も検討。

外部ハードウェアやシンセをKIHACHI MUSIC AI制作環境へ組み込むためのAudio / Mixer Hubとして活用可能。

---

# 49. その他のミキサー検討

制作環境強化として、

- YAMAHA 03D
- YAMAHA 01V96
- BEHRINGER XENYX X2442USB
- PreSonus StudioLive 16.0.2

なども導入候補として比較検討している。

これらは、

**KIHACHI MUSIC AI本体の必須構成ではなく、外部制作環境候補。**

---

# 50. KORG MS-20 mini

外部シンセとして、

**KORG MS-20 mini**

導入も検討。

ヤフオクで入札を行っていた。

確認時価格：

**12,551円**

将来的には、

MS-20 mini
↓
ZOOM L-6max
↓
Ableton Live
↓
KIHACHI MUSIC AI

というハードウェアを含む制作環境も想定。

---

# 51. MV / 映像展開

KIHACHI MUSIC AIで作った楽曲を、

MVへ展開する制作も進めている。

代表曲：

- Mutant Dub
- Mutant Boy
- DISCO KID2
- kiss me

など。

---

# 52. Mutant Dub MVコンセプト

世界観：

- 未来都市
- ネオン
- 雨
- サイケデリック
- 液体クローム
- ダブ的残像
- 小型スラップベースロボット

構成：

導入：
雨のネオン都市

Groove：
光のBass cableが都市を走る

Break：
建物が水面のように溶ける

Peak：
都市が万華鏡・Liquid Chrome化

Ending：
夜明け

表示：

**KIHACHI / MUTANT DUB**

---

# 53. 映像生成プラットフォーム

検討・制作対象：

- Runway
- Higgsfield
- Seedance
- Luma AI
- Veo 3.1
- Gemini

Seedanceでは、

**Mutant Boy.wav**

4分21.8秒のフルMV構想。

比率：

16:9
+
9:16

両方。

---

# 54. キャラクター

KIHACHI MUSIC AI / MV世界観のキャラクターとして、

**スラップベースを弾く小さなロボット**

という設定を作成。

Mutant DubのMVにも登場させる構想。

ブランドキャラクターとして発展させる余地がある。

---

# 55. note開発ログ

KIHACHI MUSIC AIの開発過程を、

noteで公開する活動も開始。

重要なストーリー：

**「AIに曲を作らせるところから始まったプロジェクトが、ついにAbleton Liveを実際に操作し、Arrangement上で音を鳴らすところまで来た」**

これを一般ユーザーにも分かる形で発信する。

---

# 56. Threads発信

Threads向けにも、

- KIHACHI MUSIC AI概要
- 現在状況
- できること一覧
- KIHACHI MUSIC AIの未来

などの投稿素材を作成。

今後は開発そのものだけでなく、

**開発過程をコンテンツ化する**

ことも重要。

---

# 57. 初心者向け取扱説明書

KIHACHI MUSIC AIを初めて使う作曲初心者向けに、

図解付きの詳細取扱説明書を作る方針。

これは教育目的と強く結びつく。

将来的には、

「曲を作ってもらうAI」

ではなく、

**「AIと曲を作りながら作曲を覚えるアプリ」**

として位置付ける。

---

# 58. A8.net関連

別事業として、

A8.netを利用したアフィリエイトプラットフォーム構想も進めている。

Antigravityに実装させるためのPromptも作成。

ただしこれは、

**KIHACHI MUSIC AIの音楽制作コアとは別の周辺事業。**

---

# 59. 現在実証済みの主要成果

現時点で特に重要なのは、

### 1
AI → Ableton Live接続

### 2
Tempo変更

### 3
Arrangement MIDI生成・配置

### 4
Audio One-shot配置

### 5
Simpler Sample assignment

### 6
Drum Rack Sample assignment

### 7
Clip移動・削除

### 8
Human Approvalを挟んだLive Write

### 9
Readback / Verification

### 10
Mutant Dub Core Groove実制作

### 11
5分Arrangement構築

### 12
SynthV SVP readback verification

まで実機で進んだこと。

---

# 60. 現在未完成の主要項目

最重要。

### Studio / Jarvis完全統合
未完成。

### Music Brain → SongSpec → Composer → Studio
完全E2Eは未完成。

### Analyzer
統合未完成。

### Critic
統合未完成。

### Memory
制作システムとして統合未完成。

### Ollama
完全統合再確認が必要。

### Mix / Master
自動化未完成。

### Export
手動。

### One-click Production
未完成。

### State Recovery
改善中。

### UI ↔ Backend同期
改善中。

### CI
最新PRに失敗あり。

### Apple Silicon移行
未完了。

---

# 61. 現在もっとも大きな技術的ボトルネック

以前の最大問題は、

**「AIからAbleton Liveを操作できない」**

ことだった。

これはかなり解決した。

現在の問題は、

**「個別に動く機能を、Studioから安全かつ一貫して一つの制作体験にまとめる」**

ことである。

つまり開発フェーズは、

### 接続技術の実験
から

### 製品統合

へ移っている。

---

# 62. 現在もっとも重要なマイルストーン

最優先は、

**Studio → Jarvis → Music Brain → Composer → DRY RUN → Approval → Ableton Live → Readback → VERIFIED**

を、

1本の安定したGolden Pathとして完成させること。

新しいAIモデルを大量に増やすことより、

まずこの一本を安定させる方が重要。

---

# 63. 現在のHuman Verified Music Decisions

人間が明確に判断済みの代表例：

SYNTH STABS：
採用。

Level：
**-6.0 dB**

ARP：
**ありの方が良い。**

Mutation Peak ARP：
音量調整後「ちょうどいい」。

Re-entry：
512–544配置。

Mutation Peak：
544–576構成を採用。

これらは単なるAI提案ではなく、

**Human Auditionを通過した制作判断。**

---

# 64. 週次進捗管理

Mutant Dub / KIHACHI MUSIC AIについて、

毎週まとめるべき情報も定義されている。

重点項目：

- 人間確認済み制作判断
- 未解決事項
- Core Groove 7 Layers
- SYNTH STABS状態
- ARP判断
- Blocker
- 次の最重要Milestone

この形式を継続的なDevelopment Logとして使う。

---

# 65. KIHACHI MUSIC AIの現在地を一言で表すと

初期：

**AIに曲を作らせるプロジェクト**

だった。

現在：

**AIがAbleton Liveの制作工程に参加するシステム**

まで進化した。

次：

**AIと人間がStudio上で会話しながら、実際のDAW制作を安全に共同実行できる製品**

へ移行する段階にある。

---

# 66. 最終的に目指している姿

ユーザー：

「110 BPM、D# minor。
ファンキーなスラップベースで、
ダブっぽくして。
Dropはもう少し変態的に。」

KIHACHI MUSIC AI：

「了解。
現在のArrangementを解析しました。

Bass Patternを2案、
Drum Mutationを1案、
Dub Stab配置変更を1案提案します。

Ableton Liveへ反映する前に
DRY RUNを表示します。」

ユーザー：

「B案で。」

KIHACHI MUSIC AI：

```text
PLAN
VALIDATED
APPROVED
WRITE
READBACK
VERIFIED
```

そしてAbleton Live上へ、

**編集可能なMIDIと制作工程そのものが反映される。**

これがKIHACHI MUSIC AIの完成形である。

---

# 67. 現時点でのプロジェクト評価

KIHACHI MUSIC AIは、

アイデア段階：
**終了**

基礎研究：
**かなり完了**

Ableton接続Proof of Concept：
**実証済み**

MIDI / Arrangement操作：
**実証済み**

Human Approval安全設計：
**実装・運用中**

実曲Production Validation：
**Mutant Dubで進行・大部分実証**

Studio：
**存在**

Studio / Jarvis統合：
**開発中**

Analyzer / Critic / Memory：
**未完成**

One-click Production：
**未完成**

Apple Silicon移行：
**次の重要基盤**

製品v1.0：
**まだ未完成**

---

# 68. 現在の最重要結論

KIHACHI MUSIC AIにとって今もっとも重要なのは、

**さらに機能を増やすことではない。**

すでに、

AI
MIDI
MCP
AbletonGPT
Ableton Live
Studio
SynthV
Approval
Verification

という主要部品は揃い始めている。

必要なのは、

これらを、

**一つの安定した制作体験へ統合すること。**

つまり現在の最重要テーマは、

# 「機能開発」から「製品統合」へ

である。

そして最終的な価値は、

# Human × AI Make Better Music

という言葉通り、

AIに人間を置き換えさせることではなく、

**人間がAIと一緒に、より良い音楽を作り、同時に作曲そのものを学べる制作環境を完成させること**

にある。
