"""Local macOS speech synthesis; temporary audio is removed after each reply."""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
from pathlib import Path
from tempfile import TemporaryDirectory

from kihachi_mcp.services.live_paths import bridge_state_dir

_ASR_LOCK = threading.Lock()
MAX_AUDIO_BYTES = 5 * 1024 * 1024
_AUDIO_SUFFIXES = {
    "audio/webm": ".webm",
    "audio/mp4": ".m4a",
    "audio/ogg": ".ogg",
    "audio/wav": ".wav",
}
_VOICE_TERM_CORRECTIONS = {"ミューテーションパンク": "Mutation Funk"}


class VoiceError(Exception):
    pass


def correct_voice_terms(transcript: str) -> str:
    """Apply only user-confirmed, visible spelling corrections to ASR text."""
    for heard, intended in _VOICE_TERM_CORRECTIONS.items():
        transcript = transcript.replace(heard, intended)
    return transcript


def asr_executable() -> Path:
    return (
        Path(bridge_state_dir())
        / "OnDeviceASR.app"
        / "Contents"
        / "MacOS"
        / "OnDeviceASR"
    )


def voice_status() -> dict:
    executable = asr_executable()
    result = {
        "ok": True,
        "asr_installed": executable.is_file(),
        "tts_installed": Path("/usr/bin/say").is_file(),
        "ffmpeg_available": bool(shutil.which("ffmpeg")),
        "cloud_fallback": False,
        "authorization": "unknown",
        "on_device": None,
        "available": None,
        "usage_description_present": False,
        "authorize_supported": False,
    }
    if not result["asr_installed"]:
        return {**result, "message": "ローカル音声認識の準備が必要です"}
    try:
        with TemporaryDirectory(prefix="kihachi-asr-status-") as directory:
            output_file = Path(directory) / "status.json"
            subprocess.run(
                [
                    "/usr/bin/open",
                    "-W",
                    "-n",
                    "--stdout",
                    str(output_file),
                    str(executable.parents[2]),
                    "--args",
                    "--status",
                ],
                capture_output=True,
                text=True,
                timeout=20,
                check=True,
            )
            data = json.loads(output_file.read_text())
        if (
            not isinstance(data, dict)
            or data.get("authorization")
            not in {"authorized", "denied", "restricted", "not_determined", "unknown"}
            or type(data.get("on_device")) is not bool
            or type(data.get("available")) is not bool
        ):
            raise ValueError("invalid status")
        result.update(
            {key: data[key] for key in ("authorization", "on_device", "available")}
        )
        result["usage_description_present"] = (
            data.get("usage_description_present") is True
        )
        result["authorize_supported"] = data.get("authorize_supported") is True
        messages = {
            "authorized": "音声認識の許可済みです",
            "denied": "macOSの設定で音声認識の許可を確認してください",
            "restricted": "このMacでは音声認識が制限されています",
            "not_determined": "初回の音声認識時にmacOSの許可画面が表示されます",
            "unknown": "音声認識の許可状態を確認できません",
        }
        result["message"] = messages[data["authorization"]]
        if not data["on_device"]:
            result["message"] = "日本語のオンデバイス認識を利用できません"
        if not result["usage_description_present"]:
            result["message"] = (
                "音声認識ヘルパーの更新が必要です。許可説明を読み取れません"
            )
    except (OSError, subprocess.SubprocessError, ValueError):
        result["message"] = (
            "音声認識の状態を取得できません。準備状況を再確認してください"
        )
    return result


def voice_authorize() -> dict:
    """Request macOS Speech permission from the installed local app, on user action."""
    status = voice_status()
    if not status["asr_installed"] or not status["authorize_supported"]:
        raise VoiceError("音声認識アプリの更新が必要です")
    if status["on_device"] is not True:
        raise VoiceError("このMacでは日本語のオンデバイス認識を使えません")
    if not _ASR_LOCK.acquire(blocking=False):
        raise VoiceError("別の音声を認識中です")
    try:
        with TemporaryDirectory(prefix="kihachi-asr-authorize-") as directory:
            output_file = Path(directory) / "authorization.json"
            executable = asr_executable()
            subprocess.run(
                [
                    "/usr/bin/open", "-W", "-n", str(executable.parents[2]),
                    "--args", "--authorize", str(output_file),
                ],
                capture_output=True,
                text=True,
                timeout=40,
                check=True,
            )
            data = json.loads(output_file.read_text())
            if not isinstance(data, dict) or data.get("authorization") not in {
                "authorized", "denied", "restricted", "not_determined", "unknown"
            }:
                raise ValueError("invalid authorization")
            permission = data["authorization"]
            return {
                "ok": permission == "authorized",
                "authorization": permission,
                "message": (
                    "macOSの音声認識を許可しました"
                    if permission == "authorized"
                    else "macOSの設定でKIHACHIの音声認識を許可してください"
                ),
            }
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise VoiceError("音声認識の許可状態を確認できませんでした") from exc
    finally:
        _ASR_LOCK.release()


def transcribe_japanese(audio: bytes, content_type: str) -> str:
    return transcribe_japanese_options(audio, content_type)["transcript"]


def transcribe_japanese_options(audio: bytes, content_type: str) -> dict:
    kind = content_type.split(";")[0].strip().lower()
    if kind not in _AUDIO_SUFFIXES or not 100 <= len(audio) <= MAX_AUDIO_BYTES:
        raise VoiceError("15秒以内・5MB以内の音声を送ってください")
    executable = asr_executable()
    if not executable.is_file() or not shutil.which("ffmpeg"):
        raise VoiceError("ローカル音声認識の準備がまだできていません")
    if not _ASR_LOCK.acquire(blocking=False):
        raise VoiceError("別の音声を認識中です")
    try:
        with TemporaryDirectory(prefix="kihachi-asr-") as directory:
            source = Path(directory) / ("input" + _AUDIO_SUFFIXES[kind])
            wav = Path(directory) / "input.wav"
            source.write_bytes(audio)
            try:
                subprocess.run(
                    [
                        "ffmpeg",
                        "-v",
                        "error",
                        "-nostdin",
                        "-i",
                        str(source),
                        "-t",
                        "15",
                        "-ac",
                        "1",
                        "-ar",
                        "16000",
                        "-c:a",
                        "pcm_s16le",
                        str(wav),
                    ],
                    capture_output=True,
                    timeout=20,
                    check=True,
                )
            except subprocess.CalledProcessError as exc:
                raise VoiceError(
                    "録音データを読み取れません。もう一度録音してください"
                ) from exc
            result_file = Path(directory) / "recognition.json"
            try:
                subprocess.run(
                    [
                        "/usr/bin/open",
                        "-W",
                        "-n",
                        str(executable.parents[2]),
                        "--args",
                        str(wav),
                        str(result_file),
                    ],
                    capture_output=True,
                    text=True,
                    timeout=80,
                    check=True,
                )
            except subprocess.TimeoutExpired as exc:
                raise VoiceError(
                    "音声認識が時間切れになりました。もう一度試してください"
                ) from exc
            except subprocess.CalledProcessError as exc:
                raise VoiceError(
                    "音声認識アプリを起動できません。準備状況を再確認してください"
                ) from exc
            if not result_file.is_file():
                raise VoiceError(
                    "音声認識アプリが終了しました。権限とアプリの状態を確認してください"
                )
            result = json.loads(result_file.read_text())
            if not isinstance(result, dict):
                raise VoiceError("音声認識の応答が不正です")
            if result.get("ok") is not True:
                error = result.get("error")
                if not isinstance(error, str):
                    raise VoiceError("音声認識の応答が不正です")
                if error == "speech_permission_required":
                    raise VoiceError("macOSの音声認識を許可してください")
                if error == "on_device_japanese_unavailable":
                    raise VoiceError("このMacでは日本語のオンデバイス認識を使えません")
                if error == "recognizer_unavailable":
                    raise VoiceError(
                        "macOSの音声認識を利用できません。準備状況を再確認してください"
                    )
                if error == "recognition_timeout":
                    raise VoiceError(
                        "音声認識が時間切れになりました。もう一度試してください"
                    )
                if error.startswith("recognition_failed"):
                    raise VoiceError(
                        "発話を聞き取れませんでした。マイクを確認し、静かな場所で再試行してください"
                    )
                raise VoiceError("このMacで音声を認識できませんでした")
            transcript = result.get("transcript", "")
            if (
                not isinstance(transcript, str)
                or not 1 <= len(transcript.strip()) <= 500
            ):
                raise VoiceError("音声を聞き取れませんでした。もう一度話してください")
            choices = result.get("alternatives", [])
            if not isinstance(choices, list):
                choices = []
            alternatives = [transcript.strip()]
            for choice in choices[:4]:
                if (
                    isinstance(choice, str)
                    and 1 <= len(choice.strip()) <= 500
                    and choice.strip() not in alternatives
                ):
                    alternatives.append(choice.strip())
            return {"transcript": transcript.strip(), "alternatives": alternatives}
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise VoiceError("ローカル音声認識に失敗しました") from exc
    finally:
        _ASR_LOCK.release()


def synthesize_japanese(text: str) -> bytes:
    if not isinstance(text, str) or not 1 <= len(text.strip()) <= 500:
        raise VoiceError("読み上げる文章は500文字以内にしてください")
    if not Path("/usr/bin/say").is_file() or not shutil.which("ffmpeg"):
        raise VoiceError("このMacの音声合成に必要な機能がありません")
    try:
        with TemporaryDirectory(prefix="kihachi-voice-") as directory:
            aiff = Path(directory) / "reply.aiff"
            wav = Path(directory) / "reply.wav"
            subprocess.run(
                ["/usr/bin/say", "-v", "Kyoko", "-o", str(aiff)],
                input=text,
                text=True,
                capture_output=True,
                timeout=30,
                check=True,
            )
            subprocess.run(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-nostdin",
                    "-i",
                    str(aiff),
                    "-ac",
                    "1",
                    "-ar",
                    "22050",
                    "-f",
                    "wav",
                    str(wav),
                ],
                capture_output=True,
                timeout=20,
                check=True,
            )
            if not wav.is_file() or not 44 < wav.stat().st_size <= 20_000_000:
                raise VoiceError("音声合成の結果が不正です")
            return wav.read_bytes()
    except (OSError, subprocess.SubprocessError) as exc:
        raise VoiceError("音声合成に失敗しました") from exc
