"""Planner safety rules: ownership, collisions, devices, and transport state."""

from live_fixtures import empty_live_set, planner, project_plan, snapshot_of

from kihachi_mcp.models import Arrangement, TrackSpec
from kihachi_mcp.models.live_contract import (
    OP_CREATE_MIDI_TRACK,
    OP_CREATE_SCENE,
    OP_LOAD_LIVE_DEVICE,
    OP_SET_TEMPO,
    STATUS_APPROVAL_REQUIRED,
    STATUS_BLOCKED,
)
from kihachi_mcp.services.live_transport_fake import FakeLiveTransport, FakeSessionClip


def _plan_for(transport: FakeLiveTransport, **kwargs):
    return planner().create_session_plan(
        project_plan(**kwargs), snapshot_of(transport)
    )


def test_new_set_plan_creates_tracks_scenes_clips_and_devices() -> None:
    transport = FakeLiveTransport(empty_live_set(tempo=120.0))

    plan = _plan_for(transport)
    ops = [operation.op for operation in plan.operations]

    assert plan.status == STATUS_APPROVAL_REQUIRED
    assert plan.conflicts == []
    assert ops.count(OP_CREATE_MIDI_TRACK) == 2
    assert ops.count(OP_CREATE_SCENE) == 2
    assert ops.count(OP_LOAD_LIVE_DEVICE) == 2
    assert ops[0] == OP_SET_TEMPO
    assert plan.destructive_operation_count == 1


def test_tempo_is_left_alone_when_it_already_matches() -> None:
    transport = FakeLiveTransport(empty_live_set(tempo=110.0))

    plan = _plan_for(transport)

    assert OP_SET_TEMPO not in [operation.op for operation in plan.operations]
    assert plan.destructive_operation_count == 0


def test_a_user_track_with_the_same_name_is_never_reused() -> None:
    live = empty_live_set(tempo=110.0)
    live.add_track("Kick", "midi")
    transport = FakeLiveTransport(live)

    plan = _plan_for(transport)
    created = [
        operation.arguments["name"]
        for operation in plan.operations
        if operation.op == OP_CREATE_MIDI_TRACK
    ]

    assert "Kick [KIHACHI]" in created
    assert plan.conflicts == []
    assert any("user owned" in warning for warning in plan.warnings)


def test_an_existing_kihachi_track_is_reused_without_recreating_it() -> None:
    live = empty_live_set(tempo=110.0)
    live.add_track("Kick [KIHACHI]", "midi")
    live.tracks[0]["device_names"] = ["Drum Rack"]
    transport = FakeLiveTransport(live)

    plan = _plan_for(transport)
    created = [
        operation.arguments["name"]
        for operation in plan.operations
        if operation.op == OP_CREATE_MIDI_TRACK
    ]

    assert created == ["Bass [KIHACHI]"]
    loaded = [
        operation.arguments["device_name"]
        for operation in plan.operations
        if operation.op == OP_LOAD_LIVE_DEVICE
    ]
    assert loaded == ["Operator"]


def test_a_user_clip_in_a_target_slot_blocks_the_plan() -> None:
    live = empty_live_set(tempo=110.0)
    live.add_track("Kick [KIHACHI]", "midi")
    live.tracks[0]["device_names"] = ["Drum Rack"]
    live.add_track("Bass [KIHACHI]", "midi")
    live.tracks[1]["device_names"] = ["Operator"]
    live.add_scene("Intro [KIHACHI]")
    live.add_scene("Drop [KIHACHI]")
    live.session_clips[(0, 0)] = FakeSessionClip(name="My Groove", length_beats=16.0)
    transport = FakeLiveTransport(live)

    plan = _plan_for(transport)

    assert plan.status == STATUS_BLOCKED
    assert plan.operations == []
    assert plan.conflicts[0].kind == "user_owned_clip"
    assert "My Groove" in plan.conflicts[0].detail


def test_a_kihachi_clip_in_a_target_slot_is_replaced_as_a_destructive_step() -> None:
    live = empty_live_set(tempo=110.0)
    live.add_track("Kick [KIHACHI]", "midi")
    live.tracks[0]["device_names"] = ["Drum Rack"]
    live.add_track("Bass [KIHACHI]", "midi")
    live.tracks[1]["device_names"] = ["Operator"]
    live.add_scene("Intro [KIHACHI]")
    live.add_scene("Drop [KIHACHI]")
    live.session_clips[(0, 0)] = FakeSessionClip(
        name="Intro [K:abcd1234]", length_beats=16.0
    )
    transport = FakeLiveTransport(live)

    plan = _plan_for(transport)

    assert plan.status == STATUS_APPROVAL_REQUIRED
    assert plan.destructive_operation_count == 1
    assert any("replacing notes" in warning for warning in plan.warnings)
    replaced = [
        operation
        for operation in plan.operations
        if operation.destructive and operation.op == "replace_clip_notes"
    ]
    assert [condition.kind for condition in replaced[0].preconditions].count(
        "clip_is_managed"
    ) == 1


def test_a_missing_stock_device_blocks_the_plan_without_substitution() -> None:
    live = empty_live_set(tempo=110.0, available_devices=["Simpler", "EQ Eight"])
    transport = FakeLiveTransport(live)

    plan = _plan_for(transport)

    assert plan.status == STATUS_BLOCKED
    kinds = {conflict.kind for conflict in plan.conflicts}
    assert kinds == {"device_unavailable"}
    assert all(
        "no substitute" in conflict.detail for conflict in plan.conflicts
    )


def test_recording_blocks_planning() -> None:
    live = empty_live_set(tempo=110.0)
    live.is_recording = True
    transport = FakeLiveTransport(live)

    plan = _plan_for(transport)

    assert plan.status == STATUS_BLOCKED
    assert plan.conflicts[0].kind == "live_is_recording"


def test_a_running_transport_blocks_planning() -> None:
    live = empty_live_set(tempo=110.0)
    live.is_playing = True
    transport = FakeLiveTransport(live)

    plan = _plan_for(transport)

    assert plan.status == STATUS_BLOCKED
    assert plan.conflicts[0].kind == "live_is_playing"


def test_a_project_without_sections_is_blocked() -> None:
    transport = FakeLiveTransport(empty_live_set(tempo=110.0))

    plan = _plan_for(transport, sections=[])

    assert plan.status == STATUS_BLOCKED
    assert plan.conflicts[0].kind == "no_sections"


def test_a_project_without_tracks_is_blocked() -> None:
    transport = FakeLiveTransport(empty_live_set(tempo=110.0))

    plan = _plan_for(transport, tracks=[])

    assert plan.status == STATUS_BLOCKED
    assert plan.conflicts[0].kind == "no_tracks"


def test_audio_tracks_get_no_instrument_and_no_clips() -> None:
    transport = FakeLiveTransport(empty_live_set(tempo=110.0))

    plan = planner().create_session_plan(
        project_plan(tracks=[TrackSpec(name="FX", type="Audio", color="Gray")]),
        snapshot_of(transport),
    )
    ops = [operation.op for operation in plan.operations]

    assert "create_audio_track" in ops
    assert OP_LOAD_LIVE_DEVICE not in ops
    assert "create_session_clip" not in ops


def test_clip_lengths_follow_the_set_meter_not_a_fixed_four_beats() -> None:
    live = empty_live_set(tempo=110.0, numerator=3, denominator=4)
    transport = FakeLiveTransport(live)

    plan = _plan_for(transport)
    clip = next(
        operation
        for operation in plan.operations
        if operation.op == "create_session_clip"
    )

    assert clip.arguments["length_beats"] == 12.0


def test_every_operation_declares_a_not_recording_precondition() -> None:
    transport = FakeLiveTransport(empty_live_set(tempo=120.0))

    plan = _plan_for(transport)

    for operation in plan.operations:
        assert "not_recording" in [
            condition.kind for condition in operation.preconditions
        ]


def test_planning_the_same_project_twice_yields_different_idempotency_keys() -> None:
    transport = FakeLiveTransport(empty_live_set(tempo=110.0))
    shared = planner()
    snapshot = snapshot_of(transport)

    first = shared.create_session_plan(project_plan(), snapshot)
    second = shared.create_session_plan(project_plan(), snapshot)

    assert first.idempotency_key != second.idempotency_key
    assert first.source_plan_hash == second.source_plan_hash


def test_more_sections_produce_more_scenes_but_reuse_existing_ones() -> None:
    live = empty_live_set(tempo=110.0)
    live.add_scene("Intro [KIHACHI]")
    transport = FakeLiveTransport(live)

    plan = planner().create_session_plan(
        project_plan(
            sections=[
                Arrangement(name="Intro", start_bar=1, length_bars=16),
                Arrangement(name="Groove A", start_bar=17, length_bars=16),
                Arrangement(name="Drop", start_bar=33, length_bars=32),
            ]
        ),
        snapshot_of(transport),
    )
    created_scenes = [
        operation.arguments["name"]
        for operation in plan.operations
        if operation.op == OP_CREATE_SCENE
    ]

    assert created_scenes == ["Groove A [KIHACHI]", "Drop [KIHACHI]"]
