import json
import shutil
import subprocess

import pytest

from kihachi_mcp.services.voice_io import (
    VoiceError,
    correct_voice_terms,
    synthesize_japanese,
    transcribe_japanese,
    transcribe_japanese_options,
    voice_authorize,
    voice_status,
)


def test_user_confirmed_voice_term_correction_keeps_other_words():
    assert correct_voice_terms("ミューテーションパンクでキックを短く") == (
        "Mutation Funkでキックを短く"
    )
    assert correct_voice_terms("パンクでキックを短く") == "パンクでキックを短く"


@pytest.mark.parametrize(
    "permission", ["not_determined", "authorized", "denied", "restricted"]
)
def test_status_does_not_request_permission_or_recognize_audio(
    tmp_path, monkeypatch, permission
):
    import json

    from kihachi_mcp.services import voice_io

    helper = tmp_path / "helper"
    helper.touch()
    monkeypatch.setattr(voice_io, "asr_executable", lambda: helper)
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        assert command[:4] == ["/usr/bin/open", "-W", "-n", "--stdout"]
        from pathlib import Path

        Path(command[4]).write_text(
            json.dumps(
                {
                    "authorization": permission,
                    "on_device": True,
                    "available": True,
                    "usage_description_present": True,
                    "authorize_supported": True,
                }
            )
        )
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(voice_io.subprocess, "run", run)
    result = voice_status()
    assert len(calls) == 1
    assert calls[0][-2:] == ["--args", "--status"]
    assert result["authorization"] == permission
    assert result["cloud_fallback"] is False
    assert result["authorize_supported"] is True


@pytest.mark.parametrize("permission", ["authorized", "denied"])
def test_authorization_uses_local_app_and_removes_result(tmp_path, monkeypatch, permission):
    from pathlib import Path

    from kihachi_mcp.services import voice_io

    helper = tmp_path / "OnDeviceASR.app" / "Contents" / "MacOS" / "OnDeviceASR"
    helper.parent.mkdir(parents=True)
    helper.touch()
    monkeypatch.setattr(voice_io, "asr_executable", lambda: helper)
    monkeypatch.setattr(
        voice_io,
        "voice_status",
        lambda: {"asr_installed": True, "authorize_supported": True, "on_device": True},
    )
    outputs = []

    def run(command, **kwargs):
        assert command[:3] == ["/usr/bin/open", "-W", "-n"]
        assert command[-3] == "--args"
        assert command[-2] == "--authorize"
        output = Path(command[-1])
        outputs.append(output)
        output.write_text(json.dumps({"ok": permission == "authorized", "authorization": permission}))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(voice_io.subprocess, "run", run)
    result = voice_authorize()
    assert result["ok"] is (permission == "authorized")
    assert result["authorization"] == permission
    assert all(not output.exists() for output in outputs)
    assert not voice_io._ASR_LOCK.locked()


def test_failed_status_is_unknown_not_ready(tmp_path, monkeypatch):
    from kihachi_mcp.services import voice_io

    helper = tmp_path / "helper"
    helper.touch()
    monkeypatch.setattr(voice_io, "asr_executable", lambda: helper)

    def fail(*args, **kwargs):
        raise subprocess.TimeoutExpired("helper", 10)

    monkeypatch.setattr(voice_io.subprocess, "run", fail)
    result = voice_status()
    assert result["authorization"] == "unknown"
    assert result["on_device"] is None


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (None, None),
        ("speech_permission_required", "許可"),
        ("recognition_timeout", "時間切れ"),
        ("recognition_failed", "聞き取れません"),
    ],
)
def test_transcription_removes_temporary_audio_and_releases_lock(
    tmp_path, monkeypatch, error, message
):
    from pathlib import Path

    from kihachi_mcp.services import voice_io

    helper = tmp_path / "helper"
    helper.touch()
    monkeypatch.setattr(voice_io, "asr_executable", lambda: helper)
    monkeypatch.setattr(voice_io.shutil, "which", lambda _: "ffmpeg")
    paths = []

    def run(command, **kwargs):
        if command[0] == "ffmpeg":
            source = Path(command[command.index("-i") + 1])
            assert source.read_bytes() == b"x" * 100
            paths.append(source.parent)
            Path(command[-1]).write_bytes(b"wav")
            return subprocess.CompletedProcess(command, 0)
        assert command[:3] == ["/usr/bin/open", "-W", "-n"]
        assert Path(command[-2]).read_bytes() == b"wav"
        Path(command[-1]).write_text(
            json.dumps({"ok": False, "error": error})
            if error
            else json.dumps({"ok": True, "transcript": "キックを短く"})
        )
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(voice_io.subprocess, "run", run)
    if error:
        with pytest.raises(VoiceError, match=message):
            transcribe_japanese(b"x" * 100, "audio/wav")
    else:
        assert transcribe_japanese(b"x" * 100, "audio/wav") == "キックを短く"
    assert paths and all(not path.exists() for path in paths)
    assert not voice_io._ASR_LOCK.locked()


@pytest.mark.parametrize("kind", ["text/plain", "application/json", "audio/mpeg"])
def test_asr_refuses_unexpected_media_type(kind):
    with pytest.raises(VoiceError):
        transcribe_japanese(b"x" * 100, kind)


def test_asr_refuses_oversized_audio():
    with pytest.raises(VoiceError):
        transcribe_japanese(b"x" * (5 * 1024 * 1024 + 1), "audio/wav")


def test_transcription_options_are_bounded_and_deduplicated(tmp_path, monkeypatch):
    from pathlib import Path

    from kihachi_mcp.services import voice_io

    helper = tmp_path / "helper"
    helper.touch()
    monkeypatch.setattr(voice_io, "asr_executable", lambda: helper)
    monkeypatch.setattr(voice_io.shutil, "which", lambda _: "ffmpeg")

    def run(command, **kwargs):
        if command[0] == "ffmpeg":
            Path(command[-1]).write_bytes(b"wav")
        else:
            Path(command[-1]).write_text(json.dumps({
                "ok": True,
                "transcript": "候補A",
                "alternatives": ["候補A", "候補B", "候補B", "", "x" * 501],
            }))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(voice_io.subprocess, "run", run)
    assert transcribe_japanese_options(b"x" * 100, "audio/wav") == {
        "transcript": "候補A", "alternatives": ["候補A", "候補B"]
    }


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg required")
def test_japanese_reply_is_local_wav_and_temp_files_are_removed():
    data = synthesize_japanese("こんにちは")
    assert data[:4] == b"RIFF"
    assert b"WAVE" in data[:16]
    assert len(data) > 44


def test_tts_rejects_unbounded_text():
    with pytest.raises(VoiceError):
        synthesize_japanese("長" * 501)
