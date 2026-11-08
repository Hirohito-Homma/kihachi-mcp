"""The Finder installer must preserve user files and be safe to rerun."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "Liveデバイスをインストール.command"
NAMES = ("KIHACHI Live Device.amxd", "kihachi.device.js", "kihachi.bridge.js")


def _run(library: Path, *, answer: str = "", yes: bool = False) -> str:
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash is unavailable")
    command = [bash, str(SCRIPT), "--library", str(library)]
    if yes:
        command.append("--yes")
    result = subprocess.run(
        command, input=answer, text=True, capture_output=True, check=True, timeout=15
    )
    return result.stdout


def test_installer_backs_up_changed_files_and_second_run_is_idempotent(
    tmp_path: Path,
) -> None:
    library = tmp_path / "User Library"
    destination = library / "Presets" / "Audio Effects" / "Max Audio Effect"
    destination.mkdir(parents=True)
    old_device = destination / "kihachi.device.js"
    old_device.write_text("user-owned old device", encoding="utf-8")

    _run(library, yes=True)
    assert all(
        (destination / name).read_bytes() == (ROOT / "maxforlive" / name).read_bytes()
        for name in NAMES
    )
    backups = list((library / ".kihachi-backups").glob("*/kihachi.device.js"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == "user-owned old device"

    output = _run(library, yes=True)
    assert output.count("変更なし") == 3
    assert list((library / ".kihachi-backups").glob("*/kihachi.device.js")) == backups


def test_installer_respects_a_declined_confirmation(tmp_path: Path) -> None:
    library = tmp_path / "User Library"
    library.mkdir()

    assert "変更せず終了しました" in _run(library, answer="n\n")
    assert not (library / "Presets").exists()


def test_installer_refuses_symlink_without_writing(tmp_path: Path) -> None:
    library = tmp_path / "User Library"
    destination = library / "Presets" / "Audio Effects" / "Max Audio Effect"
    destination.mkdir(parents=True)
    protected = tmp_path / "protected.js"
    protected.write_text("keep me", encoding="utf-8")
    (destination / "kihachi.device.js").symlink_to(protected)

    result = subprocess.run(
        ["bash", str(SCRIPT), "--library", str(library), "--yes"],
        text=True,
        capture_output=True,
        check=False,
        timeout=15,
    )
    assert result.returncode == 1
    assert "通常のファイルではない" in result.stderr
    assert protected.read_text(encoding="utf-8") == "keep me"
    assert not (destination / "KIHACHI Live Device.amxd").exists()


def test_finder_folder_selection_handles_spaces(tmp_path: Path) -> None:
    library = tmp_path / "My Ableton User Library"
    library.mkdir()
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    osascript = fake_bin / "osascript"
    osascript.write_text('#!/bin/sh\nprintf "%s\\n" "$MOCK_LIBRARY"\n', encoding="utf-8")
    osascript.chmod(0o755)
    environment = os.environ.copy()
    environment["PATH"] = f"{fake_bin}:{environment['PATH']}"
    environment["MOCK_LIBRARY"] = str(library)

    result = subprocess.run(
        ["bash", str(SCRIPT)],
        input="y\n",
        text=True,
        capture_output=True,
        check=True,
        timeout=15,
        env=environment,
    )
    assert str(library / "Presets/Audio Effects/Max Audio Effect") in result.stdout
    for name in NAMES:
        assert (library / "Presets/Audio Effects/Max Audio Effect" / name).read_bytes() == (
            ROOT / "maxforlive" / name
        ).read_bytes()


def test_cancelled_finder_selection_makes_no_changes(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    osascript = fake_bin / "osascript"
    osascript.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    osascript.chmod(0o755)
    environment = os.environ.copy()
    environment["PATH"] = f"{fake_bin}:{environment['PATH']}"

    result = subprocess.run(
        ["bash", str(SCRIPT)],
        input="",
        text=True,
        capture_output=True,
        check=False,
        timeout=15,
        env=environment,
    )
    assert result.returncode == 1
    assert "フォルダ選択を取り消しました" in result.stdout
    assert not (tmp_path / "Presets").exists()
