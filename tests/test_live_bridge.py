"""Bridge guarantees: loopback only, capped, deduplicated, and secret-safe."""

import json
import logging
import socket

import pytest

from kihachi_mcp.services.live_bridge import (
    LOOPBACK_HOST,
    LiveBridgeSession,
    LocalhostBridgeTransport,
    LoopbackUdpChannel,
)
from kihachi_mcp.services.live_paths import (
    SYSTEM_DARWIN,
    SYSTEM_WINDOWS,
    bridge_handshake_path,
    bridge_state_dir,
    candidate_store_dir,
)
from kihachi_mcp.services.live_transport import (
    ERROR_DUPLICATE,
    ERROR_TIMEOUT,
    ERROR_TOO_LARGE,
    PROTOCOL_NAME,
    PROTOCOL_VERSION,
    LiveTransportError,
    NullLiveTransport,
    build_message,
    decode_response,
)


class _RecordingChannel:
    """A datagram channel that records what the bridge tried to send."""

    def __init__(self, reply: dict | None = None, raise_on_receive=None) -> None:
        self.sent: list[bytes] = []
        self.reply = reply
        self.raise_on_receive = raise_on_receive
        self.closed = False

    def send(self, payload: bytes) -> None:
        self.sent.append(payload)

    def receive(self, timeout: float) -> bytes:
        if self.raise_on_receive is not None:
            raise self.raise_on_receive
        return json.dumps(self.reply or {}).encode("utf-8")

    def close(self) -> None:
        self.closed = True


def _session(tmp_path) -> LiveBridgeSession:
    return LiveBridgeSession(
        handshake_path=tmp_path / "live-bridge.json", environ={}
    )


def test_null_transport_refuses_everything() -> None:
    with pytest.raises(LiveTransportError, match="no Ableton Live transport") as error:
        NullLiveTransport().request({})

    assert error.value.code == "unavailable"


def test_build_message_carries_no_credential() -> None:
    message = build_message("get_state", "req-1")

    assert "token" not in message
    assert message["protocol"] == PROTOCOL_NAME
    assert message["version"] == PROTOCOL_VERSION


def test_build_message_refuses_unknown_methods() -> None:
    with pytest.raises(LiveTransportError, match="unsupported method"):
        build_message("format_disk", "req-1")


def test_decode_response_rejects_mismatched_request_ids() -> None:
    with pytest.raises(LiveTransportError, match="request_id does not match"):
        decode_response(
            {
                "protocol": PROTOCOL_NAME,
                "version": PROTOCOL_VERSION,
                "request_id": "other",
                "ok": True,
            },
            "req-1",
        )


def test_decode_response_rejects_a_foreign_protocol() -> None:
    with pytest.raises(LiveTransportError, match="not a KIHACHI message"):
        decode_response({"protocol": "something-else"}, "req-1")


def test_decode_response_rejects_a_different_protocol_version() -> None:
    with pytest.raises(LiveTransportError, match="protocol version must be"):
        decode_response(
            {"protocol": PROTOCOL_NAME, "version": 99, "request_id": "r", "ok": True},
            "r",
        )


def test_stale_replies_are_discarded_until_the_matching_request_id(tmp_path) -> None:
    class _StaleThenFresh(_RecordingChannel):
        def __init__(self) -> None:
            super().__init__()
            self._replies = [
                {
                    "protocol": PROTOCOL_NAME,
                    "version": PROTOCOL_VERSION,
                    "request_id": "old",
                    "ok": True,
                    "result": {},
                },
                {
                    "protocol": PROTOCOL_NAME,
                    "version": PROTOCOL_VERSION,
                    "request_id": "req-2",
                    "ok": True,
                    "result": {"fresh": True},
                },
            ]

        def receive(self, timeout: float) -> bytes:
            return json.dumps(self._replies.pop(0)).encode("utf-8")

    channel = _StaleThenFresh()
    transport = LocalhostBridgeTransport(session=_session(tmp_path), channel=channel)
    result = transport.request(build_message("ping", "req-2"))
    assert result["result"] == {"fresh": True}


def test_the_token_is_attached_by_the_transport_not_the_caller(tmp_path) -> None:
    channel = _RecordingChannel(
        {
            "protocol": PROTOCOL_NAME,
            "version": PROTOCOL_VERSION,
            "request_id": "req-1",
            "ok": True,
            "result": {},
        }
    )
    session = _session(tmp_path)
    transport = LocalhostBridgeTransport(session=session, channel=channel)

    transport.request(build_message("ping", "req-1"))
    envelope = json.loads(channel.sent[0])

    assert envelope["token"] == session.token
    assert len(session.token) >= 32


def test_a_duplicate_request_id_is_refused_without_resending(tmp_path) -> None:
    reply = {
        "protocol": PROTOCOL_NAME,
        "version": PROTOCOL_VERSION,
        "request_id": "req-1",
        "ok": True,
        "result": {},
    }
    channel = _RecordingChannel(reply)
    transport = LocalhostBridgeTransport(session=_session(tmp_path), channel=channel)
    transport.request(build_message("ping", "req-1"))

    with pytest.raises(LiveTransportError, match="already sent") as error:
        transport.request(build_message("ping", "req-1"))

    assert error.value.code == ERROR_DUPLICATE
    assert len(channel.sent) == 1


def test_an_oversized_request_is_refused_before_sending(tmp_path) -> None:
    channel = _RecordingChannel()
    transport = LocalhostBridgeTransport(
        session=_session(tmp_path), channel=channel, max_request_bytes=200
    )
    message = build_message(
        "apply_operation", "req-1", {"notes": [{"pitch": 60}] * 500}
    )

    with pytest.raises(LiveTransportError, match="over the") as error:
        transport.request(message)

    assert error.value.code == ERROR_TOO_LARGE
    assert channel.sent == []


def test_a_request_without_an_id_is_refused(tmp_path) -> None:
    transport = LocalhostBridgeTransport(
        session=_session(tmp_path), channel=_RecordingChannel()
    )

    with pytest.raises(LiveTransportError, match="request_id is required"):
        transport.request({"protocol": PROTOCOL_NAME, "method": "ping"})


def test_a_timeout_is_reported_and_not_retried(tmp_path) -> None:
    channel = _RecordingChannel(
        raise_on_receive=LiveTransportError(ERROR_TIMEOUT, "no reply in 5s")
    )
    transport = LocalhostBridgeTransport(session=_session(tmp_path), channel=channel)

    with pytest.raises(LiveTransportError) as error:
        transport.request(build_message("ping", "req-1"))

    assert error.value.code == ERROR_TIMEOUT
    assert len(channel.sent) == 1


def test_a_non_json_reply_is_refused(tmp_path) -> None:
    class BadChannel(_RecordingChannel):
        def receive(self, timeout: float) -> bytes:
            return b"\xff\xfe not json"

    transport = LocalhostBridgeTransport(
        session=_session(tmp_path), channel=BadChannel()
    )

    with pytest.raises(LiveTransportError, match="not JSON"):
        transport.request(build_message("ping", "req-1"))


def test_the_handshake_file_is_owner_only_and_removable(tmp_path) -> None:
    session = _session(tmp_path)

    path = session.write_handshake()
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload["host"] == LOOPBACK_HOST
    assert payload["token"] == session.token
    assert oct(path.stat().st_mode)[-3:] == "600"

    session.remove_handshake()
    assert not path.exists()


def test_bridge_logs_never_contain_the_session_token(tmp_path, caplog) -> None:
    channel = _RecordingChannel(
        {
            "protocol": PROTOCOL_NAME,
            "version": PROTOCOL_VERSION,
            "request_id": "req-1",
            "ok": True,
            "result": {},
        }
    )
    session = _session(tmp_path)
    transport = LocalhostBridgeTransport(session=session, channel=channel)

    with caplog.at_level(logging.DEBUG, logger="kihachi.live.bridge"):
        session.write_handshake()
        transport.request(build_message("ping", "req-1"))

    combined = "\n".join(
        record.getMessage() + json.dumps(record.__dict__, default=str)
        for record in caplog.records
    )
    assert session.token not in combined
    assert caplog.records


def test_transport_errors_never_leak_the_session_token(tmp_path) -> None:
    session = _session(tmp_path)
    transport = LocalhostBridgeTransport(
        session=session, channel=_RecordingChannel(), max_request_bytes=10
    )

    with pytest.raises(LiveTransportError) as error:
        transport.request(build_message("ping", "req-1"))

    assert session.token not in error.value.message
    assert session.token not in str(error.value)


def test_close_releases_the_channel_and_the_handshake(tmp_path) -> None:
    channel = _RecordingChannel()
    session = _session(tmp_path)
    session.write_handshake()
    transport = LocalhostBridgeTransport(session=session, channel=channel)

    transport.close()

    assert channel.closed is True
    assert not session.handshake_path.exists()


def test_the_udp_channel_binds_to_loopback_only() -> None:
    channel = LoopbackUdpChannel(device_port=0, reply_port=0)
    try:
        bound_host = LOOPBACK_HOST
        assert channel.reply_port > 0
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.bind((bound_host, 0))
        finally:
            probe.close()
    finally:
        channel.close()


def test_the_udp_channel_raises_the_macos_send_buffer() -> None:
    channel = LoopbackUdpChannel(device_port=0, reply_port=0)
    try:
        sndbuf = channel._socket.getsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF)
        assert sndbuf >= 60_000
    finally:
        channel.close()


def test_a_busy_reply_port_reports_unavailable() -> None:
    holder = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    holder.bind((LOOPBACK_HOST, 0))
    busy_port = holder.getsockname()[1]
    try:
        with pytest.raises(LiveTransportError, match="cannot bind") as error:
            LoopbackUdpChannel(device_port=0, reply_port=busy_port)
        assert error.value.code == "unavailable"
    finally:
        holder.close()


def test_macos_and_windows_state_directories_are_separated() -> None:
    macos = bridge_state_dir(
        SYSTEM_DARWIN, {"HOME": "/Users/kihachi"}
    )
    windows = bridge_state_dir(
        SYSTEM_WINDOWS, {"LOCALAPPDATA": r"C:\Users\kihachi\AppData\Local"}
    )
    linux = bridge_state_dir("Linux", {"HOME": "/home/ci"})

    assert str(macos) == "/Users/kihachi/Library/Application Support/KIHACHI"
    assert str(windows) == r"C:\Users\kihachi\AppData\Local\KIHACHI"
    assert str(linux) == "/home/ci/.local/state/kihachi"


def test_the_state_directory_can_be_overridden_for_tests() -> None:
    path = bridge_state_dir(SYSTEM_DARWIN, {"KIHACHI_LIVE_STATE_DIR": "/tmp/kihachi"})

    assert str(path) == "/tmp/kihachi"


def test_saved_candidates_sit_beside_the_handshake_file() -> None:
    env = {"HOME": "/Users/kihachi"}

    assert candidate_store_dir(SYSTEM_DARWIN, env).parent == bridge_state_dir(
        SYSTEM_DARWIN, env
    )
    assert candidate_store_dir(SYSTEM_DARWIN, env).name == "candidates"


def test_the_project_directory_can_be_moved() -> None:
    env = {"HOME": "/Users/kihachi", "KIHACHI_PROJECT_DIR": "/tmp/songs"}

    assert str(candidate_store_dir(SYSTEM_DARWIN, env)) == "/tmp/songs"


def test_the_handshake_filename_is_stable_across_platforms() -> None:
    macos = bridge_handshake_path(SYSTEM_DARWIN, {"HOME": "/Users/kihachi"})
    windows = bridge_handshake_path(
        SYSTEM_WINDOWS, {"LOCALAPPDATA": r"C:\Users\kihachi\AppData\Local"}
    )

    assert macos.name == "live-bridge.json"
    assert windows.name == "live-bridge.json"


def test_snapshot_falls_back_to_chunked_reads_when_reply_exceeds_max_bytes() -> None:
    """単一 get_state が response_too_large を返した場合でも分割取得で完結する。"""
    from kihachi_mcp.services.live_state_inspector import LiveStateInspector
    from kihachi_mcp.services.live_transport import (
        ERROR_RESPONSE_TOO_LARGE,
        METHOD_GET_STATE,
    )
    from kihachi_mcp.services.live_transport_fake import (
        FakeArrangementClip,
        FakeLiveSet,
        FakeLiveTransport,
        FakeSessionClip,
    )

    live_set = FakeLiveSet()
    live_set.add_track("Kick")
    live_set.add_track("Bass")
    for t in range(len(live_set.tracks)):
        live_set.session_clips[(t, 0)] = FakeSessionClip(
            name=f"Clip-{t}", length_beats=16.0
        )
        live_set.arrangement_clips.append(
            FakeArrangementClip(
                track_index=t, name=f"Arr-{t}", start_beats=0.0, length_beats=16.0
            )
        )
    live_set.locators = [{"name": "Intro", "time": 0.0}]

    class OversizedFullGetStateTransport:
        def __init__(self, inner: FakeLiveTransport) -> None:
            self._inner = inner
            self.methods: list[str] = []

        def request(self, message: dict) -> dict:
            method = str(message.get("method") or "")
            payload = message.get("payload") or {}
            self.methods.append(method)
            if method == METHOD_GET_STATE and (
                payload.get("include_arrangement") is not False
                or payload.get("include_session_clips") is not False
            ):
                return {
                    "protocol": PROTOCOL_NAME,
                    "version": PROTOCOL_VERSION,
                    "request_id": message["request_id"],
                    "ok": False,
                    "error": {
                        "code": ERROR_RESPONSE_TOO_LARGE,
                        "message": "response exceeds Max outlet limit",
                    },
                }
            return self._inner.request(message)

    fake = FakeLiveTransport(live_set)
    wrapped = OversizedFullGetStateTransport(fake)
    inspector = LiveStateInspector(transport=wrapped)
    snapshot = inspector.snapshot(
        include_arrangement=True,
        count_session_notes=False,
        include_session_clips=True,
    )

    assert len(snapshot.session_clips) == len(live_set.tracks)
    assert len(snapshot.arrangement_clips) == len(live_set.tracks)
    assert "get_session_track_clips" in wrapped.methods
    assert "get_arrangement_track_summary" in wrapped.methods
    assert "get_locator_summary" in wrapped.methods


def test_resolve_bridge_timeout_seconds_reads_env_and_clamps() -> None:
    """環境変数によるタイムアウト指定・不正値フォールバック・上下限クランプを検証する。"""
    from kihachi_mcp.services.live_bridge import (
        BRIDGE_TIMEOUT_ENV,
        MAX_BRIDGE_TIMEOUT_SECONDS,
        MIN_BRIDGE_TIMEOUT_SECONDS,
        STUDIO_BRIDGE_TIMEOUT_SECONDS,
        resolve_bridge_timeout_seconds,
    )

    assert resolve_bridge_timeout_seconds(environ={}) == STUDIO_BRIDGE_TIMEOUT_SECONDS
    assert resolve_bridge_timeout_seconds(environ={BRIDGE_TIMEOUT_ENV: "75.5"}) == 75.5
    assert (
        resolve_bridge_timeout_seconds(environ={BRIDGE_TIMEOUT_ENV: "invalid"})
        == STUDIO_BRIDGE_TIMEOUT_SECONDS
    )
    assert (
        resolve_bridge_timeout_seconds(environ={BRIDGE_TIMEOUT_ENV: "nan"})
        == STUDIO_BRIDGE_TIMEOUT_SECONDS
    )
    assert (
        resolve_bridge_timeout_seconds(environ={BRIDGE_TIMEOUT_ENV: "0.1"})
        == MIN_BRIDGE_TIMEOUT_SECONDS
    )
    assert (
        resolve_bridge_timeout_seconds(environ={BRIDGE_TIMEOUT_ENV: "9999"})
        == MAX_BRIDGE_TIMEOUT_SECONDS
    )


def test_bridge_logs_elapsed_ms_on_response_and_timeout_without_retry(
    tmp_path, caplog
) -> None:
    """正常応答とタイムアウト時に elapsed_ms が記録され、タイムアウト時に再送しないこと。"""
    ok_channel = _RecordingChannel(
        {
            "protocol": PROTOCOL_NAME,
            "version": PROTOCOL_VERSION,
            "request_id": "req-ok",
            "ok": True,
            "result": {},
        }
    )
    transport = LocalhostBridgeTransport(session=_session(tmp_path), channel=ok_channel)
    with caplog.at_level(logging.INFO, logger="kihachi.live.bridge"):
        transport.request(build_message("ping", "req-ok"))

    response_records = [r for r in caplog.records if r.message == "live.bridge.response"]
    assert len(response_records) == 1
    assert response_records[0].method == "ping"
    assert isinstance(response_records[0].elapsed_ms, float)
    assert response_records[0].elapsed_ms >= 0.0

    caplog.clear()
    timeout_channel = _RecordingChannel(
        raise_on_receive=LiveTransportError(ERROR_TIMEOUT, "no reply in 5s")
    )
    timeout_transport = LocalhostBridgeTransport(
        session=_session(tmp_path), channel=timeout_channel, timeout_seconds=5.0
    )
    with (
        caplog.at_level(logging.WARNING, logger="kihachi.live.bridge"),
        pytest.raises(LiveTransportError) as error,
    ):
        timeout_transport.request(build_message("apply_operation", "req-to"))

    assert error.value.code == ERROR_TIMEOUT
    assert len(timeout_channel.sent) == 1
    timeout_records = [r for r in caplog.records if r.message == "live.bridge.timeout"]
    assert len(timeout_records) == 1
    assert timeout_records[0].method == "apply_operation"
    assert timeout_records[0].timeout_seconds == 5.0
    assert isinstance(timeout_records[0].elapsed_ms, float)
