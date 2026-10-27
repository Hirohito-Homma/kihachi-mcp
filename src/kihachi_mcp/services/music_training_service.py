"""Music Training Service for KIHACHI MUSIC AI.

Enables musicians and producers to learn composition, groove, swing, sound design,
and arrangement by comparing contrasting creative variations (A/B/C) derived from
reference cards or candidate material.

Strictly follows safety guidelines:
- Never clones raw notes from references.
- Presents musical choices with both novice and technical explanations.
- Isolates Live mutation planning behind human approval gates and readback verification.
- Stores user impressions to generate personalized production tips for future songs.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

try:
    import fcntl
except ImportError:  # pragma: no cover - exercised on Windows only
    fcntl = None
try:
    import msvcrt
except ImportError:  # pragma: no cover - exercised on POSIX only
    msvcrt = None

from kihachi_mcp.models.midi_candidate import CandidateClip, MidiCandidate
from kihachi_mcp.models.midi_composer import (
    MidiCompositionRequest,
    MusicalConstraints,
)
from kihachi_mcp.models.production_brief import (
    KEY_ENUM,
    PART_ORDER,
    SOURCE_AI,
    SOURCE_DEFAULT,
    SOURCE_GENRE,
    SOURCE_USER,
    ProductionBrief,
    SectionIntent,
    SourcedValue,
)
from kihachi_mcp.services.groove_rule_composer import _apply_swing_56, promote_variant_to_candidate
from kihachi_mcp.services.live_paths import bridge_state_dir, candidate_store_dir
from kihachi_mcp.services.midi_composer import DeterministicMIDIComposer
from kihachi_mcp.services.reference_analysis_card import AnalysisCard
from kihachi_mcp.services.session_pattern_builder import MidiNote, root_pitch


# =============================================================================
# Training Theme Definitions
# =============================================================================

@dataclass(frozen=True)
class TrainingTheme:
    theme_id: str
    category: str  # "composition" | "groove" | "sound_design"
    category_name: str
    title: str
    subtitle: str
    listening_points: list[str]
    theory_explanation: str
    beginner_explanation: str
    target_genre: str = "house"


TRAINING_THEMES: list[TrainingTheme] = [
    # -------------------------------------------------------------------------
    # グルーヴとスイング (Groove & Swing)
    # -------------------------------------------------------------------------
    TrainingTheme(
        theme_id="kick_bass_space",
        category="groove",
        category_name="グルーヴとスイング",
        title="キックとベースの住み分け（発音位置と休符）",
        subtitle="キックが鳴る瞬間、ベースを少し休ませると音が濁らず太くなる",
        listening_points=[
            "キックのアタック（ドン）とベースのアタックが同時に鳴ったとき、低域がもたついていないか？",
            "ベースが裏拍（16分のウラ）に抜けたとき、前に進む推進力（グルーヴ）が生まれているか？",
            "ベースのノートの長さ（音価）を短く切ったとき、キックの余韻がクリアに聴こえるか？",
        ],
        theory_explanation="キックとベースは低音域（40Hz〜150Hz）のエネルギーを奪い合います。時間軸上で発音タイミングをずらす（インターロッキング）か、ベース音価を16分休符分短縮することで、マスキングを防ぎヘッドルームを確保します。",
        beginner_explanation="太鼓（キック）と低音（ベース）が同時に大きな声を出すと、部屋の中がモワモワして聞き取りにくくなります。太鼓が鳴ったら低音は一瞬息を吸って休むようにすると、両方がキリッと引き締まって聴こえます。",
    ),
    TrainingTheme(
        theme_id="swing_microtiming",
        category="groove",
        category_name="グルーヴとスイング",
        title="16分音符のマイクロタイミングとスイング量",
        subtitle="カチッとした機械的な刻みと、体が自然に揺れるスイングの違い",
        listening_points=[
            "スイング0%（機械的ジャスト）では無機質で硬く聴こえるか？",
            "スイング56%（クラシックハウス・MPC）ではハイハットが粘り、横揺れが生まれるか？",
            "ベースとハイハットが同じスイング感で噛み合っているか？",
        ],
        theory_explanation="16分音符の偶数拍（2番目・4番目の16分）を時間的にわずかに遅らせる処理をシャッフル/スイングと呼びます。50%が完全グリッド、54〜58%がハウスやヒップホップで多用されるグルーヴ領域です。",
        beginner_explanation="メトロノーム通りにきっちり歩く行進曲と、ちょっと腰を振って軽快にスキップするダンスの違いです。ハイハットをほんの少し後ろに遅らせるだけで、曲に命が吹き込まれます。",
    ),
    TrainingTheme(
        theme_id="velocity_accents",
        category="groove",
        category_name="グルーヴとスイング",
        title="ベロシティの強弱とゴーストノート",
        subtitle="全部同じ音量で叩かない。弱い音（ゴースト）があるから強い音が際立つ",
        listening_points=[
            "すべての音が同じ音量（ベロシティ100固定）のとき、一本調子で平坦に聴こえないか？",
            "アクセント音（ベロシティ115〜120）が小節の頭やシンコペーションで際立っているか？",
            "合間に囁くように入る小音量ノート（ゴーストノート: 60〜75）が隙間を埋めているか？",
        ],
        theory_explanation="ベロシティ（打鍵の強さ）のダイナミックレンジを持たせることで、人間のドラマーやベーシストの演奏ニュアンスを再現します。アクセントとゴーストノートの対比が反復音楽の飽きを防ぎます。",
        beginner_explanation="普通の声だけでずっと喋られると眠くなりますが、ヒソヒソ声とハッキリした声が混ざると自然に耳を傾けたくなります。小さな音を隠し味として挟むことで、リズムに立体感が生まれます。",
    ),

    # -------------------------------------------------------------------------
    # 作曲 (Composition)
    # -------------------------------------------------------------------------
    TrainingTheme(
        theme_id="call_and_response",
        category="composition",
        category_name="作曲",
        title="フレーズの問いと答え（コール＆レスポンス）",
        subtitle="1小節目の問いかけに対して、2小節目が応える会話のようなフレーズ作り",
        listening_points=[
            "前半（1小節目）で音が上に上がって「問いかけ」の雰囲気を作っているか？",
            "後半（2小節目）でルート音（基音）に着地して「答え」を出しているか？",
            "ドラムやベースのリズムパターンと会話が成立しているか？",
        ],
        theory_explanation="フレーズの前半（先行動機/アンテセデント）に緊張（テンション音や非終止音）を持たせ、後半（後行動機/コンシクエント）でトニックに解決する伝統的な音楽構文です。ハウスでは2小節または4小節単位で周期を作ります。",
        beginner_explanation="「どこ行くの？」（問いかけ）「ここに行くよ！」（答え）のように、フレーズを会話にする手法です。ずっと同じことを言い続けるのではなく、問いと答えのペアにすることで、聴き手は安心してノレるようになります。",
    ),
    TrainingTheme(
        theme_id="motif_repetition_variation",
        category="composition",
        category_name="作曲",
        title="モチーフの反復と変化（飽きさせないループの美学）",
        subtitle="3回繰り返して覚えさせ、4回目に少し変えてハッとさせる",
        listening_points=[
            "基本モチーフが頭にすんなり残るか？",
            "4小節目や8小節目の末尾で、フィルや音程の跳躍など小さな変化が起きているか？",
            "変化が大きすぎて別の曲のようになってしまっていないか？",
        ],
        theory_explanation="ミニマルミュージックやダンスミュージックにおける「反復の中の微細な変化（マイクロバリエーション）」です。3回の反復でリスナーに安心感と期待を与え、4回目の変奏で期待を心地よく裏切ります。",
        beginner_explanation="「同じ、同じ、同じ、ちょっと違う！」のリズムです。毎回違うメロディを歌うと覚えられず、全く同じだと退屈します。3回同じフレーズを繰り返して親しみを持たせ、4回目に少しだけ変化をつけるのが王道テクニックです。",
    ),
    TrainingTheme(
        theme_id="tension_release",
        category="composition",
        category_name="作曲",
        title="展開の密度と緊張・解放（ビルドとドロップの役割）",
        subtitle="音を増やして緊張を高め、一気に解放して踊らせる",
        listening_points=[
            "ビルド（直前）で低域（キックやベース）を抜き、高域の密度を上げているか？",
            "ドロップ（サビ）の1拍目で重低音と主役フレーズが一気に戻ってくる快感があるか？",
            "ブレイクで音が減ったときに、次に向けた期待感が高まるか？",
        ],
        theory_explanation="楽曲構造（アレンジ）におけるエネルギーマネジメントです。イントロからビルドアップで音密度とピッチを上昇させて緊張（Tension）を作り、ドロップでキックとベースを投入して一気に解放（Release）します。",
        beginner_explanation="ジェットコースターでゆっくり坂を登っているときのドキドキ感（緊張）と、一気に急降下するときの爽快感（解放）の関係です。サビを最高に気持ちよく聴かせるために、直前でわざと音を減らしたり音を高くしたりします。",
    ),

    # -------------------------------------------------------------------------
    # 音作り (Sound Design & Processing)
    # -------------------------------------------------------------------------
    TrainingTheme(
        theme_id="spatial_and_dynamics",
        category="sound_design",
        category_name="音作り",
        title="音の前後感と空間処理（リバーブ・EQ・ダッキング）",
        subtitle="主役は目の前に置き、装飾音は奥に広げる。低域はすっきり整理する",
        listening_points=[
            "キックとベースがドライ（エフェクトなし）で、センターの目の前にどっしり構えているか？",
            "コードやパッド、アルペジオにリバーブやディレイがかかり、部屋の奥に広がって聴こえるか？",
            "キックが鳴るたびにコードの音量がわずかに下がる（サイドチェイン）ことで、グルーヴがうねっているか？",
        ],
        theory_explanation="ミキシングの3次元空間配置（左右Pan、前後Reverb/Delay、上下EQ周波数）です。低音楽器（Kick, Sub）は完全モノラル・完全ドライで明瞭度を保ち、Mid〜High楽器（Stab, Arp）に空間系エフェクトとサイドチェインコンプを適用して奥行きを演出します。",
        beginner_explanation="集合写真で全員が最前列に並ぶと窮屈になります。キックとベースは一番前の真ん中に立たせ、コードや飾り音は一歩後ろに下がって手を広げてもらうように配置します。これで音がぶつからず、綺麗な立体空間になります。",
    ),
    TrainingTheme(
        theme_id="bass_timbre",
        category="sound_design",
        category_name="音作り",
        title="ベースの倍音を作る（Operator）",
        subtitle="同じベースフレーズで、丸い音から存在感のある音まで聴き比べる",
        listening_points=[
            "Aの丸い基音は、キックと重なっても低音の輪郭が残るか？",
            "Bの三角波は、小さなスピーカーでもベースの音程が聞き取りやすいか？",
            "Cの鋸歯状波は存在感が増す一方で、キックやコードを邪魔していないか？",
        ],
        theory_explanation="比較するのはOperatorのオシレーター波形が持つ倍音構成です。Aは基音中心、Bは奇数倍音を含む三角波、Cは高次倍音が多い鋸歯状波です。ノート、ベロシティ、音量、フィルター、エンベロープを揃え、音量差ではなく音色差を判断します。",
        beginner_explanation="同じメロディを、丸い口笛・少し鼻にかかった声・明るく張った声で歌い分ける練習です。音の高さやリズムは変えず、音の材料だけを変えて、曲の中で聞き取りやすい声を選びます。",
    ),
]


# =============================================================================
# Training Journal & User Reflection
# =============================================================================

@dataclass
class TrainingJournalEntry:
    entry_id: str
    theme_id: str
    theme_title: str
    theme_category: str
    source_type: str  # "reference_card" | "candidate"
    source_id: str
    source_title: str
    chosen_variant_id: str  # "A" | "B" | "C"
    chosen_variant_name: str
    user_feedback_tags: list[str]
    user_notes: str
    key_takeaway: str
    next_action_tip: str
    created_at: str
    promoted_candidate_id: str | None = None
    sound_design_recipe: dict[str, Any] = field(default_factory=dict)
    genre: str = ""
    genre_basis: str = ""


def default_journal_file() -> Path:
    base = Path(os.environ.get("KIHACHI_TRAINING_DIR") or Path(bridge_state_dir()) / "training")
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    return base / "journal.json"


class TrainingJournalStore:
    def __init__(self, file_path: Path | None = None) -> None:
        self.file_path = file_path or default_journal_file()

    def list_entries(self, *, strict: bool = False) -> list[TrainingJournalEntry]:
        if not self.file_path.exists():
            return []
        try:
            raw = json.loads(self.file_path.read_text(encoding="utf-8"))
            if not isinstance(raw, list):
                raise ValueError("training journal must contain a JSON array")
            if strict and any(not isinstance(item, dict) for item in raw):
                raise ValueError("training journal contains a non-object entry")
            return [TrainingJournalEntry(**item) for item in raw if isinstance(item, dict)]
        except Exception as exc:
            if strict:
                raise ValueError(f"学習履歴が読み取れないため、学習関連の処理を中止しました: {exc}") from exc
            return []

    @contextmanager
    def _journal_lock(self):
        """Serialize read-modify-write operations across Studio processes."""
        self.file_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        lock_path = self.file_path.with_name(f".{self.file_path.name}.lock")
        descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            with os.fdopen(descriptor, "a+b") as lock_file:
                if fcntl is not None:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
                    unlock = lambda: fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
                elif msvcrt is not None:
                    lock_file.seek(0, os.SEEK_END)
                    if lock_file.tell() == 0:
                        lock_file.write(b"\0")
                        lock_file.flush()
                    lock_file.seek(0)
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
                    unlock = lambda: (lock_file.seek(0), msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1))
                else:
                    raise OSError("この環境では学習履歴のファイルロックを利用できません")
                try:
                    yield
                finally:
                    unlock()
        except BaseException:
            # fdopen owns the descriptor after construction; close only when
            # setup failed before ownership transferred.
            try:
                os.close(descriptor)
            except OSError:
                pass
            raise

    def _write_entries(self, entries: list[TrainingJournalEntry]) -> None:
        """Atomically replace the journal so interruption cannot truncate it."""
        self.file_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        payload = json.dumps([asdict(entry) for entry in entries], ensure_ascii=False, indent=2)
        fd, temporary_name = tempfile.mkstemp(prefix=f".{self.file_path.name}.", suffix=".tmp", dir=self.file_path.parent)
        try:
            # POSIX permissions are explicit; on Windows, the temporary file
            # inherits the containing directory's ACL and os.fchmod is absent.
            if hasattr(os, "fchmod"):
                os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as temporary_file:
                temporary_file.write(payload)
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
            os.replace(temporary_name, self.file_path)
        except BaseException:
            try:
                os.close(fd)
            except OSError:
                pass
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
            raise

    def save_entry(self, entry: TrainingJournalEntry) -> None:
        with self._journal_lock():
            entries = self.list_entries(strict=True)
            entries.insert(0, entry)
            self._write_entries(entries)

    def record_composition_application(self, package: dict[str, Any]) -> TrainingJournalEntry:
        """Persist what a generated composition actually consumed from learning.

        Application events remain separate from choice observations so merely
        generating a package never changes the learned preference profile.
        """
        application = package.get("learning_application") or {}
        theme_id = str(package.get("theme_id") or "")
        theme = next((item for item in TRAINING_THEMES if item.theme_id == theme_id), None)
        if theme is None:
            raise ValueError("作曲適用履歴に未知の学習テーマが指定されています")
        created_at = str(package.get("generated_at") or datetime.now(UTC).isoformat())
        event_id = str(package.get("composition_id") or uuid.uuid4().hex)
        entry = TrainingJournalEntry(
            entry_id=f"composition_{event_id[:32]}",
            theme_id=theme.theme_id,
            theme_title=theme.title,
            theme_category=theme.category,
            source_type="composition_application",
            source_id=str(package.get("card_id") or ""),
            source_title=str(package.get("source_title") or "自律作曲"),
            chosen_variant_id="",
            chosen_variant_name="",
            user_feedback_tags=[],
            user_notes=(
                f"生成時の学習適用: {'選好反映' if application.get('personal_preference_applied') else '選好未適用'} / "
                f"聴感メモ反映: {'あり' if application.get('sensory_feedback_applied') else 'なし'}。"
                "これは生成ログであり、人の試聴評価ではありません。"
            ),
            key_takeaway="",
            next_action_tip="",
            created_at=created_at,
            promoted_candidate_id=None,
            sound_design_recipe={
                "event_type": "composition_application",
                "composition_id": event_id,
                "applied": bool(application.get("personal_preference_applied") or application.get("sensory_feedback_applied")),
                "personal_preference_applied": bool(application.get("personal_preference_applied")),
                "sensory_feedback_applied": bool(application.get("sensory_feedback_applied")),
                "application_scope": application.get("application_scope"),
                "preferred_variant": application.get("preferred_variant"),
                "theme_observations": int(application.get("theme_observations") or 0),
                "required_observations": int(application.get("required_observations") or 5),
                "sensory_adjustment": application.get("sensory_adjustment"),
            },
        )
        with self._journal_lock():
            entries = self.list_entries(strict=True)
            if any(item.entry_id == entry.entry_id for item in entries):
                return next(item for item in entries if item.entry_id == entry.entry_id)
            entries.insert(0, entry)
            self._write_entries(entries)
        return entry

    def record_promoted_candidate(self, candidate: MidiCandidate) -> TrainingJournalEntry | None:
        """Learn only from an explicitly tagged human-promoted training candidate."""
        match = None
        for theme in TRAINING_THEMES:
            marker = f"[Training:{theme.theme_id}:"
            if marker not in candidate.brief.original_text:
                continue
            suffix = candidate.brief.original_text.split(marker, 1)[1]
            variant_id = suffix.split("]", 1)[0].strip()
            if variant_id in {"A", "B", "C"}:
                match = (theme, variant_id)
                break
        if match is None:
            return None
        theme, variant_id = match
        # Idempotency: re-opening or re-saving the same candidate must not
        # inflate the preference count.
        entry = TrainingJournalEntry(
            entry_id=uuid.uuid4().hex[:10],
            theme_id=theme.theme_id,
            theme_title=theme.title,
            theme_category=theme.category,
            source_type="promoted_training_candidate",
            source_id="cross_card",
            source_title="Studioで採用された学習候補",
            chosen_variant_id=variant_id,
            chosen_variant_name=f"案{variant_id}",
            user_feedback_tags=["studio_candidate_promoted"],
            user_notes="Studioで明示的に採用された学習候補の選択を自動記録。音質評価は推定していません。",
            key_takeaway="採用候補の選択傾向を次回の比較候補に反映します。",
            next_action_tip="A/B/Cの比較を続け、聴感上の理由は必要に応じて手動メモしてください。",
            created_at=datetime.now(UTC).isoformat(),
            promoted_candidate_id=candidate.candidate_id,
        )
        with self._journal_lock():
            entries = self.list_entries(strict=True)
            if any(item.promoted_candidate_id == candidate.candidate_id for item in entries):
                return None
            entries.insert(0, entry)
            self._write_entries(entries)
        return entry

    def get_recent_tips_for_production(self, limit: int = 3) -> list[dict[str, Any]]:
        """Return usable takeaways for subsequent song production sessions."""
        entries = self.list_entries(strict=True)
        tips = []
        for e in entries[:limit]:
            tips.append({
                "theme_title": e.theme_title,
                "chosen_style": e.chosen_variant_name,
                "takeaway": e.key_takeaway,
                "tip": e.next_action_tip,
                "date": e.created_at[:10],
            })
        return tips

    def learning_dashboard(self, references: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        """Return a compact, explainable snapshot for the Studio learning UI."""
        entries = self.list_entries(strict=True)
        reference_genres: dict[str, int] = {}
        for reference in references or []:
            genre = str(reference.get("genre") or "未分類").strip() or "未分類"
            reference_genres[genre] = reference_genres.get(genre, 0) + 1
        themes = []
        genre_learning: dict[str, dict[str, Any]] = {}
        scoped_by_theme = {theme.theme_id: [] for theme in TRAINING_THEMES}
        for entry in entries:
            if entry.theme_id in scoped_by_theme and entry.chosen_variant_id in {"A", "B", "C"}:
                scoped_by_theme[entry.theme_id].append(entry)
            if entry.source_type == "composition_application":
                continue
            genre = str(entry.genre or "").strip()
            basis = entry.genre_basis or "legacy_or_unknown"
            # Older manual reflections stored the linked YouTube reference in
            # their note. Recover its declared genre without guessing from the
            # generated candidate title or theme default.
            if not genre and entry.source_type == "reference_card":
                linked_note = str(entry.user_notes or "")
                matched_reference = next((
                    reference for reference in references or []
                    if str(reference.get("title") or "").strip()
                    and str(reference.get("title")).strip() in linked_note
                ), None)
                if matched_reference:
                    genre = str(matched_reference.get("genre") or "").strip()
                    if genre:
                        basis = "linked_reference_metadata"
            if not genre:
                continue
            meter = genre_learning.setdefault(genre, {
                "practice_records": 0, "human_choices": 0,
                "choice_counts": {variant: 0 for variant in ("A", "B", "C")},
                "basis_counts": {},
            })
            meter["practice_records"] += 1
            meter["basis_counts"][basis] = meter["basis_counts"].get(basis, 0) + 1
            if entry.source_type == "reference_card" and entry.chosen_variant_id in {"A", "B", "C"}:
                meter["human_choices"] += 1
                meter["choice_counts"][entry.chosen_variant_id] += 1
        for theme in TRAINING_THEMES:
            scoped = scoped_by_theme[theme.theme_id]
            counts = {variant: sum(e.chosen_variant_id == variant for e in scoped) for variant in ("A", "B", "C")}
            preferred = max(counts, key=counts.get) if scoped else None
            active = len(scoped) >= 5 and preferred is not None and counts[preferred] / len(scoped) >= 0.5
            sensory = self.learning_profile(
                theme.theme_id, "dashboard", [], _entries=entries, _choices=scoped
            )["sensory_profile"]
            themes.append({
                "theme_id": theme.theme_id,
                "title": theme.title,
                "category": theme.category_name,
                "observations": len(scoped),
                "required_observations": 5,
                "choice_counts": counts,
                "preferred_variant": preferred,
                "active": active,
                "sensory_profile": sensory,
                "progress_percent": min(100, round(len(scoped) / 5 * 100)),
            })
        return {
            "ok": True,
            "total_observations": sum(e.source_type != "composition_application" for e in entries),
            "automatic_observations": sum(e.source_type in {"promoted_training_candidate", "studio_ab_evaluation"} for e in entries),
            "human_reflections": sum(e.source_type not in {"promoted_training_candidate", "studio_ab_evaluation", "composition_application"} for e in entries),
            "composition_applications": {
                "total": sum(e.source_type == "composition_application" for e in entries),
                "applied": sum(
                    e.source_type == "composition_application"
                    and bool((e.sound_design_recipe or {}).get("applied"))
                    for e in entries
                ),
                "recent_events": [
                    {
                        "created_at": e.created_at,
                        "theme_id": e.theme_id,
                        "theme_title": e.theme_title,
                        "source_title": e.source_title,
                        "application": e.sound_design_recipe,
                        "note": e.user_notes,
                    }
                    for e in entries if e.source_type == "composition_application"
                ][:12],
            },
            "reference_genres": reference_genres,
            "genre_learning": genre_learning,
            "themes": themes,
            "studio_swing": self.learned_studio_swing(_entries=entries),
            "recent_events": [
                {
                    "created_at": e.created_at,
                    "theme_id": e.theme_id,
                    "theme_title": e.theme_title,
                    "variant_id": e.chosen_variant_id,
                    "variant_name": e.chosen_variant_name,
                    "source_type": e.source_type,
                    "note": e.user_notes,
                    "sound_design_recipe": e.sound_design_recipe,
                    "feedback_tags": e.user_feedback_tags,
                    "application": e.sound_design_recipe if e.source_type == "composition_application" else None,
                }
                for e in entries if e.source_type != "composition_application"
            ][:12],
            "learning_policy": "StudioのA/Bプレビュー評価は候補ごとに自動記録。同点は選好に数えません。5件以上かつ60%以上の偏りで、明示スイング指定のない次回制作の既定スイングに反映します。参考カードのA/B/C評価もテーマ別に蓄積。Liveへは自動適用しません。",
        }

    def record_studio_evaluation(self, candidate_id: str, rating: str) -> TrainingJournalEntry | None:
        """Automatically learn the user's normal Studio A/B groove evaluation."""
        with self._journal_lock():
            entries = self.list_entries(strict=True)
            existing_index = next((
                index for index, entry in enumerate(entries)
                if entry.source_type == "studio_ab_evaluation"
                and entry.promoted_candidate_id == candidate_id
            ), None)
            if rating == "A_BETTER":
                variant_id, name = "A", "50% ストレート"
            elif rating == "B_BETTER":
                variant_id, name = "B", "54% スイング"
            else:
                if rating == "EQUAL" and existing_index is not None:
                    del entries[existing_index]
                    self._write_entries(entries)
                return None
            theme = next(t for t in TRAINING_THEMES if t.theme_id == "swing_microtiming")
            if existing_index is not None:
                existing = entries[existing_index]
                entries[existing_index] = TrainingJournalEntry(
                    **{
                        **asdict(existing),
                        "chosen_variant_id": variant_id,
                        "chosen_variant_name": name,
                        "created_at": datetime.now(UTC).isoformat(),
                    }
                )
                self._write_entries(entries)
                return entries[existing_index]
            entry = TrainingJournalEntry(
                entry_id=uuid.uuid4().hex[:10],
                theme_id=theme.theme_id,
                theme_title=theme.title,
                theme_category=theme.category,
                source_type="studio_ab_evaluation",
                source_id="studio",
                source_title="Studio A/Bプレビュー評価",
                chosen_variant_id=variant_id,
                chosen_variant_name=name,
                user_feedback_tags=["automatic_studio_evaluation"],
                user_notes="StudioのA/Bプレビュー評価から自動記録。",
                key_takeaway="",
                next_action_tip="",
                created_at=datetime.now(UTC).isoformat(),
                promoted_candidate_id=candidate_id,
            )
            entries.insert(0, entry)
            self._write_entries(entries)
            return entry

    def learned_studio_swing(self, *, _entries: list[TrainingJournalEntry] | None = None) -> dict[str, Any]:
        """Return a cautious default only after repeated directional ratings."""
        entries = [
            e for e in (_entries if _entries is not None else self.list_entries(strict=True))
            if e.theme_id == "swing_microtiming"
            and e.source_type == "studio_ab_evaluation"
            and e.chosen_variant_id in {"A", "B"}
        ]
        counts = {variant: sum(e.chosen_variant_id == variant for e in entries) for variant in ("A", "B")}
        total = len(entries)
        preferred = max(counts, key=counts.get) if total else None
        active = total >= 5 and preferred is not None and counts[preferred] / total >= 0.6
        return {
            "active": active,
            "observations": total,
            "choice_counts": counts,
            "preferred_variant": preferred,
            "swing_ratio": (0.50 if preferred == "A" else 0.54) if active else None,
            "minimum_observations": 5,
            "scope": "studio_default_only_when_brief_has_no_explicit_swing",
        }

    def learning_profile(
        self,
        theme_id: str,
        card_id: str,
        references: list[dict[str, Any]],
        *,
        _entries: list[TrainingJournalEntry] | None = None,
        _choices: list[TrainingJournalEntry] | None = None,
    ) -> dict[str, Any]:
        """Build an explainable, per-theme profile from explicit choices.

        A card id remains in the response for traceability, but observations from
        other cards are useful too: they reflect the user's preference for the
        musical concept, rather than a single reference recording.
        """
        choices = _choices if _choices is not None else [
            entry for entry in (_entries if _entries is not None else self.list_entries(strict=True))
            if entry.theme_id == theme_id and entry.chosen_variant_id in {"A", "B", "C"}
        ]
        notes = [
            str(ref.get("musical_notes", "")) for ref in references
            if ref.get("listened") and ref.get("musical_notes")
        ]
        counts = {variant: sum(e.chosen_variant_id == variant for e in choices) for variant in ("A", "B", "C")}
        best = max(counts, key=counts.get) if choices else None
        total = len(choices)
        active = total >= 5 and best is not None and counts[best] / total >= 0.5
        reflections = [
            entry for entry in choices
            if entry.source_type == "reference_card" and entry.user_feedback_tags
        ]
        feedback_counts: dict[str, int] = {}
        for entry in reflections:
            for tag in set(entry.user_feedback_tags):
                feedback_counts[tag] = feedback_counts.get(tag, 0) + 1
        feedback_total = sum(feedback_counts.values())
        ranked_feedback = sorted(feedback_counts.items(), key=lambda item: item[1], reverse=True)
        strongest_feedback = ranked_feedback[0][0] if ranked_feedback else None
        feedback_is_unambiguous = (
            len(ranked_feedback) < 2
            or ranked_feedback[0][1] > ranked_feedback[1][1]
        )
        sensory_active = (
            theme_id == "kick_bass_space" and len(reflections) >= 3 and strongest_feedback is not None
            and feedback_is_unambiguous
            and feedback_counts[strongest_feedback] / max(1, len(reflections)) >= 0.5
        )
        return {
            "active": active,
            "observations": total,
            "choice_counts": counts,
            "preferred_variant": best,
            "sensory_profile": {
                "active": sensory_active,
                "reflection_count": len(reflections),
                "feedback_counts": feedback_counts,
                "dominant_feedback": strongest_feedback if sensory_active else None,
                "total_feedback_selections": feedback_total,
                "source": "explicit_human_listening_feedback",
                "supported_theme": "kick_bass_space",
            },
            "scope_type": "theme",
            "youtube_reference_count": len(notes),
            "youtube_notes_are_context_only": True,
            "youtube_note_examples": notes[:3],
            "scope": {"theme_id": theme_id, "card_id": card_id},
            "profile_version": "1",
        }


# =============================================================================
# Training Session Variation Generator
# =============================================================================

def generate_training_variants(
    theme: TrainingTheme,
    card: AnalysisCard,
    key: str | None = None,
    bars: int = 8,
    seed: int = 42,
    learning_profile: dict[str, Any] | None = None,
    reference_conditioned: bool = False,
) -> dict[str, Any]:
    """Generate 3 contrasting variations (A/B/C) tailored specifically to the training theme."""
    rules = card.generation_rules
    tempo, tempo_source = resolve_reference_tempo(card)
    key = resolve_reference_key(card, key)
    reference_profile = reference_generation_profile(card, key)
    bass_confident = reference_profile["bass_confidence"] >= 0.55
    kick_confident = reference_profile["kick_confidence"] >= 0.55
    base_sync = float(reference_profile["bass_kick_separation_ratio"] if bass_confident else 0.5)
    base_rest = float(reference_profile["bass_rest_ratio"] if bass_confident else 0.35)
    is_four_floor = reference_profile["kick_style"] == "four_on_the_floor" if kick_confident else True

    variants = []
    sensory_adjustment = None

    if theme.theme_id == "kick_bass_space":
        # ---------------------------------------------------------------------
        # Theme: Kick & Bass Space
        # Variant A: 同時発音・密集型（基準案）
        # Variant B: インターロッキング・裏拍配置（グルーヴ最大化）
        # Variant C: サブ持続＋サイドチェインダッキング（モダンハウス）
        # ---------------------------------------------------------------------
        # A stable per-profile seed personalizes the generated phrase while the
        # A/B/C musical contrast itself remains unchanged.
        profile_seed = seed
        if learning_profile and learning_profile.get("active"):
            profile_seed += {"A": 1709, "B": 2713, "C": 3911}.get(
                learning_profile.get("preferred_variant"), 0
            )
        composer_a = DeterministicMIDIComposer(seed=profile_seed)
        kick_style = ("four_on_the_floor" if is_four_floor else "broken_beat") if reference_conditioned else "four_on_the_floor"
        kick_density = _bounded_density(reference_profile.get("kick_density_per_bar"), 4.0) if reference_conditioned and kick_confident else None
        bass_density = _bounded_density(reference_profile.get("bass_density_per_bar"), 6.0) if reference_conditioned and bass_confident else None
        sync_a = base_sync if reference_conditioned else 0.1
        sync_b = min(0.98, base_sync + 0.15) if reference_conditioned else 0.8
        sync_c = max(0.02, base_sync - 0.15) if reference_conditioned else 0.4
        sensory_profile = (learning_profile or {}).get("sensory_profile", {})
        sensory_adjustment = None
        if sensory_profile.get("active"):
            dominant = sensory_profile.get("dominant_feedback")
            if dominant in {"低音がスッキリ抜けた", "自然なノリが出た", "自然にノれる", "低音がスッキリした", "ノリが出た"}:
                delta = 0.05 if dominant not in {"低音がスッキリ抜けた", "低音がスッキリした"} else 0.07
                sensory_adjustment = {"scope": "A/B/C共通の基準", "syncopation_delta": delta, "reason": dominant}
            elif dominant in {"少しやりすぎに感じた", "音数が多すぎる", "シンプルで使いやすい"}:
                sensory_adjustment = {"scope": "A/B/C共通の基準", "syncopation_delta": -0.05, "reason": dominant}
            if sensory_adjustment:
                sync_a = max(0.02, min(0.98, sync_a + sensory_adjustment["syncopation_delta"]))
                sync_b = max(0.02, min(0.98, sync_b + sensory_adjustment["syncopation_delta"]))
                sync_c = max(0.02, min(0.98, sync_c + sensory_adjustment["syncopation_delta"]))
        rest_a, rest_b, rest_c = (
            (base_rest, min(0.9, base_rest + 0.1), max(0.05, base_rest - 0.1))
            if reference_conditioned else (0.15, 0.45, 0.20)
        )
        kick_density_a = MusicalConstraints(key=key, tempo=tempo, bars=bars, role="drums", grid="1/16", kick_style=kick_style, kick_density_per_bar=kick_density)
        kick_a = composer_a.compose(MidiCompositionRequest(constraints=kick_density_a, operation="generate"))
        c_bass_a = MusicalConstraints(key=key, tempo=tempo, bars=bars, role="bass", grid="1/16", syncopation=sync_a, ghost_notes=(base_rest < 0.25) if reference_conditioned else False, avoid_kick_collision=(base_sync >= 0.45 and bass_confident) if reference_conditioned else False, kick_onsets=tuple(n.start for n in kick_a if n.pitch == 36), bass_density_per_bar=bass_density, note_length_max=max(0.2, min(0.75, 0.25 + (1.0 - rest_a) * 0.5)) if reference_conditioned else 0.75)
        bass_a = composer_a.compose(MidiCompositionRequest(constraints=c_bass_a, operation="generate"))

        variants.append({
            "variant_id": "A",
            "name": "参考特徴フィット（基準案）" if reference_conditioned else "同時発音型（直線的・アタック重複）",
            "concept": "参考音源から測ったBPM・キック型・ベース密度・キック分離傾向を基準に、新しいノート列を生成します。原曲のノート列は使いません。" if reference_conditioned else "キックとベースが拍の頭で同時に鳴るパターン。低音のエネルギーが密集し、力強いですが抜け感は少なめです。",
            "novice_summary": "参考音源のリズム特徴に近づける基準案です。" if reference_conditioned else "太鼓とベースが「いっせーのせ」で一緒に鳴ります。力強いですが、ちょっと音が詰まって聴こえます。",
            "technical_summary": "検出した参考BPM、キック様式、密度、休符率、キックとの分離率をMIDI生成条件へ反映します。" if reference_conditioned else "ビートのダウンビート（1拍目、2拍目等）でキックとベースノートが重なるため、50〜100Hz帯のマスキングが発生しやすい構造です。",
            "what_to_listen": "キックの音がベースの重低音に埋もれず輪郭が保てているか、音量を上げると濁らないかに注目してください。",
            "live_sound_design": {
                "eq_recommendation": "キック 60Hz ブースト、ベース 60Hz ディップ（-3dB Q=1.5）で周波数の住み分けを試す",
                "sidechain": "なし（純粋な同時演奏）",
                "effect_preset": "Dry Direct",
            },
            "parameters": {"syncopation": sync_a, "rest_ratio": rest_a, "kick_bass_overlap": "reference_target" if reference_conditioned else "high", "reference_target": reference_profile if reference_conditioned else None},
            "notes": {
                "Kick": [_note_to_dict(n) for n in kick_a if n.pitch in (36, 35)],
                "Bass": [_note_to_dict(n) for n in bass_a],
            },
        })

        composer_b = DeterministicMIDIComposer(seed=profile_seed + 101)
        c_bass_b = MusicalConstraints(key=key, tempo=tempo, bars=bars, role="bass", grid="1/16", syncopation=sync_b, ghost_notes=(rest_b < 0.25) if reference_conditioned else False, avoid_kick_collision=True, kick_onsets=tuple(n.start for n in kick_a if n.pitch == 36), bass_density_per_bar=max(1.0, min(16.0, bass_density * 1.1)) if bass_density is not None else None, note_length_max=max(0.2, min(0.75, 0.25 + (1.0 - rest_b) * 0.5)) if reference_conditioned else 0.75)
        bass_b = composer_b.compose(MidiCompositionRequest(constraints=c_bass_b, operation="generate"))
        # Shift bass notes away from downbeat to pure offbeats (interlocking)
        interlocked_bass = []
        for n in bass_b:
            start_val = getattr(n, "start", getattr(n, "start_beats", 0.0))
            dur_val = getattr(n, "duration", getattr(n, "duration_beats", 0.25))
            beat_in_bar = start_val % 4.0
            if beat_in_bar in (0.0, 1.0, 2.0, 3.0):
                interlocked_bass.append(MidiNote(pitch=n.pitch, start_beats=start_val + 0.5, duration_beats=min(dur_val, 0.4), velocity=n.velocity))
            else:
                interlocked_bass.append(n)

        variants.append({
            "variant_id": "B",
            "name": "分離率を上げた参考変奏" if reference_conditioned else "裏拍インターロッキング型（隙間を縫うグルーヴ）",
            "concept": "参考音源の測定値からキックとの間隔とシンコペーションを少し強めた変奏案です。" if reference_conditioned else "キックの直後にベースを打ち込み、時間軸で完全に役割を分担。推進力とクリアな低音が得られます。",
            "novice_summary": "太鼓が鳴った「直後」に低音がポンと入ります。お互いが譲り合うことで、体が自然に揺れるノリが生まれます。",
            "technical_summary": "キックのディケイタイムを避けてベースを裏拍（8分裏・16分裏）に配置。帯域の競合が時間軸上で完全にゼロになるため、最もクリーンに鳴る古典的ハウス構造です。",
            "what_to_listen": "キックがズドンと鳴った後、ベースが跳ねるように飛び出してくる「バウンス感」を聴き取ってください。",
            "live_sound_design": {
                "eq_recommendation": "ベースの80〜120Hzをパンチ重視で適度に持ち上げ、キックの余韻と対比させる",
                "sidechain": "不要（タイミングだけで住み分け完了）",
                "effect_preset": "Clean Interlock",
            },
            "parameters": {"syncopation": sync_b, "rest_ratio": rest_b, "kick_bass_overlap": "reduced" if reference_conditioned else "zero", "reference_target": reference_profile if reference_conditioned else None},
            "notes": {
                "Kick": [_note_to_dict(n) for n in kick_a if n.pitch in (36, 35)],
                "Bass": [_note_to_dict(n) for n in interlocked_bass],
            },
        })

        composer_c = DeterministicMIDIComposer(seed=profile_seed + 202)
        c_bass_c = MusicalConstraints(key=key, tempo=tempo, bars=bars, role="bass", grid="1/16", syncopation=sync_c, ghost_notes=True, avoid_kick_collision=(sync_c >= 0.45) if reference_conditioned else False, kick_onsets=tuple(n.start for n in kick_a if n.pitch == 36), bass_density_per_bar=max(1.0, min(16.0, bass_density * 0.9)) if bass_density is not None else None, note_length_max=max(0.2, min(0.75, 0.25 + (1.0 - rest_c) * 0.5)) if reference_conditioned else 0.75)
        bass_c = composer_c.compose(MidiCompositionRequest(constraints=c_bass_c, operation="generate"))
        variants.append({
            "variant_id": "C",
            "name": "密度と休符を変えた参考変奏" if reference_conditioned else "持続サブ＋サイドチェインダッキング型（モダンうねり）",
            "concept": "参考音源の測定値からベース密度と休符率を少し変えた変奏案です。MIDIにはサイドチェイン処理を含みません。" if reference_conditioned else "長めのベースを流し、キックが鳴る瞬間だけ音量を強制的に下げるモダンクラブ定番の手法。",
            "novice_summary": "基準案より音数と休符の取り方を変えた比較案です。" if reference_conditioned else "低音が長く鳴り続けますが、太鼓が鳴る瞬間だけ「フッ」と引っ込み、直後にグワッと戻ってきます。",
            "technical_summary": "ベース密度・休符率・キックとの分離を基準案から変えています。音量のサイドチェイン処理はこのMIDIには実装していません。" if reference_conditioned else "持続音価のサブベースに対して、キックトリガーのサイドチェインコンプレッサーを深め（Gain Reduction 4〜6dB）にかけることで、ポンピングエフェクトとうねりを生み出します。",
            "what_to_listen": "キックが鳴った直後に低音が波のように押し寄せてくる「パンピング（息継ぎ）」の感覚に耳を澄ませてください。",
            "live_sound_design": {
                "eq_recommendation": "サブベースの30Hz以下をハイパス（18dB/oct）でカットし、不要な超低域の濁りを除去",
                "sidechain": "Ableton Compressor: キックをルーティングしRatio 4:1, Attack 2ms, Release 80ms",
                "effect_preset": "Sidechain Pumping",
            },
            "parameters": {"syncopation": sync_c, "rest_ratio": rest_c, "sidechain_ducking": "not_encoded_in_midi" if reference_conditioned else "active", "reference_target": reference_profile if reference_conditioned else None},
            "notes": {
                "Kick": [_note_to_dict(n) for n in kick_a if n.pitch in (36, 35)],
                "Bass": [_note_to_dict(n) for n in bass_c],
            },
        })

    elif theme.theme_id == "swing_microtiming":
        # ---------------------------------------------------------------------
        # Theme: Swing & Microtiming
        # Variant A: 50% ストレート
        # Variant B: 56% MPC スイング
        # Variant C: 62% ヘビースイング
        # ---------------------------------------------------------------------
        composer = DeterministicMIDIComposer(seed=seed)
        c_drums = MusicalConstraints(key=key, tempo=tempo, bars=bars, role="drums", grid="1/16", kick_style=("four_on_the_floor" if is_four_floor else "broken_beat") if reference_conditioned else "four_on_the_floor", kick_density_per_bar=_bounded_density(reference_profile.get("kick_density_per_bar"), 4.0) if reference_conditioned and kick_confident else None)
        c_bass = MusicalConstraints(key=key, tempo=tempo, bars=bars, role="bass", grid="1/16", syncopation=base_sync if reference_conditioned else 0.6, bass_density_per_bar=_bounded_density(reference_profile.get("bass_density_per_bar"), 6.0) if reference_conditioned and bass_confident else None)
        raw_drums = composer.compose(MidiCompositionRequest(constraints=c_drums, operation="generate"))
        raw_bass = composer.compose(MidiCompositionRequest(constraints=c_bass, operation="generate"))

        # Variant A: Straight 50%
        variants.append({
            "variant_id": "A",
            "name": "スイング0%（完全グリッド・ソリッド）",
            "concept": "1ミリのズレもない完璧な16分音符。タイトで電子的、ソリッドなミニマルテクノ・テックハウス感。",
            "novice_summary": "時計の針のように正確無比なリズムです。カチッとしていて、機械的なカッコよさがあります。",
            "technical_summary": "16分音符の各グリッド位置（0.0, 0.25, 0.5, 0.75拍）にクオンタイズ率100%で配置。無機質でストイックなテンションを保ちます。",
            "what_to_listen": "ハイハットのチキチキした刻みにブレがなく、前にも後ろにも傾かない安定感を聴いてください。",
            "live_sound_design": {"groove_pool": "None (Straight 16th)", "timing_offset": "0ms"},
            "parameters": {"swing_ratio": 0.50, "groove_feel": "straight"},
            "notes": {
                "Hats": [_note_to_dict(n) for n in raw_drums if n.pitch in (42, 44, 46)],
                "Bass": [_note_to_dict(n) for n in raw_bass],
            },
        })

        # Variant B: 56% Swing
        swung_hats_b = []
        for n in raw_drums:
            if n.pitch in (42, 44, 46):
                start_val = getattr(n, "start", getattr(n, "start_beats", 0.0))
                dur_val = getattr(n, "duration", getattr(n, "duration_beats", 0.2))
                pos = _apply_swing_56(start_val)
                swung_hats_b.append(MidiNote(pitch=n.pitch, start_beats=pos, duration_beats=dur_val, velocity=n.velocity))
        swung_bass_b = []
        for n in raw_bass:
            start_val = getattr(n, "start", getattr(n, "start_beats", 0.0))
            dur_val = getattr(n, "duration", getattr(n, "duration_beats", 0.2))
            pos = _apply_swing_56(start_val)
            swung_bass_b.append(MidiNote(pitch=n.pitch, start_beats=pos, duration_beats=dur_val, velocity=n.velocity))

        variants.append({
            "variant_id": "B",
            "name": "スイング56%（黄金比・MPCクラシックスイング）",
            "concept": "ハウスミュージックの歴史を創った王道スイング。偶数拍がわずかに溜まり、腰が自然に動くノリ。",
            "novice_summary": "歩きながら軽くリズムに乗っているような、心地よいスキップ感です。体が勝手に揺れ始めます。",
            "technical_summary": "16分裏拍が約15〜20ms後方に遅延するスイング率56%。シャッフルほど3連符に寄らず、2拍・4拍のタメを強調します。",
            "what_to_listen": "ハットとベースの2音目が「タッ・タッ」とほんの少し遅れて入ってくる引っかかりを感じてください。",
            "live_sound_design": {"groove_pool": "MPC 16 Swing-56", "timing_offset": "+18ms on evens"},
            "parameters": {"swing_ratio": 0.56, "groove_feel": "mpc_classic"},
            "notes": {
                "Hats": [_note_to_dict(n) for n in swung_hats_b],
                "Bass": [_note_to_dict(n) for n in swung_bass_b],
            },
        })

        # Variant C: 62% Heavy Swing
        def _apply_swing_62(beat: float) -> float:
            base = int(beat)
            frac = beat - base
            if abs(frac - 0.25) < 0.02:
                return base + 0.31
            elif abs(frac - 0.75) < 0.02:
                return base + 0.81
            return beat

        swung_hats_c = [
            MidiNote(
                pitch=n.pitch,
                start_beats=_apply_swing_62(getattr(n, "start", getattr(n, "start_beats", 0.0))),
                duration_beats=getattr(n, "duration", getattr(n, "duration_beats", 0.2)),
                velocity=n.velocity,
            )
            for n in raw_drums
            if n.pitch in (42, 44, 46)
        ]
        swung_bass_c = [
            MidiNote(
                pitch=n.pitch,
                start_beats=_apply_swing_62(getattr(n, "start", getattr(n, "start_beats", 0.0))),
                duration_beats=getattr(n, "duration", getattr(n, "duration_beats", 0.2)),
                velocity=n.velocity,
            )
            for n in raw_bass
        ]

        variants.append({
            "variant_id": "C",
            "name": "スイング62%（ディープ＆ファンキー・強シャッフル）",
            "concept": "ガラージやディープハウス、ジャズ寄りの強い跳ね。遅れが明確になり、濃厚なグルーヴを生みます。",
            "novice_summary": "かなり強めに跳ねています。陽気でファンキー、クラブの深夜帯にじっくり踊りたくなる雰囲気です。",
            "technical_summary": "3連符（66.7%）に近づけた強いスイング。ダウンビートとアップビートの長さの比率が約2:1になり、顕著なバウンスを生みます。",
            "what_to_listen": "跳ねが強くなることで、音がゆっくり（ルーズに）感じられる錯覚を楽しんでください。",
            "live_sound_design": {"groove_pool": "Logic 16 Swing-62", "timing_offset": "+35ms on evens"},
            "parameters": {"swing_ratio": 0.62, "groove_feel": "heavy_shuffle"},
            "notes": {
                "Hats": [_note_to_dict(n) for n in swung_hats_c],
                "Bass": [_note_to_dict(n) for n in swung_bass_c],
            },
        })

    elif theme.theme_id == "velocity_accents":
        # Keep pitch and timing fixed for A/B. C adds only quiet snare ghosts.
        composer = DeterministicMIDIComposer(seed=seed)
        c_drums = MusicalConstraints(key=key, tempo=tempo, bars=bars, role="drums", grid="1/16")
        c_bass = MusicalConstraints(key=key, tempo=tempo, bars=bars, role="bass", grid="1/16", syncopation=base_sync)
        raw_drums = composer.compose(MidiCompositionRequest(constraints=c_drums, operation="generate"))
        raw_bass = composer.compose(MidiCompositionRequest(constraints=c_bass, operation="generate"))

        def notes_for_pitch(pitches: set[int]) -> list[dict[str, Any]]:
            return [_note_to_dict(note) for note in raw_drums if note.pitch in pitches]

        kick_b = notes_for_pitch({36, 35})
        snare_b = notes_for_pitch({38})
        hats_b = notes_for_pitch({42, 46})
        bass_b = [_note_to_dict(note) for note in raw_bass]
        all_tracks_b = {"Kick": kick_b, "Snare": snare_b, "Hats": hats_b, "Bass": bass_b}
        all_tracks_a = {
            part: [{**note, "velocity": 90} for note in notes]
            for part, notes in all_tracks_b.items()
        }
        ghost_snares = [
            {
                "pitch": 38,
                "start_beats": round(bar * 4.0 + offset, 3),
                "duration_beats": 0.08,
                "velocity": 60,
            }
            for bar in range(max(0, int(bars)))
            if bar % 2 == 1
            for offset in (1.75, 3.25)
        ]
        all_tracks_c = {
            part: [dict(note) for note in notes]
            for part, notes in all_tracks_b.items()
        }
        all_tracks_c["Snare"].extend(ghost_snares)
        all_tracks_c["Snare"].sort(key=lambda note: (note["start_beats"], note["pitch"]))

        variants = [
            {
                "variant_id": "A",
                "name": "均一ベロシティ・基準案",
                "concept": "発音位置と音高を保ち、全ノートのベロシティを90に揃えます。",
                "novice_summary": "すべて同じ強さで鳴らしたとき、リズムが平坦に聞こえるかを聴きます。",
                "technical_summary": "Kick、Snare、Hats、Bassの既存ノートを維持し、全ノートのベロシティのみ90に統一します。",
                "what_to_listen": "拍の強弱が少ないと、ノリや各パートの輪郭がどう変わるか聴いてください。",
                "live_sound_design": {},
                "parameters": {"velocity_mode": "uniform", "velocity": 90, "ghost_notes": False},
                "notes": all_tracks_a,
            },
            {
                "variant_id": "B",
                "name": "アクセント付き・基準グルーヴ",
                "concept": "同じ発音位置・音高のまま、拍や裏拍の強さに差をつけます。",
                "novice_summary": "強い音と弱い音の差で、リズムに弾みや立体感が出るかを聴きます。",
                "technical_summary": "A案とノートの位置・音高・音数を揃え、作曲器のアクセントベロシティを使います。",
                "what_to_listen": "Kickの頭、Snareのバックビート、Hatsの裏拍が聞き分けやすくなるか確認してください。",
                "live_sound_design": {},
                "parameters": {"velocity_mode": "accented", "ghost_notes": False},
                "notes": all_tracks_b,
            },
            {
                "variant_id": "C",
                "name": "アクセント＋ゴーストスネア",
                "concept": "B案に、2小節ごとに弱いスネアのゴーストノートを加えます。",
                "novice_summary": "強い拍のすき間に小さな音を入れ、リズムの会話が生まれるかを聴きます。",
                "technical_summary": "B案の全ノートを保ち、偶数小節の1.75拍と3.25拍にベロシティ60の短いスネアを追加します。",
                "what_to_listen": "ゴースト音がグルーヴに細かな動きを足すか、主役のKickやSnareを邪魔しないか聴いてください。",
                "live_sound_design": {},
                "parameters": {"velocity_mode": "accented", "ghost_notes": True, "ghost_velocity": 60},
                "notes": all_tracks_c,
            },
        ]

    elif theme.theme_id == "tension_release":
        # Build each repeated phrase by removing low end before its final-bar
        # release. C changes only the build's snare density relative to B.
        composer = DeterministicMIDIComposer(seed=seed)
        c_drums = MusicalConstraints(key=key, tempo=tempo, bars=bars, role="drums", grid="1/16")
        c_bass = MusicalConstraints(key=key, tempo=tempo, bars=bars, role="bass", grid="1/16", syncopation=base_sync)
        raw_drums = composer.compose(MidiCompositionRequest(constraints=c_drums, operation="generate"))
        raw_bass = composer.compose(MidiCompositionRequest(constraints=c_bass, operation="generate"))
        base_tracks = {
            "Kick": [_note_to_dict(n) for n in raw_drums if n.pitch in (36, 35)],
            "Snare": [_note_to_dict(n) for n in raw_drums if n.pitch == 38],
            "Hats": [_note_to_dict(n) for n in raw_drums if n.pitch in (42, 46)],
            "Bass": [_note_to_dict(n) for n in raw_bass],
        }
        total_bars = max(1, int(bars))
        cycle_bars = max(8, total_bars // 4)
        build_bars = max(3, cycle_bars // 4)
        build_start = cycle_bars - build_bars - 1

        def make_arrangement(add_snare_roll: bool = False) -> dict[str, list[dict[str, Any]]]:
            tracks = {part: [dict(note) for note in notes] for part, notes in base_tracks.items()}
            for part in ("Kick", "Bass"):
                tracks[part] = [
                    note
                    for note in tracks[part]
                    if not build_start <= (int(float(note["start_beats"]) // 4.0) % cycle_bars) < cycle_bars - 1
                ]
            if add_snare_roll:
                last_build_bar_in_cycle = cycle_bars - 2
                for bar in range(total_bars):
                    if bar % cycle_bars == last_build_bar_in_cycle:
                        for step in range(4):
                            tracks["Snare"].append(
                                {
                                    "pitch": 38,
                                    "start_beats": round(bar * 4.0 + 0.5 + step * 1.0, 3),
                                    "duration_beats": 0.1,
                                    "velocity": 68 + step * 10,
                                }
                            )
                tracks["Snare"].sort(key=lambda note: (note["start_beats"], note["pitch"]))
            return tracks

        tracks_a = {part: [dict(note) for note in notes] for part, notes in base_tracks.items()}
        tracks_b = make_arrangement()
        tracks_c = make_arrangement(add_snare_roll=True)
        variants = [
            {
                "variant_id": "A",
                "name": "一定エネルギー・基準案",
                "concept": "8小節を通してKickとBassを保ち、一定の密度で進みます。",
                "novice_summary": "音がずっと続くときの安定感と、展開が少なく感じるかを聴きます。",
                "technical_summary": "全区間でKickとBassを維持し、密度の上げ下げを加えない基準構成です。",
                "what_to_listen": "一定の推進力が心地よいか、次の展開を待ちたくなるかを聴いてください。",
                "live_sound_design": {},
                "parameters": {"low_end_build": False, "snare_build_roll": False},
                "notes": tracks_a,
            },
            {
                "variant_id": "B",
                "name": "低音を抜いて戻す案",
                "concept": "各フレーズ終盤でKickとBassを一時的に抜き、最後の小節で戻します。",
                "novice_summary": "低音が消えたあとに戻ると、解放感や期待感が強まるかを聴きます。",
                "technical_summary": f"{cycle_bars}小節周期の終盤{build_bars}小節で低域を抜き、周期最終小節で再投入します。",
                "what_to_listen": "低音を抜く長さが次の再投入を気持ちよく感じさせるか確認してください。",
                "live_sound_design": {},
                "parameters": {"low_end_build": True, "cycle_bars": cycle_bars, "build_bars": build_bars, "snare_build_roll": False},
                "notes": tracks_b,
            },
            {
                "variant_id": "C",
                "name": "低音抜き＋スネアビルド案",
                "concept": "B案の低音を抜く展開を保ち、ビルド最後の小節でスネアを段階的に増やします。",
                "novice_summary": "低音がない間にスネアが増えると、最後の戻りをより待ちたくなるかを聴きます。",
                "technical_summary": "B案と同じ低域配置に、最終ビルド小節の裏拍スネア4発をベロシティ68〜98で追加します。",
                "what_to_listen": "スネアの増加が期待を高めるか、密度が上がりすぎて煩雑にならないか聴いてください。",
                "live_sound_design": {},
                "parameters": {"low_end_build": True, "cycle_bars": cycle_bars, "build_bars": build_bars, "snare_build_roll": True},
                "notes": tracks_c,
            },
        ]

    elif theme.theme_id == "motif_repetition_variation":
        # Keep the same one-bar bass motif and kick pattern across all three
        # candidates. Compare one audible change at a time at phrase endings.
        composer = DeterministicMIDIComposer(seed=seed)
        c_kick = MusicalConstraints(key=key, tempo=tempo, bars=bars, role="drums", grid="1/16")
        c_motif = MusicalConstraints(
            key=key,
            tempo=tempo,
            bars=1,
            role="bass",
            grid="1/16",
            syncopation=base_sync,
        )
        kick_notes = [
            _note_to_dict(n)
            for n in composer.compose(MidiCompositionRequest(constraints=c_kick, operation="generate"))
            if n.pitch in (36, 35)
        ]
        motif = [
            _note_to_dict(n)
            for n in composer.compose(MidiCompositionRequest(constraints=c_motif, operation="generate"))
        ]
        total_bars = max(1, int(bars))
        bass_a = _repeat_bass_motif(motif, total_bars)
        bass_b = _repeat_bass_motif(motif, total_bars, vary_phrase_ending=True)
        bass_c = _repeat_bass_motif(
            motif,
            total_bars,
            vary_phrase_ending=True,
            octave_answer=True,
        )

        variants = [
            {
                "variant_id": "A",
                "name": "反復・基準案",
                "concept": "同じ1小節のベースモチーフを繰り返します。変化案を比べる基準です。",
                "novice_summary": "同じフレーズが続くと、どのくらい覚えやすく、どこで飽きるかを聴きます。",
                "technical_summary": "1小節の音高・発音位置・音価を全小節で反復します。",
                "what_to_listen": "モチーフを覚えやすいか、反復が長く続いたときに単調に感じるかを聴いてください。",
                "live_sound_design": {},
                "parameters": {"motif_period_bars": 1, "phrase_end_change": "none"},
                "notes": {"Kick": kick_notes, "Bass": bass_a},
            },
            {
                "variant_id": "B",
                "name": "末尾リズム変化案",
                "concept": "基準モチーフを保ち、4小節ごとの最後のベース音だけを16分音符ぶん前後させます。",
                "novice_summary": "ほぼ同じフレーズのまま、区切りの最後だけ少し変えたときの違いを聴きます。",
                "technical_summary": "音高と音数は保ち、4小節フレーズ末尾の最終ノートの発音位置を0.25拍だけ変更します。",
                "what_to_listen": "4小節目の最後に小さな合図が生まれるか、基本のノリが崩れていないかを聴いてください。",
                "live_sound_design": {},
                "parameters": {"motif_period_bars": 1, "phrase_end_change": "timing_0.25_beats"},
                "notes": {"Kick": kick_notes, "Bass": bass_b},
            },
            {
                "variant_id": "C",
                "name": "末尾オクターブ応答案",
                "concept": "B案と同じ末尾リズムにし、最後のベース音だけを1オクターブ移して応答を作ります。",
                "novice_summary": "B案のリズム変化に加えて、最後の音が高低に跳ぶことでフレーズの返事に聞こえるかを聴きます。",
                "technical_summary": "B案の発音位置を保ち、フレーズ末尾の最終ノートだけ同じ音名の別オクターブへ移します。",
                "what_to_listen": "B案と比べ、末尾の音域変化がフレーズを印象づけるか、低音のまとまりを損なわないかを聴いてください。",
                "live_sound_design": {},
                "parameters": {"motif_period_bars": 1, "phrase_end_change": "timing_and_octave"},
                "notes": {"Kick": kick_notes, "Bass": bass_c},
            },
        ]

    elif theme.theme_id == "call_and_response":
        # Use the same melodic motif and rhythm in every option. The comparison
        # focuses on phrase-ending pitch: repetition, tonic resolution, and its
        # octave placement.
        composer = DeterministicMIDIComposer(seed=seed)
        c_kick = MusicalConstraints(key=key, tempo=tempo, bars=bars, role="drums", grid="1/16")
        c_motif = MusicalConstraints(
            key=key,
            tempo=tempo,
            bars=1,
            role="melody",
            grid="1/16",
        )
        kick_notes = [
            _note_to_dict(n)
            for n in composer.compose(MidiCompositionRequest(constraints=c_kick, operation="generate"))
            if n.pitch in (36, 35)
        ]
        motif = [
            _note_to_dict(n)
            for n in composer.compose(MidiCompositionRequest(constraints=c_motif, operation="generate"))
        ]
        total_bars = max(1, int(bars))
        tonic_pitch = root_pitch(key, octave_offset=2)
        lead_a = _repeat_melody_motif(motif, total_bars)
        lead_b = _repeat_melody_motif(
            motif,
            total_bars,
            tonic_pitch=tonic_pitch,
            call_and_response=True,
        )
        lead_c = _repeat_melody_motif(
            motif,
            total_bars,
            tonic_pitch=tonic_pitch + 12,
            call_and_response=True,
        )

        variants = [
            {
                "variant_id": "A",
                "name": "反復・基準案",
                "concept": "同じ1小節のメロディを繰り返す基準案です。",
                "novice_summary": "同じ歌い回しが続くと、どれくらい覚えやすいかを聴きます。",
                "technical_summary": "音高、発音位置、音価を保った1小節モチーフの反復です。",
                "what_to_listen": "メロディが覚えやすいか、終わり方が単調に感じるかを聴いてください。",
                "live_sound_design": {},
                "parameters": {"phrase_form": "repetition", "answer_pitch": "unchanged"},
                "notes": {"Kick": kick_notes, "Lead": lead_a},
            },
            {
                "variant_id": "B",
                "name": "高い問い・主音の答え",
                "concept": "問いの小節末尾を高くし、次の答えの小節末尾をキーの主音に着地させます。",
                "novice_summary": "一度上に投げかけてから、主音に戻ると会話のように聞こえるかを聴きます。",
                "technical_summary": "2小節単位で問いの最終音を1オクターブ上げ、答えの最終音を主音にします。",
                "what_to_listen": "問いが続きを期待させ、答えで落ち着く感じが出るかを聴いてください。",
                "live_sound_design": {},
                "parameters": {"phrase_form": "call_and_response", "answer_pitch": "tonic"},
                "notes": {"Kick": kick_notes, "Lead": lead_b},
            },
            {
                "variant_id": "C",
                "name": "高い問い・高域の答え",
                "concept": "B案と同じ問いと答えを使い、答えの主音だけを1オクターブ上に置きます。",
                "novice_summary": "B案より明るく高い場所に答えると、フレーズの印象がどう変わるかを聴きます。",
                "technical_summary": "問いの処理とリズムはB案と同一で、答えの最終音を主音の1オクターブ上にします。",
                "what_to_listen": "B案と比べ、答えの高さがフレーズの解決感や存在感を強めるかを聴いてください。",
                "live_sound_design": {},
                "parameters": {"phrase_form": "call_and_response", "answer_pitch": "tonic_plus_octave"},
                "notes": {"Kick": kick_notes, "Lead": lead_c},
            },
        ]

    elif theme.theme_id == "bass_timbre":
        # Keep one phrase for all three options; the controlled variable is
        # Operator's oscillator waveform, not MIDI, velocity, or arrangement.
        composer = DeterministicMIDIComposer(seed=seed)
        c_drums = MusicalConstraints(key=key, tempo=tempo, bars=bars, role="drums", grid="1/16")
        c_bass = MusicalConstraints(key=key, tempo=tempo, bars=bars, role="bass", grid="1/16", syncopation=base_sync)
        raw_drums = composer.compose(MidiCompositionRequest(constraints=c_drums, operation="generate"))
        raw_bass = composer.compose(MidiCompositionRequest(constraints=c_bass, operation="generate"))
        timbre_notes = {
            "Kick": [_note_to_dict(n) for n in raw_drums if n.pitch in (36, 35)],
            "Bass": [_note_to_dict(n) for n in raw_bass],
        }
        shared = {
            "application_status": "manual_recommendation_only",
            "midi_identical_across_variants": True,
            "target_device": "Ableton Operator",
            "common_settings": {
                "filter": "LP24, cutoff 180 Hz, resonance 10%",
                "amp_envelope": "Attack 5 ms, Decay 250 ms, Sustain -6 dB, Release 80 ms",
                "effects": "off during comparison",
                "track_level": "match perceived loudness before judging",
            },
        }
        options = [
            (
                "A", "サイン波・丸いサブ", "基準",
                "音の芯になる基音を中心にした、丸く短いベースです。",
                "低域の基音が主役。Kickと重ねても輪郭が崩れないか聴きます。",
                {"oscillator_A_waveform": "Sine", "oscillator_B_level": "-inf dB", "filter_cutoff_hz": 180},
            ),
            (
                "B", "三角波・聞き取りやすい", "倍音を少し追加",
                "Aと同じフレーズに、少しだけ倍音を足して音程を聞き取りやすくします。",
                "基音に奇数倍音が加わります。小さなスピーカーでも音程が残るか聴きます。",
                {"oscillator_A_waveform": "Triangle", "oscillator_B_level": "-inf dB", "filter_cutoff_hz": 180},
            ),
            (
                "C", "鋸歯状波・前に出る", "倍音を多く追加",
                "フレーズはそのまま、倍音の多い波形でベースを前に出します。",
                "高次倍音が増えます。音量を揃えたうえで、存在感と耳当たりを比べます。",
                {"oscillator_A_waveform": "Saw D", "oscillator_B_level": "-inf dB", "filter_cutoff_hz": 180},
            ),
        ]
        variants = [
            {
                "variant_id": variant_id,
                "name": name,
                "concept": f"{concept} A/B/CのKickとBass MIDIは同一で、Operatorの波形だけを変える比較です。",
                "novice_summary": beginner,
                "technical_summary": f"{detail} 共通設定を保ち、変更する主条件はOscillator Aの波形だけです。",
                "what_to_listen": detail,
                "live_sound_design": {
                    **shared,
                    "oscillator": oscillator,
                    "beginner_steps": [
                        "LiveのBassトラックでOperatorを開く（候補に音源がない場合はOperatorを挿入）。",
                        f"Oscillator Aの波形を {oscillator['oscillator_A_waveform']} にする。",
                        "フィルターとAmp EnvelopeをA/B/C共通設定に合わせる。",
                        "エフェクトを切り、再生音量を揃えて同じフレーズを試聴する。",
                        "選んだ案を記録した後、必要ならSaturatorを少量ずつ足して再試聴する。",
                    ],
                },
                "parameters": {"controlled_variable": "Operator Oscillator A waveform", "midi_changed": False},
                "notes": timbre_notes,
            }
            for variant_id, name, concept, beginner, detail, oscillator in options
        ]

    elif theme.theme_id == "spatial_and_dynamics":
        # Device state is not part of MidiCandidate. Keep the MIDI identical
        # and make the manual-only effect recipe explicit in every option.
        composer = DeterministicMIDIComposer(seed=seed)
        c_drums = MusicalConstraints(key=key, tempo=tempo, bars=bars, role="drums", grid="1/16")
        c_bass = MusicalConstraints(key=key, tempo=tempo, bars=bars, role="bass", grid="1/16", syncopation=base_sync)
        c_stab = MusicalConstraints(key=key, tempo=tempo, bars=bars, role="Stab", grid="1/16")
        raw_drums = composer.compose(MidiCompositionRequest(constraints=c_drums, operation="generate"))
        raw_bass = composer.compose(MidiCompositionRequest(constraints=c_bass, operation="generate"))
        raw_stab = composer.compose(MidiCompositionRequest(constraints=c_stab, operation="generate"))
        spatial_notes = {
            "Kick": [_note_to_dict(n) for n in raw_drums if n.pitch in (36, 35)],
            "Snare": [_note_to_dict(n) for n in raw_drums if n.pitch == 38],
            "Hats": [_note_to_dict(n) for n in raw_drums if n.pitch in (42, 46)],
            "Bass": [_note_to_dict(n) for n in raw_bass],
            "Stab": [_note_to_dict(n) for n in raw_stab],
        }
        variants = [
            {
                "variant_id": "A",
                "name": "ドライ・センター基準案",
                "concept": "比較用MIDIは共通です。Kick/Bassをセンター・ドライにし、空間系なしの音を基準にします。",
                "novice_summary": "低音を真ん中の手前に置き、余韻を足さない状態を基準にします。",
                "technical_summary": "全案のMIDIは同一です。Kick/Bassをセンター・ドライにする手動レシピで、候補生成時にLive機器設定は変更しません。",
                "what_to_listen": "LiveでKick/Bassをセンター・ドライにしたとき、低域の輪郭とStabの近さを確認してください。",
                "live_sound_design": {
                    "application_status": "manual_recommendation_only",
                    "midi_identical_across_variants": True,
                    "kick_bass": "center, dry, mono",
                    "stab": "dry, center",
                },
                "parameters": {"recipe": "dry_center", "effects_applied": False},
                "notes": spatial_notes,
            },
            {
                "variant_id": "B",
                "name": "Stabに短い空間を足す案",
                "concept": "共通MIDIに対し、Stabだけへ短いリバーブを手動設定する比較レシピです。",
                "novice_summary": "低音は手前に残し、コードの余韻だけを少し奥へ広げます。",
                "technical_summary": "全案のMIDIは同一です。StabにDecay 1.2秒、Pre-delay 20ms、Wet 12%、Low-cut 250Hzを手動設定します。",
                "what_to_listen": "コードの奥行きが増し、Kick/Bassの輪郭と低域の明瞭さが保たれるか聴いてください。",
                "live_sound_design": {
                    "application_status": "manual_recommendation_only",
                    "midi_identical_across_variants": True,
                    "kick_bass": "center, dry, mono",
                    "stab_reverb": {"decay_seconds": 1.2, "predelay_ms": 20, "wet_percent": 12, "low_cut_hz": 250},
                },
                "parameters": {"recipe": "stab_short_room", "effects_applied": False},
                "notes": spatial_notes,
            },
            {
                "variant_id": "C",
                "name": "空間＋キック連動ダッキング案",
                "concept": "B案のStabリバーブに、キック連動の軽い音量変化を加える手動比較レシピです。",
                "novice_summary": "コードの余韻を広げつつ、キックが鳴る瞬間だけコードを少し引かせます。",
                "technical_summary": "全案のMIDIは同一です。B案のリバーブに加え、StabへKick入力のCompressorをRatio 3:1、Attack 2ms、Release 120msで設定し、Gain Reduction 2〜4dBを目安に手動調整します。",
                "what_to_listen": "Kickのアタックが明瞭になり、Stabのポンピングが目立ちすぎないか確認してください。",
                "live_sound_design": {
                    "application_status": "manual_recommendation_only",
                    "midi_identical_across_variants": True,
                    "kick_bass": "center, dry, mono",
                    "stab_reverb": {"decay_seconds": 1.2, "predelay_ms": 20, "wet_percent": 12, "low_cut_hz": 250},
                    "stab_sidechain": {"source": "Kick", "ratio": "3:1", "attack_ms": 2, "release_ms": 120, "target_gain_reduction_db": "2-4"},
                },
                "parameters": {"recipe": "stab_space_and_sidechain", "effects_applied": False},
                "notes": spatial_notes,
            },
        ]

    else:
        # ---------------------------------------------------------------------
        # Fallback / General Training Variation (Composition & Sound Design)
        # ---------------------------------------------------------------------
        composer = DeterministicMIDIComposer(seed=seed)
        c_kick = MusicalConstraints(key=key, tempo=tempo, bars=bars, role="drums", grid="1/16")
        c_bass = MusicalConstraints(key=key, tempo=tempo, bars=bars, role="bass", grid="1/16", syncopation=base_sync)
        kick_n = [_note_to_dict(n) for n in composer.compose(MidiCompositionRequest(constraints=c_kick, operation="generate")) if n.pitch in (36, 35)]
        bass_n = [_note_to_dict(n) for n in composer.compose(MidiCompositionRequest(constraints=c_bass, operation="generate"))]

        variants = [
            {
                "variant_id": "A",
                "name": "ストレート・基準案",
                "concept": f"テーマ「{theme.title}」の基礎形。基本に忠実でシンプルな構成。",
                "novice_summary": "一番シンプルでストレートな形です。基準として聴いてみてください。",
                "technical_summary": "装飾やテンションを抑え、トニック音中心の堅実なボイシング・配置。",
                "what_to_listen": "基本の形を耳に焼き付け、B案・C案との違いを比べる物差しにしてください。",
                "live_sound_design": {"eq": "Flat Balanced", "reverb": "Dry", "comp": "Mild"},
                "parameters": {"variation": "standard", "tempo": tempo},
                "notes": {"Kick": kick_n, "Bass": bass_n},
            },
            {
                "variant_id": "B",
                "name": "コントラスト強調・ダイナミック案",
                "concept": f"テーマ「{theme.title}」の効果をあえて強調し、特徴を際立たせた案。",
                "novice_summary": "違いがハッキリ分かるように、効果を少し強めにかけています。",
                "technical_summary": "休符やアクセントのコントラストを2倍に拡張し、ダイナミクスを強調。",
                "what_to_listen": "静かな部分と盛り上がる部分の差が、感情にどう響くか注目してください。",
                "live_sound_design": {"eq": "Sculpted Mid Scoop", "reverb": "Deep Hall on Stabs", "comp": "Punchy"},
                "parameters": {"variation": "dynamic", "tempo": tempo},
                "notes": {"Kick": kick_n, "Bass": bass_n},
            },
            {
                "variant_id": "C",
                "name": "洗練・プログレッシブ案",
                "concept": f"テーマ「{theme.title}」を現代的なクラブトラック仕様に応用した発展案。",
                "novice_summary": "現代のクラブで流れているような、オシャレで洗練された鳴り方です。",
                "technical_summary": "ポリリズムとサイドチェイン、フィルターオートメーションを織り交ぜた複合構成。",
                "what_to_listen": "さりげない装飾音が曲全体のクオリティをどれだけ引き上げているかを聴いてください。",
                "live_sound_design": {"eq": "High Shelf Air & Sub Cut", "reverb": "Filtered Space", "comp": "Sidechain Ducker"},
                "parameters": {"variation": "progressive", "tempo": tempo},
                "notes": {"Kick": kick_n, "Bass": bass_n},
            },
        ]

    learning_profile = learning_profile or {}
    # First rollout is intentionally limited to the kick/bass theme because
    # that path has controlled, deterministic A/B/C phrase generation.
    learning_active = bool(
        learning_profile.get("active") and theme.theme_id == "kick_bass_space"
    )
    if theme.theme_id == "kick_bass_space":
        preferred = learning_profile.get("preferred_variant")
        for variant in variants:
            if learning_active and variant["variant_id"] == preferred:
                variant["learning_adjustment"] = {
                    "observations": learning_profile.get("observations", 0),
                    "source": "explicit_human_ab_choice",
                    "notice": "過去の選択傾向を生成シードに反映しています。YouTubeメモ自体は文脈表示のみです。",
                }
            if sensory_adjustment:
                variant["sensory_adjustment"] = sensory_adjustment
    return {
        "ok": True,
        "theme": asdict(theme),
        "source_reference": {
            "card_id": card.card_id,
            "title": card.user_title,
            "tempo": tempo,
            "tempo_source": tempo_source,
            "key": key,
            "features": reference_profile,
        },
        "variants": variants,
        "learning": {
            "active": learning_active,
            "observations": learning_profile.get("observations", 0),
            "required_observations": 5,
            "scope_type": learning_profile.get("scope_type", "theme"),
            "preferred_variant": learning_profile.get("preferred_variant") if learning_active else None,
            "sensory_profile": learning_profile.get("sensory_profile", {"active": False}),
            "sensory_adjustment": sensory_adjustment,
            "youtube_note_examples": learning_profile.get("youtube_note_examples", []),
            "message": "過去の選択傾向を反映しました。" if learning_active else "学習例5件に達するまでは基準条件です。",
        },
    }


def _normalized_key(value: Any) -> str | None:
    text = str(value or "").strip().replace("♯", "#").replace("♭", "b")
    match = re.fullmatch(r"([A-Ga-g])([#b]?)(?:\s*(minor|min|m|major|maj))?", text, re.IGNORECASE)
    if not match:
        return None
    root, accidental, mode = match.groups()
    flat_to_sharp = {"Db": "C#", "Eb": "D#", "Gb": "F#", "Ab": "G#", "Bb": "A#"}
    root = root.upper() + accidental
    root = flat_to_sharp.get(root, root)
    minor = bool(mode and mode.lower() in {"minor", "min", "m"})
    return root + ("m" if minor else "")


def resolve_reference_key(card: AnalysisCard, override: str | None = None) -> str:
    """Prefer a deliberate override, then a sufficiently confident detected key."""
    if override:
        return _normalized_key(override) or "D#m"
    rules = card.generation_rules or {}
    explicit = _normalized_key(rules.get("key"))
    if explicit:
        return explicit
    harmonic = card.harmonic_analysis or {}
    confidence = float(harmonic.get("confidence", 0.0) or 0.0)
    if confidence >= 0.6:
        return _normalized_key(harmonic.get("key") or rules.get("recommended_key")) or "D#m"
    return "D#m"


def reference_generation_profile(card: AnalysisCard, key: str | None = None) -> dict[str, Any]:
    """Expose only abstract, confidence-tagged targets to the MIDI generator."""
    rules = card.generation_rules or {}
    bass = card.bass_groove or {}
    kick = card.kick_pattern or {}
    harmonic = card.harmonic_analysis or {}
    tempo_info = card.bpm or {}
    tempo, tempo_source = resolve_reference_tempo(card)
    detected_key = _normalized_key(harmonic.get("key") or rules.get("recommended_key"))
    selected_key = key or resolve_reference_key(card)
    key_confidence = float(harmonic.get("confidence", 0.0) or 0.0)
    if rules.get("key"):
        key_source = "manual_override"
    elif key_confidence >= 0.6 and detected_key:
        key_source = "reference_estimate"
    elif detected_key and detected_key == selected_key and key_confidence >= 0.6:
        key_source = "reference_estimate"
    else:
        key_source = "fallback_unconfirmed"
    return {
        "tempo_bpm": tempo,
        "tempo_source": tempo_source,
        "tempo_confidence": float(tempo_info.get("confidence", 0.0) or 0.0),
        "key": key or resolve_reference_key(card),
        "key_source": key_source,
        "key_confidence": key_confidence,
        "kick_style": rules.get("kick_style", "four_on_the_floor"),
        "kick_density_per_bar": float(rules.get("kick_density_per_bar") or kick.get("hits_per_bar") or 4.0),
        "bass_density_per_bar": float(rules.get("bass_density_value") or bass.get("density_notes_per_bar") or 6.0),
        "bass_kick_separation_ratio": float(rules.get("bass_syncopation", bass.get("syncopation_ratio", 0.5)) or 0.0),
        "bass_rest_ratio": float(rules.get("bass_rest_ratio", bass.get("rest_ratio", 0.35)) or 0.0),
        "dynamic_profile": rules.get("dynamic_profile", (card.energy_dynamics or {}).get("trend", "stable")),
        "kick_confidence": float(kick.get("confidence", 0.0) or 0.0),
        "bass_confidence": float(bass.get("confidence", 0.0) or 0.0),
    }


def resolve_reference_tempo(card: AnalysisCard) -> tuple[int, str]:
    """Use analyzed tempo only when confidence is adequate; mark fallback clearly."""
    bpm = card.bpm or {}
    value = float(bpm.get("value") or card.generation_rules.get("bpm_target") or 124.0)
    confidence = float(bpm.get("confidence", 0.0) or 0.0)
    if 40 <= value <= 240 and (confidence >= 0.55 or bpm.get("method") == "user_specified_hint"):
        return round(value), "reference_estimate"
    return 124, "fallback_unconfirmed"


def _bounded_density(value: Any, fallback: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = fallback
    return max(1.0, min(16.0, number))


def promote_training_variant_to_candidate(
    card: AnalysisCard,
    theme: TrainingTheme,
    variant_id: str,
    bars: int = 64,
    key: str | None = None,
    learning_profile: dict[str, Any] | None = None,
) -> MidiCandidate:
    """Promote a chosen training variation to a full 64-bar, 7-part Studio Candidate."""
    if variant_id not in {"A", "B", "C"}:
        raise ValueError("variant_id must be A, B or C")
    # Leverage existing promote_variant_to_candidate logic with metadata tagging
    key = resolve_reference_key(card, key)
    candidate = promote_variant_to_candidate(
        card=card,
        variant_id=variant_id,
        bars=bars,
        key=key,
    )
    if theme.theme_id in {
        "kick_bass_space",
        "swing_microtiming",
        "motif_repetition_variation",
        "call_and_response",
        "velocity_accents",
        "tension_release",
        "spatial_and_dynamics",
        "bass_timbre",
    }:
        # Preserve the exact trained part when it becomes a Studio candidate;
        # otherwise promotion would silently replace it with an unrelated
        # AnalysisCard groove variant.
        selected_variant_id = variant_id
        selected = next(
            variant
            for variant in generate_training_variants(
                theme=theme,
                card=card,
                key=key,
                bars=bars,
                seed=42,
                learning_profile=learning_profile,
                reference_conditioned=True,
            )["variants"]
            if variant["variant_id"] == selected_variant_id
        )
        source_notes_by_part = selected["notes"]
        # Rebuild each selected part once per arrangement section. This keeps
        # the learned notes even when the base candidate omitted an optional
        # part, and avoids duplicate Lead clips when the base already had one.
        replaced_clips = [
            clip for clip in candidate.clips if clip.part not in source_notes_by_part
        ]
        for part, source_notes in source_notes_by_part.items():
            for section in candidate.brief.sections:
                clip_start = (section.start_bar - 1) * 4.0
                clip_end = clip_start + section.length_bars * 4.0
                local_notes = tuple(
                    MidiNote(
                        pitch=int(note["pitch"]),
                        start_beats=round(float(note["start_beats"]) - clip_start, 3),
                        duration_beats=float(note["duration_beats"]),
                        velocity=int(note["velocity"]),
                    )
                    for note in source_notes
                    if clip_start <= float(note["start_beats"]) < clip_end
                )
                replaced_clips.append(
                    CandidateClip(
                        part=part,
                        section_name=section.name,
                        start_bar=section.start_bar,
                        length_bars=section.length_bars,
                        notes=local_notes,
                    )
                )
        replaced_clips.sort(key=lambda clip: (PART_ORDER.index(clip.part), clip.start_bar))
        candidate = MidiCandidate(
            candidate_id=candidate.candidate_id,
            seed=candidate.seed,
            brief=candidate.brief,
            clips=tuple(replaced_clips),
            parent_candidate_id=candidate.parent_candidate_id,
        )
    # Enrich the production brief with learning context
    orig_text = candidate.brief.original_text
    training_tag = f"[Training:{theme.theme_id}:{variant_id}] {theme.title}"
    enriched_brief = ProductionBrief(
        original_text=f"{training_tag} | {orig_text}",
        tempo=candidate.brief.tempo,
        key=candidate.brief.key,
        bars=candidate.brief.bars,
        genre=candidate.brief.genre,
        mood=SourcedValue(value=f"Learned via {theme.title} (Variant {variant_id})", source=SOURCE_AI),
        hats_first_half=candidate.brief.hats_first_half,
        hats_second_half=candidate.brief.hats_second_half,
        bass_register=candidate.brief.bass_register,
        note_density=candidate.brief.note_density,
        drop_start_bar=candidate.brief.drop_start_bar,
        sections=candidate.brief.sections,
    )
    enriched_candidate = MidiCandidate(
        candidate_id=candidate.candidate_id,
        seed=candidate.seed,
        brief=enriched_brief,
        clips=candidate.clips,
    )
    # Save enriched candidate
    store_dir = Path(candidate_store_dir())
    store_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    (store_dir / f"{candidate.candidate_id}.json").write_text(
        json.dumps(enriched_candidate.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return enriched_candidate


def _note_to_dict(n: Any) -> dict[str, Any]:
    pitch = getattr(n, "pitch", 60)
    start = getattr(n, "start", getattr(n, "start_beats", 0.0))
    dur = getattr(n, "duration", getattr(n, "duration_beats", 0.25))
    vel = getattr(n, "velocity", 100)
    return {
        "pitch": int(pitch),
        "start_beats": round(float(start), 3),
        "duration_beats": round(float(dur), 3),
        "velocity": int(vel),
    }


def _repeat_bass_motif(
    motif: list[dict[str, Any]],
    bars: int,
    *,
    vary_phrase_ending: bool = False,
    octave_answer: bool = False,
) -> list[dict[str, Any]]:
    """Repeat a one-bar motif, optionally changing only each phrase's final note."""
    notes: list[dict[str, Any]] = []
    for bar in range(bars):
        bar_notes = [
            {**note, "start_beats": round(float(note["start_beats"]) + bar * 4.0, 3)}
            for note in motif
        ]
        is_phrase_end = (bar + 1) % 4 == 0 or bar == bars - 1
        if vary_phrase_ending and is_phrase_end and bar_notes:
            last = max(bar_notes, key=lambda note: note["start_beats"])
            within_bar = float(last["start_beats"]) - bar * 4.0
            delta = -0.25 if within_bar >= 3.5 else 0.25
            last["start_beats"] = round(bar * 4.0 + min(3.5, max(3.0, within_bar + delta)), 3)
            if octave_answer:
                pitch = int(last["pitch"])
                last["pitch"] = pitch + 12 if pitch <= 115 else pitch - 12
        notes.extend(bar_notes)
    return sorted(notes, key=lambda note: (note["start_beats"], note["pitch"]))


def _repeat_melody_motif(
    motif: list[dict[str, Any]],
    bars: int,
    *,
    tonic_pitch: int | None = None,
    call_and_response: bool = False,
) -> list[dict[str, Any]]:
    """Repeat one melodic bar, optionally shaping paired question and answer endings."""
    notes: list[dict[str, Any]] = []
    for bar in range(bars):
        bar_notes = [
            {**note, "start_beats": round(float(note["start_beats"]) + bar * 4.0, 3)}
            for note in motif
        ]
        if call_and_response and bar_notes:
            last = max(bar_notes, key=lambda note: note["start_beats"])
            if bar % 2 == 0:
                pitch = int(last["pitch"])
                last["pitch"] = pitch + 12 if pitch <= 115 else pitch - 12
            elif tonic_pitch is not None:
                last["pitch"] = tonic_pitch
        notes.extend(bar_notes)
    return sorted(notes, key=lambda note: (note["start_beats"], note["pitch"]))
