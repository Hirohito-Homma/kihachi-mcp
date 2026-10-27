#!/usr/bin/env python3
"""
KIHACHI MUSIC AI Bundle Packager (バンドル作成プログラム)

KIHACHI MUSIC AIの実行・制作に必要なすべてのファイル
（Pythonコアパッケージ、Max for Liveデバイス、起動スクリプト、設定、ドキュメント、サンプル音源）
を整合性検証した上でひとまとめにし、配布・移行・バックアップ用のバンドル（ZIP / フォルダ / tar.gz）
を自動生成するプログラムです。

使用方法:
    python3 scripts/bundle_kihachi.py [オプション]

主なオプション:
    --profile {standard,runtime,full}  バンドル構成 (既定: standard)
    --format {zip,tar.gz,dir}          出力形式 (既定: zip)
    --output PATH                      出力先パス (ファイル名またはフォルダ)
    --check-only                       整合性検査のみ実行して終了
    --dry-run                          ファイルを作成せず含めるファイル一覧を表示
    --include-tests                    テストコードを含める
    --include-companions               AbletonGPT等のコンパニオンを含める
    --verbose                          全ファイルの詳細ログを表示
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tarfile
import tempfile
import zipfile

# 必須ファイルの検証定義（これらが欠けている場合は警告またはエラー）
CRITICAL_REQUIREMENTS = [
    # 設定・構成
    ("pyproject.toml", "プロジェクト定義 (pyproject.toml)"),
    ("uv.lock", "依存関係ロック (uv.lock)"),
    ("README.md", "README ドキュメント"),
    # スクリプト
    ("scripts/setup.sh", "セットアップスクリプト (setup.sh)"),
    ("scripts/find-uv.sh", "uv探索スクリプト (find-uv.sh)"),
    ("scripts/kihachi-studio.command", "Studio起動スクリプト (kihachi-studio.command)"),
    ("scripts/Liveデバイスをインストール.command", "Liveデバイスインストーラー (.command)"),
    ("scripts/インストール.command", "ワンクリックインストーラー (.command)"),
    # Max for Live
    ("maxforlive/KIHACHI Live Device.amxd", "Max for Liveデバイス (.amxd)"),
    ("maxforlive/kihachi.device.js", "Max for Live制御スクリプト (device.js)"),
    ("maxforlive/kihachi.bridge.js", "Max for Live通信ブリッジ (bridge.js)"),
    # Pythonコアモジュール
    ("src/kihachi_mcp/__init__.py", "Pythonパッケージルート (__init__.py)"),
    ("src/kihachi_mcp/cli.py", "CLIエントリポイント (cli.py)"),
    ("src/kihachi_mcp/studio/app.py", "Studioサーバー (app.py)"),
    ("src/kihachi_mcp/studio/static/index.html", "Studio制作画面UI (index.html)"),
    ("src/kihachi_mcp/studio/static/references.html", "参考音源画面UI (references.html)"),
    ("src/kihachi_mcp/resources/genre_database.json", "ジャンルデータベース (genre_database.json)"),
    ("src/kihachi_mcp/assets/drums/kihachi-kick-fat-tight.wav", "標準ドラムサンプル (wav)"),
]

# 除外ディレクトリ
EXCLUDE_DIRS = {
    ".git",
    ".github",
    ".venv",
    "venv",
    "env",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".cursor",
    ".vscode",
    ".idea",
    "node_modules",
    "scratch",
    "tmp",
    "temp",
    "dist",
    "bundles",
    ".DS_Store",
}

# 除外ファイル拡張子・ファイル名パターン
EXCLUDE_EXTENSIONS = {
    ".pyc",
    ".pyo",
    ".pyd",
    ".asd",  # Abletonキャッシュ
    ".tmp",
    ".log",
    ".bak",
}

EXCLUDE_FILENAMES = {
    ".DS_Store",
    "Thumbs.db",
    "package-lock.json",
    "sync_manual.py",  # 一時的な外部同期スクリプト
    "test_librosa.py", # 一時的なテストスクリプト
}


def compute_sha256(filepath: Path) -> str:
    """ファイルのSHA-256ハッシュを計算"""
    hasher = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def find_repository_root(start_path: Path | None = None) -> Path:
    """KIHACHI MUSIC AIのルートディレクトリを自動検出"""
    candidates = []
    if start_path:
        candidates.append(start_path.resolve())

    # 現在のスクリプトの位置からの推測
    script_dir = Path(__file__).resolve().parent
    candidates.append(script_dir.parent)  # scripts/ の親
    candidates.append(script_dir)

    # カレントワーキングディレクトリ
    cwd = Path.cwd().resolve()
    candidates.append(cwd)
    candidates.append(cwd / "kihachi-mcp-codex-work")
    candidates.append(cwd.parent / "kihachi-mcp-codex-work")

    for cand in candidates:
        if cand.is_dir() and (cand / "src" / "kihachi_mcp").is_dir() and (cand / "pyproject.toml").is_file():
            return cand

    raise FileNotFoundError(
        "KIHACHI MUSIC AIのルートディレクトリ（src/kihachi_mcp および pyproject.toml を含むフォルダ）"
        "を特定できませんでした。--source オプションで明示的に指定してください。"
    )


def verify_critical_files(repo_root: Path) -> tuple[bool, list[str]]:
    """必須ファイルの存在と整合性をチェック"""
    messages = []
    all_ok = True

    print("🔍 必須ファイルの整合性チェックを実行中...")

    for rel_path, desc in CRITICAL_REQUIREMENTS:
        file_path = repo_root / rel_path
        if not file_path.is_file():
            messages.append(f"❌ [欠落] {desc}: {rel_path}")
            all_ok = False
        else:
            # 特殊チェック: Max for Live デバイスのマジックヘッダー
            if rel_path.endswith(".amxd"):
                try:
                    magic = file_path.read_bytes()[:4]
                    if magic != b"ampf":
                        messages.append(f"⚠️ [異常] {desc}: マジックヘッダーが 'ampf' ではありません ({magic})")
                        all_ok = False
                    else:
                        messages.append(f"✅ [正常] {desc}")
                except Exception as e:
                    messages.append(f"❌ [読取エラー] {desc}: {e}")
                    all_ok = False
            else:
                messages.append(f"✅ [正常] {desc}")

    return all_ok, messages


def collect_bundle_files(
    repo_root: Path,
    profile: str = "standard",
    include_tests: bool = False,
    include_companions: bool = False,
    verbose: bool = False,
) -> list[tuple[Path, str]]:
    """
    バンドルに含めるファイル一覧を走査して収集。
    返り値: (絶対パス, バンドル内相対パス) のリスト
    """
    collected: list[tuple[Path, str]] = []

    # 1. ルート設定ファイル・ドキュメント
    root_files = [
        "pyproject.toml",
        "uv.lock",
        ".python-version",
        ".env.example",
        "README.md",
        "CHANGELOG.md",
        "KIHACHI_MUSIC_AI_COMPLETION_REPORT.md",
    ]
    for rf in root_files:
        src = repo_root / rf
        if src.is_file():
            collected.append((src, rf))

    # 2. 収集対象フォルダの定義
    folders_to_scan = [
        ("src", "src"),
        ("maxforlive", "maxforlive"),
        ("scripts", "scripts"),
    ]

    if profile in ("standard", "full"):
        folders_to_scan.append(("docs", "docs"))

    if include_tests or profile == "full":
        folders_to_scan.append(("tests", "tests"))

    for folder_rel, target_base in folders_to_scan:
        folder_path = repo_root / folder_rel
        if not folder_path.is_dir():
            continue

        for root, dirs, files in os.walk(folder_path):
            rel_from_repo = Path(root).relative_to(repo_root)

            # 除外ディレクトリをプルダウンして下層の走査を防ぐ
            dirs[:] = [
                d for d in dirs
                if d not in EXCLUDE_DIRS
                and not d.startswith(".")
                and not (d == "work" or d == "build")
            ]

            for file_name in sorted(files):
                if file_name in EXCLUDE_FILENAMES or file_name.startswith("._") or file_name.startswith(".~"):
                    continue

                ext = Path(file_name).suffix.lower()
                if ext in EXCLUDE_EXTENSIONS:
                    continue

                abs_file = Path(root) / file_name
                if abs_file.is_file():
                    arc_path = str(rel_from_repo / file_name)
                    collected.append((abs_file, arc_path))

    # 3. コンパニオン（AbletonGPT Remote Script 等）の同梱オプション
    if include_companions or profile == "full":
        # 隣接ディレクトリを探索
        workspace_root = repo_root.parent
        gpt_dir = workspace_root / "AbletonGPT0.2-kihachi-fingerprint-guard" / "ableton_remote_script" / "AbletonGPT"
        if gpt_dir.is_dir():
            for root, dirs, files in os.walk(gpt_dir):
                dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
                for file_name in files:
                    if file_name.endswith(".pyc") or file_name.startswith("."):
                        continue
                    abs_file = Path(root) / file_name
                    if abs_file.is_file():
                        rel = abs_file.relative_to(gpt_dir)
                        arc_path = f"companions/AbletonGPT/{rel}"
                        collected.append((abs_file, arc_path))

    # 重複排除とソート
    unique_collected = {}
    for src, arc in collected:
        unique_collected[arc] = src

    return sorted([(src, arc) for arc, src in unique_collected.items()], key=lambda x: x[1])


def generate_bundle_readme() -> str:
    """バンドル同梱用のスタートガイドを作成"""
    return """# KIHACHI MUSIC AI 配布バンドル

このフォルダは KIHACHI MUSIC AI の動作に必要なすべてのファイルがまとめられたバンドルパッケージです。

---

## 🚀 はじめ方（3ステップ）

### ステップ1: セットアップ
MacのFinderから `scripts/インストール.command` をダブルクリックして開きます。
（またはターミナルで `./scripts/setup.sh` を実行）
必要なPython依存関係が自動的にインストールされます。

### ステップ2: Ableton Live にデバイスを導入
Ableton Live を終了した状態で、Finderから `scripts/Liveデバイスをインストール.command` をダブルクリックします。
Live の User Library へ `KIHACHI Live Device` が自動配置されます。

### ステップ3: 制作画面（Studio）を起動
Finderから `scripts/kihachi-studio.command` をダブルクリックします。
ブラウザが自動的に開き、制作画面（http://127.0.0.1:8765/）が表示されます！

---

## 🎵 楽曲制作の流れ
1. **Ableton Live を起動**: 任意のオーディオトラックまたはMIDIトラックに「KIHACHI Live Device」を1つ配置します。
2. **接続確認**: 制作画面のヘッダーで `Live: READY`（緑色）になっていることを確認します。
3. **曲のイメージを入力**:
   例：「110 BPM、D# minor。Mutation Funk。Swing 54%。太いベースとタイトなドラム。」
4. **候補生成**: 「候補を生成」を押し、提案されたパートやコード進行、レビューを確認します。
5. **承認とLive反映**: 「この候補を承認」を押し、「Liveへ適用」を押します（1回だけ安全に送信されます）。
6. **読み戻し検証**: Liveからクリップとノートが正しく書き込まれたか自動検証されます。

---

## 📚 ドキュメント一覧
- `docs/QUICKSTART.md`: クイックスタートガイド
- `docs/ABLETON_SETUP.md`: Ableton Live 詳細連携ガイド
- `docs/STUDIO_GUIDE.md`: 制作画面（Studio）操作マニュアル
- `docs/ARCHITECTURE.md`: アーキテクチャと安全設計（14の安全規則）
- `docs/TROUBLESHOOTING.md`: よくある質問と対処法
"""


def package_bundle(
    repo_root: Path,
    output_path: Path,
    bundle_format: str,
    profile: str,
    files: list[tuple[Path, str]],
    verbose: bool = False,
) -> dict:
    """ファイルを指定形式でパッケージングし、マニフェストを生成"""
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # pyproject.tomlからバージョン取得
    version = "unknown"
    pyproject_file = repo_root / "pyproject.toml"
    if pyproject_file.is_file():
        for line in pyproject_file.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("version"):
                parts = line.split("=")
                if len(parts) == 2:
                    version = parts[1].strip().strip('"').strip("'")
                break

    # 一時ステージングディレクトリを作成
    with tempfile.TemporaryDirectory(prefix="kihachi_bundle_stage_") as stage_str:
        stage_dir = Path(stage_str)
        bundle_root = stage_dir / "KIHACHI_MUSIC_AI"
        bundle_root.mkdir(parents=True)

        manifest_entries = []
        total_bytes = 0

        print(f"📦 ファイルを配置中 ({len(files)} ファイル)...")
        for src, arc in files:
            dest = bundle_root / arc
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)

            file_size = src.stat().st_size
            total_bytes += file_size
            sha = compute_sha256(src)
            manifest_entries.append({
                "path": arc,
                "size": file_size,
                "sha256": sha,
            })
            if verbose:
                print(f"  + {arc} ({file_size:,} bytes)")

        # START_HERE.md の作成
        readme_path = bundle_root / "START_HERE.md"
        readme_content = generate_bundle_readme()
        readme_path.write_text(readme_content, encoding="utf-8")
        manifest_entries.append({
            "path": "START_HERE.md",
            "size": len(readme_content.encode("utf-8")),
            "sha256": compute_sha256(readme_path),
        })

        # MANIFEST.json の作成
        manifest_data = {
            "name": "KIHACHI MUSIC AI Bundle",
            "version": version,
            "profile": profile,
            "created_at": timestamp,
            "total_files": len(manifest_entries),
            "total_uncompressed_bytes": total_bytes,
            "source_repo": str(repo_root),
            "files": manifest_entries,
        }
        manifest_path = bundle_root / "MANIFEST.json"
        manifest_json_str = json.dumps(manifest_data, indent=2, ensure_ascii=False)
        manifest_path.write_text(manifest_json_str, encoding="utf-8")

        # 実行権限の付与 (scripts/*.command, scripts/*.sh)
        for cmd in bundle_root.glob("scripts/*.command"):
            cmd.chmod(0o755)
        for sh in bundle_root.glob("scripts/*.sh"):
            sh.chmod(0o755)

        # 出力先ディレクトリの確保
        output_path.parent.mkdir(parents=True, exist_ok=True)

        print(f"🚚 アーカイブ作成中: {bundle_format.upper()}...")
        if bundle_format == "zip":
            with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zipf:
                for root, _, arc_files in os.walk(bundle_root):
                    for f in arc_files:
                        abs_p = Path(root) / f
                        rel_p = abs_p.relative_to(stage_dir)
                        zipf.write(abs_p, str(rel_p))
        elif bundle_format == "tar.gz":
            with tarfile.open(output_path, "w:gz") as tar:
                tar.add(bundle_root, arcname="KIHACHI_MUSIC_AI")
        elif bundle_format == "dir":
            if output_path.exists():
                shutil.rmtree(output_path)
            shutil.copytree(bundle_root, output_path)

        out_size = output_path.stat().st_size if output_path.is_file() else sum(f.stat().st_size for f in output_path.rglob("*") if f.is_file())
        manifest_data["output_file"] = str(output_path)
        manifest_data["output_size_bytes"] = out_size
        return manifest_data


def main() -> int:
    parser = argparse.ArgumentParser(
        description="KIHACHI MUSIC AI に必要なファイルをひとまとめにするバンドル生成プログラム",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=None,
        help="対象リポジトリのルートパス（省略時は自動検出）",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        default=None,
        help="出力ファイルまたはディレクトリのパス（省略時は bundles/ フォルダ下に自動命名）",
    )
    parser.add_argument(
        "--profile",
        choices=["standard", "runtime", "full"],
        default="standard",
        help="バンドルプロファイル: standard(通常), runtime(最小実行構成), full(テスト・外部連携含む)",
    )
    parser.add_argument(
        "--format",
        choices=["zip", "tar.gz", "dir"],
        default="zip",
        help="出力フォーマット (既定: zip)",
    )
    parser.add_argument(
        "--include-tests",
        action="store_true",
        help="テストファイル (tests/) を含める",
    )
    parser.add_argument(
        "--include-companions",
        action="store_true",
        help="AbletonGPTなどのコンパニオンツールを含める",
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="必須ファイルの整合性検査のみ行い、パッケージングは行わない",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="実際にファイルをコピー・圧縮せず、対象ファイル一覧と容量を試算表示する",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="詳細なファイル配置ログを出力する",
    )

    args = parser.parse_args()

    print("=" * 64)
    print(" 🎵 KIHACHI MUSIC AI バンドル作成ツール")
    print("=" * 64)

    try:
        repo_root = find_repository_root(args.source)
        print(f"📁 リポジトリルート: {repo_root}")
    except FileNotFoundError as e:
        print(f"❌ エラー: {e}")
        return 1

    # 1. 必須ファイルの検証
    all_ok, check_messages = verify_critical_files(repo_root)
    for msg in check_messages:
        print(f"  {msg}")

    if not all_ok:
        print("\n⚠️ 必須ファイルに不足または異常が見つかりました。")
        if args.check_only:
            return 1
        confirm = input("⚠️ このままバンドル作成を続行しますか？ [y/N]: ").strip().lower()
        if confirm not in ("y", "yes"):
            print("処理を中断しました。")
            return 1
    else:
        print("\n✨ すべての必須ファイルが正常に揃っています！")

    if args.check_only:
        return 0

    # 2. バンドルに含めるファイルの収集
    print(f"\n📂 ファイル収集を開始 (Profile: {args.profile})...")
    files_to_bundle = collect_bundle_files(
        repo_root=repo_root,
        profile=args.profile,
        include_tests=args.include_tests,
        include_companions=args.include_companions,
        verbose=args.verbose,
    )

    total_uncompressed = sum(src.stat().st_size for src, _ in files_to_bundle)
    print(f"📊 収集完了: {len(files_to_bundle)} ファイル (未圧縮合計: {total_uncompressed / (1024*1024):.2f} MB)")

    if args.dry_run:
        print("\n--- [DRY RUN] 収録予定ファイル一覧 ---")
        for src, arc in files_to_bundle:
            print(f"  {arc:55} ({src.stat().st_size:>8,} B)")
        print("-" * 64)
        print("Dry run 完了（ファイルは作成されませんでした）。")
        return 0

    # 3. 出力先パスの決定
    now_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    if args.output:
        output_path = args.output.resolve()
    else:
        bundles_dir = repo_root / "bundles"
        if args.format == "zip":
            output_path = bundles_dir / f"kihachi_music_ai_bundle_{now_str}.zip"
        elif args.format == "tar.gz":
            output_path = bundles_dir / f"kihachi_music_ai_bundle_{now_str}.tar.gz"
        else:
            output_path = bundles_dir / f"kihachi_music_ai_bundle_{now_str}"

    print(f"\n🚀 バンドル出力先: {output_path}")

    # 4. パッケージング実行
    manifest = package_bundle(
        repo_root=repo_root,
        output_path=output_path,
        bundle_format=args.format,
        profile=args.profile,
        files=files_to_bundle,
        verbose=args.verbose,
    )

    out_size_mb = manifest["output_size_bytes"] / (1024 * 1024)
    print("\n" + "=" * 64)
    print(" 🎉 KIHACHI MUSIC AI バンドル作成が完了しました！")
    print("=" * 64)
    print(f"  ・出力ファイル: {output_path}")
    print(f"  ・ファイル数:   {manifest['total_files']} ファイル")
    print(f"  ・パッケージサイズ: {out_size_mb:.2f} MB")
    print(f"  ・プロファイル: {manifest['profile']}")
    print(f"  ・バージョン:   {manifest['version']}")
    print("=" * 64)
    print("💡 バンドル内には 'START_HERE.md' と 'MANIFEST.json' が同梱されています。")
    print("   配布先では 'scripts/インストール.command' を実行するだけで即座に使い始められます。")

    return 0


if __name__ == "__main__":
    sys.exit(main())
