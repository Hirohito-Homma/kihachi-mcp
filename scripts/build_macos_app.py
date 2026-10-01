"""Build a self-contained macOS .app; optionally sign with Developer ID."""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "src/kihachi_mcp"
NAME = "KIHACHI MUSIC AI"
BUNDLE_ID = "com.kihachi.musicai"


def bundled_data() -> list[Path]:
    patterns = (
        "resources/genres/*.yaml",
        "resources/genre_database.json",
        "studio/static/*.html",
        "assets/drums/*.wav",
        "knowledge/live_device_parameters.json",
    )
    result = sorted({path for pattern in patterns for path in PACKAGE.glob(pattern)})
    if len(result) < 15:
        raise RuntimeError("Expected Studio pages, genre data, and bundled drum sounds")
    return result


def build(identity: str | None = None) -> Path:
    if sys.platform != "darwin":
        raise RuntimeError("A macOS .app must be built on macOS")
    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    arch = platform.machine()
    build_kind = "-developer-id" if identity else ""
    destination = ROOT / "dist" / "macos" / f"{version}-{arch}{build_kind}"
    app = destination / f"{NAME}.app"
    if destination.exists():
        raise FileExistsError(f"Build output already exists: {destination}")
    with tempfile.TemporaryDirectory(prefix="kihachi-pyinstaller-") as work:
        built_app = Path(work) / "dist" / f"{NAME}.app"
        command = [
            sys.executable, "-m", "PyInstaller", "--onedir", "--windowed",
            "--log-level", "WARN",
            "--name", NAME, "--osx-bundle-identifier", BUNDLE_ID,
            "--distpath", str(Path(work) / "dist"), "--workpath", str(Path(work) / "work"),
            "--specpath", str(Path(work) / "spec"),
            "--paths", str(ROOT / "src"),
        ]
        for path in bundled_data():
            target = Path("kihachi_mcp") / path.relative_to(PACKAGE).parent
            command.extend(("--add-data", f"{path}:{target}"))
        if identity:
            command.extend(("--codesign-identity", identity))
        command.append(str(ROOT / "scripts/mac_app_entry.py"))
        environment = os.environ.copy()
        environment["PYINSTALLER_CONFIG_DIR"] = str(Path(work) / "config")
        subprocess.run(command, cwd=ROOT, env=environment, check=True)
        if not built_app.is_dir():
            raise RuntimeError("PyInstaller did not create the .app bundle")
        subprocess.run(["codesign", "--verify", "--deep", "--strict", str(built_app)], check=True)
        if destination.exists():
            raise FileExistsError(f"Build output appeared during build: {destination}")
        destination.mkdir(parents=True)
        shutil.move(str(built_app), str(app))
    print(f"Built {app}")
    print("Developer ID signed" if identity else "Ad-hoc signed: local evaluation only")
    return app


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--signing-identity", help="Developer ID Application identity from Keychain"
    )
    args = parser.parse_args()
    build(args.signing_identity)


if __name__ == "__main__":
    main()
