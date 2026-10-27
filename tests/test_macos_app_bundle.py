"""The macOS release folder must carry the real Live device beside the app."""

from pathlib import Path

from scripts.build_macos_app import LIVE_DEVICE_FILES, ROOT, copy_live_device_bundle


def test_copy_live_device_bundle_keeps_installer_layout(tmp_path: Path) -> None:
    copy_live_device_bundle(tmp_path)

    installer = tmp_path / "scripts" / "Liveデバイスをインストール.command"
    assert installer.read_bytes() == (
        ROOT / "scripts" / "Liveデバイスをインストール.command"
    ).read_bytes()
    assert installer.stat().st_mode & 0o111
    for name in LIVE_DEVICE_FILES:
        assert (tmp_path / "maxforlive" / name).read_bytes() == (
            ROOT / "maxforlive" / name
        ).read_bytes()
