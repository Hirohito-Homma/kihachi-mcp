# 参考曲分析・音楽知識機能（Groove Analysis Card）設計仕様書

## 1. 概要
本機能は、登録した参考曲のドラムとベースの関係性（0秒からファイル終端までの全尺、または8〜16小節程度の指定区間）を客観的・多角的に解析し、**「分析カード（AnalysisCard）」** として保存・可視化するとともに、音楽理論に基づく抽象的な制作ルール（`generation_rules`）へと変換して既存の作曲エンジン（`DeterministicMIDIComposer`）に接続するものです。

### 基本原則
1. **音符列の直接複製の禁止**:
   参考曲のMIDIノート番号やフレーズを直接コピーせず、発音密度、休符比率、キックとベースの帯域・時間的分離（シンコペーション）などの特徴量を抽象ルールとして抽出します。
2. **確信度と検証ステータスの明示**:
   全測定値に対象区間、単位、検出手段、確信度（0.0〜1.0）および検証ステータス（`verified` / `unverified` / `unknown`）を付与します。FFTベースで誤判定の起きやすいキーや和声、セクション推定は安易に断定せず、低確信度または `unverified` として安全に扱います。
3. **客観的解析・音楽理論解釈・制作ルールの分離**:
   - **検出結果（Mechanical Analysis）**: 周波数帯域分解と自己相関による機械測定値。
   - **理論的な解釈（Theory Interpretation）**: 検出値がもたらすグルーヴ構造や空間・休符の音楽的機能の説明（対象時間・測定根拠・解釈・確信度を分離記録）。
   - **生成への提案（Generation Rules）**: 新規MIDI候補生成へ渡す制御パラメータ。
4. **メモリ上限ストリーミングによる全尺解析**:
   全曲を一括でRAMにロードせず、10〜15秒程度のストリーミングブロック（ウィンドウ）で順次解析し、メモリ消費量を厳格に抑えます。曲の終端まで到達したこと（`reached_end`）と実測解析時間（`elapsed_seconds`）を結果に明記します。
5. **和声候補の過信を防ぐ**:
   クロマグラムから約3秒ごとの長三和音・短三和音・減三和音候補を表示しますが、候補はすべて未検証です。転回形、非和声音、ベース音、機能和声を照合する完全なコード進行認識ではありません。
6. **Ableton Live保護と安全境界**:
   本機能は候補の生成・保存・UIプレビューまでにとどまり、Liveへの自動適用や承認フローのバイパスは一切行いません。

---

## 2. アーキテクチャとデータフロー

```text
[参考オーディオ / 登録音源 (既定: 0秒〜ファイル終端全尺、または指定区間)]
        │
        ▼ (Streaming Bandpass STFT / Autocorrelation / Chroma / RMS)
[reference_groove_analysis.py]
  ├── 全曲ストリーミング処理 (Bounded Memory, EOF判定, 実測解析時間)
  ├── 機械分析 (BPM, Kick 4つ打ち, Bass密度, 休符比率, シンコペーション, RMS動態)
  ├── 音響相関による和声候補 (全体キーと約3秒ごとの三和音テンプレート候補。確定コード進行ではない)
  ├── 楽曲構成候補 (エネルギー・帯域・キックの簡易ヒューリスティック。Intro/Drop等は未検証候補)
  ├── 音楽理論解釈タイムライン (対象時間, 測定根拠, 解釈, 確信度)
  └── 抽象生成ルール (kick_onbeat_emphasis, bass_density, syncopation, recommended_key)
        │
        ▼
[reference_analysis_card.py (AnalysisCard v2.0.0)]
  └── AnalysisCardStore (<storage_dir>/analysis_cards/*.json にアトミック保存)
        │
        ├──▶ UI表示 (references.html)
        │      ├── 全曲概要・終端到達確認・実測解析時間
        │      ├── 検出結果（確信度バッジ表示）
        │      ├── 楽曲構成・セクション展開（Intro → Drop → Break ...）
        │      ├── 時間軸タイムライン詳細（区間ごとのリズム・密度・エネルギー）
        │      ├── 音楽理論解釈（対象時間・根拠・理論解釈・確信度の分離表示）
        │      ├── 未確定・要確認項目（キー候補や低確信度セクションの明示）
        │      └── 制作ルール
        │
        └──▶ [groove_rule_composer.py] (DeterministicMIDIComposer 連携)
               ├── Variant A: 密度適合型（参考曲の測定密度・シンコペーションを忠実再現）
               ├── Variant B: 休符強調型（ベース音数を間引き、Dub空間と休符比率を約25%向上）
               └── Variant C: 帯域分離型（キックのオンビートを徹底回避し裏拍特化のポリリズム）
                       │
                       ▼
               [ユーザー試聴評価・手動修正 (User Feedback)]
                 - 人間による評価（星評価 1〜5、試聴メモ、採用・却下理由）
                 - 手動修正値（BPM, Key）
                 - AnalysisCard に更新保存され、次回提案に活用
```

---

## 3. データ形式（AnalysisCard JSON スキーマ v2.0.0）

```json
{
  "card_id": "c61b626d70bc4f71a067a9da7c04192b",
  "schema_version": "2.0.0",
  "analysis_scope": "full_track",
  "user_title": "Minimal Dub Kick-Bass Relation",
  "audio_filename": "dub_track_01.wav",
  "execution_stats": {
    "reached_end": true,
    "elapsed_seconds": 1.482,
    "analyzed_duration_seconds": 214.5,
    "total_duration_seconds": 214.5,
    "chunk_count": 18,
    "scope": "full_track"
  },
  "segment": {
    "start_seconds": 0.0,
    "duration_seconds": 214.5,
    "sample_rate": 22050
  },
  "bpm": {
    "value": 124.0,
    "confidence": 0.85,
    "unit": "BPM",
    "method": "streaming_autocorrelation_consensus",
    "status": "verified"
  },
  "kick_pattern": {
    "hit_count": 428,
    "hits_per_bar": 4.0,
    "is_four_on_the_floor": true,
    "regularity": 0.94,
    "interval_summary": "全曲を通じた4つ打ちキック基調",
    "confidence": 0.85,
    "method": "streaming_full_track_peak_tracking"
  },
  "bass_groove": {
    "hit_count": 512,
    "density_notes_per_bar": 4.8,
    "rest_ratio": 0.58,
    "syncopation_ratio": 0.68,
    "confidence": 0.80,
    "method": "streaming_mid_low_band_transients"
  },
  "energy_dynamics": {
    "trend": "building",
    "dynamic_range_db": 18.5,
    "confidence": 0.85,
    "method": "full_track_rms_profile"
  },
  "harmonic_analysis": {
    "key": "D# minor",
    "scale": "minor",
    "confidence": 0.72,
    "status": "unverified",
    "detection_method": "12-tone Pitch Class Profile Correlation (Krumhansl-Schmuckler)",
    "reason": "クロマ相関度 0.72（2位: F# major との差 0.11）による機械推定",
    "top_candidates": [
      {"key": "D# minor", "correlation": 0.72},
      {"key": "F# major", "correlation": 0.61}
    ]
  },
  "sections": [
    {
      "section_name": "Intro",
      "start_sec": 0.0,
      "end_sec": 24.0,
      "time_range": "0:00–0:24",
      "confidence": 0.75,
      "status": "unverified",
      "evidence": "曲頭の低エネルギー・キック導入前の展開"
    },
    {
      "section_name": "Verse / Groove Core",
      "start_sec": 24.0,
      "end_sec": 72.0,
      "time_range": "0:24–1:12",
      "confidence": 0.82,
      "status": "unverified",
      "evidence": "安定したリズム骨格（約 4.0 hits/bar）と中程度のダイナミクス"
    },
    {
      "section_name": "Drop",
      "start_sec": 72.0,
      "end_sec": 132.0,
      "time_range": "1:12–2:12",
      "confidence": 0.85,
      "status": "unverified",
      "evidence": "最大RMSエネルギー（0.285）および4つ打ちキック・重低音の全開展開"
    }
  ],
  "theory_timeline": [
    {
      "time_range": "0:24–1:12",
      "start_sec": 24.0,
      "end_sec": 72.0,
      "target_section": "Verse / Groove Core",
      "measurement_evidence": "キック規則性 0.94、ベース発音の 68% がオフビート、休符比率 58%",
      "interpretation": "キックの拍周期が安定し、ベースの発音がオフビートに偏るため、拍の安定性とシンコペーションの対比がグルーヴを作っています。十分な休符が低域の飽和を防ぎ、空間性を維持しています。",
      "confidence": 0.82,
      "status": "unverified"
    }
  ],
  "generation_rules": {
    "bpm_target": 124.0,
    "kick_style": "four_on_the_floor",
    "bass_density_profile": "normal",
    "bass_density_value": 4.8,
    "bass_syncopation": 0.68,
    "bass_rest_ratio": 0.58,
    "kick_bass_interlock": "avoid_collision",
    "dynamic_profile": "building",
    "preserve_space": true,
    "recommended_key": "D# minor"
  },
  "user_feedback": {
    "human_rating": "4.5",
    "evaluated_by_human": true,
    "adopted": true,
    "rejection_reason": "",
    "notes": "キックとベースの分離が心地よい。B案の休符がジャンル感に最適。",
    "manual_overrides": {
      "tempo": 124.0,
      "key": "D# minor"
    }
  },
  "created_at": "2026-10-22T09:40:00+09:00",
  "updated_at": "2026-10-22T09:45:00+09:00"
}
```

---

## 4. API エンドポイント

| メソッド | パス | 説明 |
|---|---|---|
| `GET` | `/api/references/analysis-cards` | 保存済み分析カードの一覧取得（スコープ・解析時間・終端到達メタデータ含む） |
| `GET` | `/api/references/analysis-cards/{card_id}` | 特定の分析カードの詳細取得（v1/v2互換） |
| `GET` | `/api/references/gemini/status` | Gemini API 連携ステータス（キーは含めず状態のみ返却） |
| `POST` | `/api/references/analysis-card/create` | 音源の全尺（既定）または指定区間の解析・カード保存 |
| `POST` | `/api/references/analysis-card/enhance-gemini` | カードの理論解釈・制作ルールを Gemini で深化（特徴量のみ送信） |
| `POST` | `/api/references/analysis-card/generate-variants` | カードのルールから3つの対照MIDI候補を生成 |
| `POST` | `/api/references/analysis-card/evaluate` | ユーザー試聴評価・手動修正値の更新保存 |

---

## 5. Google Gemini API 連携仕様

### 5.1 接続方式とセキュリティ
- **認証**: バックエンド側で環境変数 `GEMINI_API_KEY`（または `GOOGLE_API_KEY`）から取得。
- **キー保護**: APIキーはリクエストヘッダー `x-goog-api-key: {key}` で送信。フロントエンド、ログ、URLクエリ、Git管理ファイルには一切露出させません。
- **音声ファイルの送信禁止**: 音声データそのものをGeminiへ送る導線は作成せず、客観的測定値（BPM、キック配置、ベース密度・休符比率、セクション情報）の特徴量テキストのみを解釈用プロンプトとして送信します。
- **モデル**: `gemini-2.5-flash`（既定）、`gemini-2.5-pro`。

### 5.2 厳格なスキーマ検証と安全フォールバック
- Google AI Studio 仕様の `responseSchema` を用いて JSON 形式で受け取り、受信後に `_validate_and_sanitize_gemini_output` でバリデーション。
- キーや和声、セクション推定などオーディオから直接断定できない値は必ず `harmonic_status: "unverified"`, `key: "unknown (unverified)"` として扱い、AIのハルシネーションによる推測を強制排除。
- APIキー未設定、通信障害、タイムアウト、レート制限（429）時は、Studio全体をクラッシュさせず `fallback_used: True` として既存のローカル解析値を安全に維持。



## 7. 和声候補の解釈上の制限

全曲クロマの調性候補に加え、約3秒窓ごとのクロマを長三和音・短三和音・減三和音テンプレートと照合します。UIには時間範囲、候補スコア、未検証状態を表示します。これはコード進行の確定ではありません。完成ミックスでは倍音、転回形、非和声音、複数楽器の同時発音で誤候補が生じるため、転回形・非和声音・ベース音・機能和声はすべて未確定として扱い、耳またはDAWで確認してください。理論解釈の文章は、測定された周期性・ピーク比・休符代理指標・RMS・三和音候補を根拠として明示し、音色や作曲意図を推定事実として扱いません。

---

## 8. 解析中UIステータスと進捗率

- **非同期ジョブ**: Studioは `POST /api/references/analysis-jobs` で解析を開始し、返されたジョブIDを `GET /api/references/analysis-jobs/{job_id}` で約600msごとに読みます。ジョブ状態はStudioプロセス内に保持され、同時実行は最大2件、完了・失敗ジョブの保持数は最大50件です。旧Studioサーバーで開始APIが404の場合は同期エンドポイントへ互換フォールバックします。
- **全曲解析**: 音声フレームの読込量に応じて進捗を更新します（音声解析は最大88%、理論解釈で92%、Gemini解釈で94%、カード保存で98%、完了時に100%）。
- **区間解析**: 音源読み込み、STFT、音響特徴抽出、理論解釈の各段階で進捗を更新します。区間処理は段階ベースの目安であり、所要時間に比例した精密な予測ではありません。M4Aは一時変換の開始・終了を状態に表示しますが、FFmpegの内部進捗率は取得しません。
- **再接続と制限**: ジョブIDに加えて解析開始要求と冪等キーをタブの `sessionStorage` に保持し、ページ再読み込み後も同じジョブへ再接続します。要求内容は完了まで保持し、ジョブが見つからない場合は条件を残して画面から再開できます。開始POSTの応答が失われても同じ要求IDで再送し、サーバーは保持中のジョブまたは保存済みカードがあれば新規解析を作らず返します。カードには冪等キーと解析条件の指紋を保存し、要求ID由来のカードIDで直接照会します。再開操作では旧形式の最新カード最大50件も照合します。それより古い旧形式カードは復元されず再解析になる場合があります。自動生成カードIDが別カードと衝突した場合は既存カードを上書きせず要求を拒否します。サーバーは冪等キーと解析条件の指紋を照合し、同じキーを異なる条件で再利用した要求は拒否します。通信エラーは最大5回再試行し、再試行後も接続できない場合はIDを保持します。同じタブで「参考音源を分析してカード化」を押すと保存済みIDへ再接続します。ブラウザーを閉じてもStudioサーバー上の解析は続きますが、新しいタブへはIDを引き継ぎません。サーバー停止時は実行中ジョブも終了します。復元完了ジョブを含むジョブ情報は最大50件のメモリ保持なので、カード保存前に停止した処理や古い要求の冪等性は保証しません。進捗値は完了時間の保証ではありません。完了時には実測時間 `elapsed_seconds`、解析時間、終端到達などを分析カードに保存します。
