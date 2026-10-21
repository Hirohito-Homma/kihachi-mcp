"""The distributable ZIP includes the app and the exact Live device assets."""

import shutil
import subprocess
from pathlib import Path

import pytest
from scripts.build_macos_app import LIVE_DEVICE_FILES, ROOT, copy_live_device_bundle


def test_release_zip_contents_and_no_overwrite(tmp_path: Path) -> None:
    if shutil.which("ditto") is None:
        pytest.skip("macOS ditto is unavailable")
    app = tmp_path / "KIHACHI MUSIC AI.app"
    (app / "Contents").mkdir(parents=True)
    (app / "Contents" / "Info.plist").write_text("test app", encoding="utf-8")
    copy_live_device_bundle(tmp_path)
    output = tmp_path / "release.zip"
    command = ["bash", str(ROOT / "scripts/package_macos_release.sh"), str(app), str(output)]

    subprocess.run(command, check=True, capture_output=True, text=True)
    extracted = tmp_path / "extracted"
    subprocess.run(["ditto", "-x", "-k", str(output), str(extracted)], check=True)
    assert (extracted / "KIHACHI MUSIC AI.app/Contents/Info.plist").read_bytes() == b"test app"
    assert (extracted / "scripts/Liveデバイスをインストール.command").read_bytes() == (
        ROOT / "scripts/Liveデバイスをインストール.command"
    ).read_bytes()
    for name in LIVE_DEVICE_FILES:
        assert (extracted / "maxforlive" / name).read_bytes() == (
            ROOT / "maxforlive" / name
        ).read_bytes()

    result = subprocess.run(command, check=False, capture_output=True, text=True)
    assert result.returncode == 1
    assert "上書きしません" in result.stderr
