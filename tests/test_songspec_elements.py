"""SongSpec elements（ジャンル定義要素）の配線とプロンプト差し込みの回帰テスト。"""

from __future__ import annotations

import json
from typing import Any

from test_studio_workflow import _studio

from kihachi_mcp.models.midi_composer import MidiCompositionRequest, MusicalConstraints
from kihachi_mcp.models.songspec import SongSpec
from kihachi_mcp.services.midi_composer import ProviderMIDIComposer

ELEMENTS_SPEC: dict[str, Any] = {
    "genre": "mutation_funk",
    "tempo": 124,
    "key": "Gm",
    "length_minutes": 0.52,
    "bars": 16,
    "tracks": [],
    "sections": [
        {"name": "Intro", "bars": 4, "energy": 0.4, "chords": ["Gm"], "tracks": [], "note": ""},
        {"name": "Build", "bars": 4, "energy": 0.65, "chords": ["Gm", "Cm"], "tracks": [], "note": ""},
        {"name": "Drop", "bars": 4, "energy": 0.95, "chords": ["Gm", "Bb"], "tracks": [], "note": ""},
        {"name": "Outro", "bars": 4, "energy": 0.5, "chords": ["Cm", "Gm"], "tracks": [], "note": ""},
    ],
    "groove": {"swing": 0.54, "syncopation": 0.35},
    "elements": {
        "bass": {"technique": "slap", "ghost_notes": True, "octave_pops": True},
        "guitar": {"technique": "cutting_16th", "muted": True},
        "horn": {"role": "stabs_and_riffs"},
        "vocal": {"effect": "vocoder"},
    },
}


class _CaptureProvider:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    def capabilities(self) -> dict[str, Any]:
        return {"provider": "fake", "structured_output": True, "paid_api": False}

    def generate_structured(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        self.prompts.append(prompt)
        return {
            "notes": [
                {"pitch": 43, "start": 0.0, "duration": 0.25, "velocity": 100, "channel": 0},
                {"pitch": 43, "start": 2.0, "duration": 0.25, "velocity": 96, "channel": 0},
            ]
        }


def test_songspec_elements_roundtrip_and_backward_compatibility() -> None:
    plain_dict = {k: v for k, v in ELEMENTS_SPEC.items() if k != "elements"}
    plain_spec = SongSpec.from_dict(plain_dict)
    assert plain_spec.elements == {}
    assert "elements" not in plain_spec.to_dict()

    with_elements = SongSpec.from_dict(ELEMENTS_SPEC)
    assert with_elements.elements["bass"] == {
        "technique": "slap",
        "ghost_notes": True,
        "octave_pops": True,
    }
    assert with_elements.to_dict()["elements"] == ELEMENTS_SPEC["elements"]


def test_preview_request_byte_identical_without_elements(tmp_path) -> None:
    runtime, _live = _studio(tmp_path)
    plain_spec = {k: v for k, v in ELEMENTS_SPEC.items() if k != "elements"}
    base = {"key": "Gm", "tempo": 124, "bars": 4, "role": "bass"}

    preview_plain = runtime.midi_composer_preview(base, songspec=plain_spec, start_bar=1)
    preview_unlisted_role = runtime.midi_composer_preview(
        {"key": "Gm", "tempo": 124, "bars": 4, "role": "arp"},
        songspec=ELEMENTS_SPEC,
        start_bar=5,
    )
    preview_unlisted_plain = runtime.midi_composer_preview(
        {"key": "Gm", "tempo": 124, "bars": 4, "role": "arp"},
        songspec=plain_spec,
        start_bar=5,
    )

    assert "element_spec" not in preview_plain["request"]["constraints"]
    assert json.dumps(preview_unlisted_role["request"], sort_keys=True) == json.dumps(
        preview_unlisted_plain["request"], sort_keys=True
    )


def test_preview_attaches_element_spec_for_matching_roles(tmp_path) -> None:
    runtime, _live = _studio(tmp_path)
    for role, expected_key in (
        ("bass", "technique"),
        ("Guitar", "technique"),
        ("Horn", "role"),
        ("Vocal", "effect"),
    ):
        preview = runtime.midi_composer_preview(
            {"key": "Gm", "tempo": 124, "bars": 4, "role": role},
            songspec=ELEMENTS_SPEC,
            start_bar=9,
        )
        assert preview["ok"] is True
        elem = preview["request"]["constraints"]["element_spec"]
        assert expected_key in elem


def test_provider_prompt_injects_verifiable_element_instructions() -> None:
    provider_with = _CaptureProvider()
    provider_plain = _CaptureProvider()

    req_plain = MidiCompositionRequest(
        constraints=MusicalConstraints(
            key="Gm", tempo=124, bars=4, role="bass", section_name="Drop", section_energy=0.95
        )
    )
    req_with = MidiCompositionRequest(
        constraints=MusicalConstraints(
            key="Gm",
            tempo=124,
            bars=4,
            role="bass",
            section_name="Drop",
            section_energy=0.95,
            element_spec={"technique": "slap", "ghost_notes": True, "octave_pops": True},
        )
    )

    ProviderMIDIComposer(provider_plain, max_attempts=1).compose(req_plain)
    ProviderMIDIComposer(provider_with, max_attempts=1).compose(req_with)

    prompt_with = provider_with.prompts[0]
    assert "slap bass" in prompt_with
    assert "velocity >= 95" in prompt_with
    assert "<= 40" in prompt_with
    assert "+12 semitones" in prompt_with
    assert "element_spec" not in provider_plain.prompts[0]
    assert "slap bass" not in provider_plain.prompts[0]


def test_ghost_notes_lowers_velocity_min_and_preserves_low_velocity(tmp_path) -> None:
    runtime, _live = _studio(tmp_path)
    plain_spec = {k: v for k, v in ELEMENTS_SPEC.items() if k != "elements"}

    preview_plain = runtime.midi_composer_preview(
        {"key": "Gm", "tempo": 124, "bars": 4, "role": "bass"},
        songspec=plain_spec,
        start_bar=1,
    )
    preview_ghost = runtime.midi_composer_preview(
        {"key": "Gm", "tempo": 124, "bars": 4, "role": "bass"},
        songspec=ELEMENTS_SPEC,
        start_bar=1,
    )
    assert preview_plain["request"]["constraints"]["velocity_min"] == 65
    assert preview_ghost["request"]["constraints"]["velocity_min"] == 20

    class _GhostProvider:
        def capabilities(self) -> dict[str, Any]:
            return {"provider": "fake", "structured_output": True, "paid_api": False}

        def generate_structured(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
            return {
                "notes": [
                    {"pitch": 43, "start": 0.0, "duration": 0.25, "velocity": 100, "channel": 0},
                    {"pitch": 43, "start": 0.75, "duration": 0.25, "velocity": 32, "channel": 0},
                ]
            }

    req = MidiCompositionRequest(
        constraints=MusicalConstraints.from_dict(preview_ghost["request"]["constraints"])
    )
    notes = ProviderMIDIComposer(_GhostProvider(), max_attempts=1).compose(req)
    assert min(n.velocity for n in notes) == 32


def test_extended_roles_and_compose_cells_roundtrip(tmp_path) -> None:
    runtime, _live = _studio(tmp_path)
    parent_id = runtime.generate("Mutation Funk、124 BPM、Gm、16小節", seed=3)["candidate"]["candidate_id"]

    extended_spec = {
        **ELEMENTS_SPEC,
        "elements": {
            **ELEMENTS_SPEC["elements"],
            "percussion": {"style": "big_beats"},
            "hats": {"style": "big_beats"},
        },
        "compose_cells": {
            "guitar": ["Drop", "Outro"],
            "horn": ["Drop"],
            "vocal": ["Drop"],
            "hats": ["Intro", "Build", "Drop", "Outro"],
        },
    }
    parsed = SongSpec.from_dict(extended_spec)
    assert parsed.compose_cells["guitar"] == ["Drop", "Outro"]
    assert parsed.to_dict()["compose_cells"] == extended_spec["compose_cells"]

    plain_parsed = SongSpec.from_dict({k: v for k, v in ELEMENTS_SPEC.items() if k != "elements"})
    assert "compose_cells" not in plain_parsed.to_dict()

    for role, section, start_bar, pitch in (
        ("guitar", "Drop", 9, 67),
        ("horn", "Drop", 9, 67),
        ("vocal", "Drop", 9, 67),
        ("hats", "Intro", 1, 42),
    ):
        preview = runtime.midi_composer_preview(
            {"key": "Gm", "tempo": 124, "bars": 4, "role": role},
            songspec=extended_spec,
            start_bar=start_bar,
        )
        assert preview["ok"] is True
        assert preview["request"]["constraints"]["element_spec"]
        proposal = runtime.propose_composer_adoption(
            parent_id,
            role,
            4,
            [{"pitch": pitch, "start": 0.0, "duration": 0.5, "velocity": 95}],
            section_name=section,
        )
        assert proposal["ok"] is True


def test_big_beats_prompt_instructions_for_percussion_and_hats() -> None:
    provider_perc = _CaptureProvider()
    provider_hats = _CaptureProvider()

    req_perc = MidiCompositionRequest(
        constraints=MusicalConstraints(
            key="Gm",
            tempo=124,
            bars=4,
            role="percussion",
            element_spec={"style": "big_beats"},
        )
    )
    req_hats = MidiCompositionRequest(
        constraints=MusicalConstraints(
            key="Gm",
            tempo=124,
            bars=4,
            role="hats",
            element_spec={"style": "big_beats"},
        )
    )
    ProviderMIDIComposer(provider_perc, max_attempts=1).compose(req_perc)
    ProviderMIDIComposer(provider_hats, max_attempts=1).compose(req_hats)

    assert "at most 4 hits per bar" in provider_perc.prompts[0]
    assert "0.5-1.0 beats" in provider_perc.prompts[0]
    assert "at most 8 hits per bar" in provider_hats.prompts[0]
    assert "pitch 42" in provider_hats.prompts[0]


def test_layer3_device_profile_override_and_backward_compatibility(tmp_path) -> None:
    runtime, _live = _studio(tmp_path)
    parent_id = runtime.generate("Mutation Funk、124 BPM、Gm、16小節", seed=3)["candidate"]["candidate_id"]

    # elements なし（従来どおり：Bass=Analog, Guitar=Drift）
    preview_plain = runtime.apply_preview(parent_id)
    assert preview_plain["ok"] is True
    devices_plain = preview_plain["summary"]["planned_devices"]
    assert devices_plain["Bass"] == {"device": "Analog"}
    assert devices_plain["Guitar"] == {"device": "Drift"}

    # elements あり（adopt-preview で elements 引き継ぎ → Bass=Operator/bass.slap, Guitar=Operator/guitar.cutting_16th）
    proposal = runtime.propose_composer_adoption(
        parent_id,
        "bass",
        4,
        [{"pitch": 43, "start": 0.0, "duration": 0.25, "velocity": 100}],
        section_name="Drop",
        elements=ELEMENTS_SPEC["elements"],
    )
    assert proposal["ok"] is True
    child_id = runtime.decide_revision(proposal["revision"]["revision_id"], True)["selected_candidate_id"]
    # ディスクからの再読み込みでも elements が保持されること
    runtime._candidates.clear()
    reloaded = runtime.get_candidate(child_id)
    assert reloaded is not None
    assert reloaded.elements["guitar"]["technique"] == "cutting_16th"

    preview_elem = runtime.apply_preview(child_id)
    assert preview_elem["ok"] is True
    devices_elem = preview_elem["summary"]["planned_devices"]
    assert devices_elem["Bass"] == {"device": "Operator", "profile": "bass.slap"}
    assert devices_elem["Guitar"] == {"device": "Operator", "profile": "guitar.cutting_16th"}

    # 実際に OP_SET_DEVICE_PARAMETER が計画に含まれていること
    param_ops = [
        op for op in preview_elem["plan"]["operations"]
        if op["op"] == "set_device_parameter" and op["arguments"].get("device_name") == "Operator"
    ]
    assert len(param_ops) >= 30


def test_http_apply_preview_routes_elements_and_plain_candidates(tmp_path) -> None:
    import threading
    from http.client import HTTPConnection
    from http.server import ThreadingHTTPServer

    from kihachi_mcp.services.reference_library import ReferenceLibrary
    from kihachi_mcp.services.sample_catalog import SampleCatalog
    from kihachi_mcp.studio.app import StudioApp, _handler_for

    runtime, _live = _studio(tmp_path)
    app = StudioApp(
        runtime=runtime,
        reference_library=ReferenceLibrary(tmp_path / "references"),
        sample_catalog=SampleCatalog(tmp_path / "samples.sqlite3"),
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler_for(app))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        plain_id = runtime.generate("Mutation Funk、124 BPM、Gm、16小節", seed=3)["candidate"]["candidate_id"]
        proposal = runtime.propose_composer_adoption(
            plain_id,
            "bass",
            4,
            [{"pitch": 43, "start": 0.0, "duration": 0.25, "velocity": 100}],
            section_name="Drop",
            elements=ELEMENTS_SPEC["elements"],
        )
        elem_id = runtime.decide_revision(proposal["revision"]["revision_id"], True)["selected_candidate_id"]

        def _post_preview(cid: str) -> dict[str, Any]:
            conn = HTTPConnection(host, port, timeout=10)
            conn.request(
                "POST",
                "/api/apply/preview",
                body=json.dumps({"candidate_id": cid}),
                headers={"Content-Type": "application/json"},
            )
            resp = conn.getresponse()
            assert resp.status == 200
            return json.loads(resp.read().decode("utf-8"))

        plain_preview = _post_preview(plain_id)
        assert plain_preview["summary"]["planned_devices"]["Bass"] == {"device": "Analog"}
        assert plain_preview["summary"]["planned_devices"]["Guitar"] == {"device": "Drift"}

        elem_preview = _post_preview(elem_id)
        assert elem_preview["summary"]["planned_devices"]["Bass"] == {
            "device": "Operator",
            "profile": "bass.slap",
        }
        assert elem_preview["summary"]["planned_devices"]["Guitar"] == {
            "device": "Operator",
            "profile": "guitar.cutting_16th",
        }
    finally:
        server.shutdown()
        server.server_close()


def test_guitar_sustain_stabs_prompt_and_backward_compatibility(tmp_path) -> None:
    """sustain_stabs=True 指定時のみ拡張指示が追加され、未指定時は従来と同一になること。"""
    runtime, _live = _studio(tmp_path)
    provider_plain = _CaptureProvider()
    provider_sustain = _CaptureProvider()

    base_guitar = {"technique": "cutting_16th", "muted": True}
    sustain_guitar = {"technique": "cutting_16th", "sustain_stabs": True}

    req_plain = MidiCompositionRequest(
        constraints=MusicalConstraints(
            key="Gm", tempo=124, bars=4, role="guitar", element_spec=base_guitar
        )
    )
    req_sustain = MidiCompositionRequest(
        constraints=MusicalConstraints(
            key="Gm", tempo=124, bars=4, role="guitar", element_spec=sustain_guitar
        )
    )

    ProviderMIDIComposer(provider_plain, max_attempts=1).compose(req_plain)
    ProviderMIDIComposer(provider_sustain, max_attempts=1).compose(req_sustain)

    prompt_plain = provider_plain.prompts[0]
    assert "16th-note funk cutting guitar" in prompt_plain
    assert "Keep muted chops staccato" in prompt_plain
    assert "sustained chord stabs" not in prompt_plain
    assert "at most 12 notes per bar" not in prompt_plain

    prompt_sustain = provider_sustain.prompts[0]
    assert "16th-note funk cutting guitar" in prompt_sustain
    assert "at most 12 notes per bar" in prompt_sustain
    assert "at least 40%" in prompt_sustain
    assert "duration 1.0-2.0 beats" in prompt_sustain
    assert "at most 30%" in prompt_sustain
    assert "duration <= 0.25 beats" in prompt_sustain
    assert "more than 4 consecutive 16th notes" in prompt_sustain
    assert "beats 1 and 3 and their 'and' off-beats" in prompt_sustain
    assert "sustain_stabs=" not in prompt_sustain

    # SongSpec -> runtime.midi_composer_preview 経由でも element_spec に引き継がれること
    songspec = {
        **ELEMENTS_SPEC,
        "elements": {**ELEMENTS_SPEC["elements"], "guitar": sustain_guitar},
    }
    preview = runtime.midi_composer_preview(
        {"key": "Gm", "tempo": 124, "bars": 4, "role": "guitar"},
        songspec=songspec,
        start_bar=9,
    )
    assert preview["ok"] is True
    assert preview["request"]["constraints"]["element_spec"] == sustain_guitar


def test_hats_big_beats_offbeat_velocity_and_open_hat_end_of_bar() -> None:
    """hats の big_beats 指示に裏拍ベロシティ制限と open(46) 小節末限定が含まれること。"""
    provider_hats = _CaptureProvider()
    req_hats = MidiCompositionRequest(
        constraints=MusicalConstraints(
            key="Gm", tempo=124, bars=4, role="hats", element_spec={"style": "big_beats"}
        )
    )
    ProviderMIDIComposer(provider_hats, max_attempts=1).compose(req_hats)
    prompt = provider_hats.prompts[0]
    assert "60% of strong beats" in prompt
    assert "only at the end of a bar" in prompt


def test_guitar_v3_profile_values_in_apply_preview(tmp_path) -> None:
    """guitar.cutting_16th プロファイル v3（Ae Decay=0.55, Ae Sustain=0.15）がプランに反映されること。"""
    runtime, _live = _studio(tmp_path)
    parent_id = runtime.generate("Mutation Funk、124 BPM、Gm、16小節", seed=3)["candidate"][
        "candidate_id"
    ]
    proposal = runtime.propose_composer_adoption(
        parent_id,
        "guitar",
        4,
        [{"pitch": 67, "start": 0.0, "duration": 1.5, "velocity": 100}],
        section_name="Drop",
        elements={
            "guitar": {"technique": "cutting_16th", "chop_pattern": True},
        },
    )
    assert proposal["ok"] is True
    child_id = runtime.decide_revision(proposal["revision"]["revision_id"], True)[
        "selected_candidate_id"
    ]

    preview = runtime.apply_preview(child_id)
    assert preview["ok"] is True
    assert preview["summary"]["planned_devices"]["Guitar"] == {
        "device": "Operator",
        "profile": "guitar.cutting_16th",
    }

    guitar_params = {
        op["arguments"]["parameter_name"]: op["arguments"]
        for op in preview["plan"]["operations"]
        if op["op"] == "set_device_parameter"
        and op["arguments"].get("device_name") == "Operator"
    }
    assert guitar_params["Ae Decay"]["value"] == 0.55
    assert guitar_params["Ae Sustain"]["value"] == 0.15
    # 波形・フィルタ等の他パラメータは変更されていないこと
    assert guitar_params["Osc-A Wave"]["item"] == "Saw 8"
    assert guitar_params["Filter Freq"]["value"] == 0.68


def test_guitar_chop_pattern_prompt_priority_and_backward_compatibility(tmp_path) -> None:
    """chop_pattern 指定時の指示文、sustain_stabs との優先順位、未指定時の後方互換性を検証する。"""
    runtime, _live = _studio(tmp_path)
    provider_plain = _CaptureProvider()
    provider_chop = _CaptureProvider()
    provider_both = _CaptureProvider()

    plain_guitar = {"technique": "cutting_16th", "muted": True}
    chop_guitar = {"technique": "cutting_16th", "chop_pattern": True}
    both_guitar = {"technique": "cutting_16th", "sustain_stabs": True, "chop_pattern": True}

    for prov, spec in (
        (provider_plain, plain_guitar),
        (provider_chop, chop_guitar),
        (provider_both, both_guitar),
    ):
        req = MidiCompositionRequest(
            constraints=MusicalConstraints(
                key="Gm", tempo=124, bars=4, role="guitar", element_spec=spec
            )
        )
        ProviderMIDIComposer(prov, max_attempts=1).compose(req)

    prompt_plain = provider_plain.prompts[0]
    assert "16th-note funk cutting guitar" in prompt_plain
    assert "Keep muted chops staccato" in prompt_plain
    assert "chop-focused clustered" not in prompt_plain
    assert "at most 12 notes per bar" not in prompt_plain

    prompt_chop = provider_chop.prompts[0]
    assert "16th-note funk cutting guitar" in prompt_chop
    assert "at most 12 notes per bar" in prompt_chop
    assert "around 70%" in prompt_chop
    assert "duration <= 0.25 beats" in prompt_chop
    assert "clusters of 2 to 4 notes" in prompt_chop
    assert "16th-note rest between clusters" in prompt_chop
    assert "at most 1 sustained stab (duration 1.0-2.0 beats) per bar, placed only on beat 1" in prompt_chop
    assert "start of each cluster and on strong beats" in prompt_chop
    assert "chop_pattern=" not in prompt_chop

    # sustain_stabs と同時指定時は chop_pattern を優先し、sustain_stabs の指示や未消費キーが残らないこと
    prompt_both = provider_both.prompts[0]
    assert "around 70%" in prompt_both
    assert "clusters of 2 to 4 notes" in prompt_both
    assert "at least 40%" not in prompt_both
    assert "sustain_stabs=" not in prompt_both
    assert "chop_pattern=" not in prompt_both

    # SongSpec -> runtime.midi_composer_preview 経由でも chop_pattern が引き継がれること
    songspec = {
        **ELEMENTS_SPEC,
        "elements": {**ELEMENTS_SPEC["elements"], "guitar": chop_guitar},
    }
    preview = runtime.midi_composer_preview(
        {"key": "Gm", "tempo": 124, "bars": 4, "role": "guitar"},
        songspec=songspec,
        start_bar=9,
    )
    assert preview["ok"] is True
    assert preview["request"]["constraints"]["element_spec"] == chop_guitar


def test_arp_sparse_prompt_and_backward_compatibility(tmp_path) -> None:
    """elements.arp style='sparse' 指定時の指示文と未指定時の後方互換性を検証する。"""
    runtime, _live = _studio(tmp_path)
    provider_plain = _CaptureProvider()
    provider_sparse = _CaptureProvider()

    req_plain = MidiCompositionRequest(
        constraints=MusicalConstraints(key="Gm", tempo=124, bars=4, role="arp")
    )
    req_sparse = MidiCompositionRequest(
        constraints=MusicalConstraints(
            key="Gm",
            tempo=124,
            bars=4,
            role="arp",
            element_spec={"style": "sparse"},
        )
    )

    ProviderMIDIComposer(provider_plain, max_attempts=1).compose(req_plain)
    ProviderMIDIComposer(provider_sparse, max_attempts=1).compose(req_sparse)

    prompt_plain = provider_plain.prompts[0]
    assert "sparse, supportive arpeggio" not in prompt_plain
    assert "at most 8 notes per bar" not in prompt_plain

    prompt_sparse = provider_sparse.prompts[0]
    assert "sparse, supportive arpeggio" in prompt_sparse
    assert "at most 8 notes per bar" in prompt_sparse
    assert "at most 4 consecutive 16th notes, followed by at least an 8th-note rest" in prompt_sparse
    assert "octave-or-wider leaps to at most 2 per bar" in prompt_sparse
    assert "trace chord tones only without turning into a prominent standalone melody" in prompt_sparse
    assert "style=" not in prompt_sparse

    # SongSpec 経由で elements.arp 未指定時は従来通り element_spec が付かないこと
    preview_plain = runtime.midi_composer_preview(
        {"key": "Gm", "tempo": 124, "bars": 4, "role": "arp"},
        songspec=ELEMENTS_SPEC,
        start_bar=5,
    )
    assert preview_plain["ok"] is True
    assert "element_spec" not in preview_plain["request"]["constraints"]

    # SongSpec 経由で elements.arp = {"style": "sparse"} 指定時は引き継がれること
    sparse_spec = {
        **ELEMENTS_SPEC,
        "elements": {**ELEMENTS_SPEC["elements"], "arp": {"style": "sparse"}},
    }
    preview_sparse = runtime.midi_composer_preview(
        {"key": "Gm", "tempo": 124, "bars": 4, "role": "arp"},
        songspec=sparse_spec,
        start_bar=5,
    )
    assert preview_sparse["ok"] is True
    assert preview_sparse["request"]["constraints"]["element_spec"] == {"style": "sparse"}


def test_songspec_arrangement_roundtrip_and_backward_compatibility(tmp_path) -> None:
    """arrangement の to_dict/from_dict 往復と未指定時の後方互換（バイト同一性）を検証する。"""
    plain_spec = {k: v for k, v in ELEMENTS_SPEC.items() if k != "elements"}
    parsed_plain = SongSpec.from_dict(plain_spec)
    assert parsed_plain.arrangement == {}
    assert "arrangement" not in parsed_plain.to_dict()

    arr_data = {
        "activity": {"Intro": ["bass", "chords"], "Drop": ["bass", "guitar", "horn"]},
        "density": {"arp": 4, "bass": 10},
        "call_response": [{"call": "guitar", "response": "horn", "sections": ["Drop", "Outro"]}],
    }
    arr_spec = {**plain_spec, "arrangement": arr_data}
    parsed_arr = SongSpec.from_dict(arr_spec)
    assert parsed_arr.arrangement == arr_data
    assert parsed_arr.to_dict()["arrangement"] == arr_data

    runtime, _live = _studio(tmp_path)
    preview_plain = runtime.midi_composer_preview(
        {"key": "Gm", "tempo": 124, "bars": 4, "role": "bass"},
        songspec=plain_spec,
        start_bar=1,
    )
    preview_empty_arr = runtime.midi_composer_preview(
        {"key": "Gm", "tempo": 124, "bars": 4, "role": "bass"},
        songspec={**plain_spec, "arrangement": {}},
        start_bar=1,
    )
    assert "max_notes_per_bar" not in preview_plain["request"]["constraints"]
    assert "call_response_role" not in preview_plain["request"]["constraints"]
    assert json.dumps(preview_plain["request"], sort_keys=True) == json.dumps(
        preview_empty_arr["request"], sort_keys=True
    )


def test_phase_v_driver_build_cells_from_activity() -> None:
    """phase_v_driver.build_cells が activity からセルを導出し、未掲載セクションは従来通り全 role 有効になること。"""
    import importlib.util
    from pathlib import Path

    driver_path = Path(__file__).resolve().parents[2] / "tasks" / "phase_v_driver.py"
    spec_mod = importlib.util.spec_from_file_location("phase_v_driver", driver_path)
    assert spec_mod is not None and spec_mod.loader is not None
    driver = importlib.util.module_from_spec(spec_mod)
    spec_mod.loader.exec_module(driver)

    # 未指定時は従来の17セル
    plain_cells = driver.build_cells({"key": "Gm", "tempo": 124})
    assert len(plain_cells) == 17

    # Intro と Drop のみ activity 指定（Kick は非 compose role なので無視）、Build/Outro は未掲載（従来通り）
    act_spec = {
        "arrangement": {
            "activity": {
                "Intro": ["bass", "chords", "kick"],
                "Drop": ["bass", "guitar", "horn"],
            }
        }
    }
    derived = driver.build_cells(act_spec)
    intro_roles = [r for s, r in derived if s == "Intro"]
    build_roles = [r for s, r in derived if s == "Build"]
    drop_roles = [r for s, r in derived if s == "Drop"]
    outro_roles = [r for s, r in derived if s == "Outro"]

    assert intro_roles == ["bass", "chords"]
    assert build_roles == ["bass", "chords", "melody", "arp", "percussion"]
    assert drop_roles == ["bass", "guitar", "horn"]
    assert outro_roles == ["bass", "chords", "melody", "percussion"]

    # compose_cells が明示されている場合はそちらが優先される
    override_spec = {**act_spec, "compose_cells": {"guitar": ["Outro"]}}
    override_cells = driver.build_cells(override_spec)
    assert ("Outro", "guitar") in override_cells
    assert ("Drop", "guitar") not in override_cells


def test_arrangement_density_and_call_response_prompts(tmp_path) -> None:
    """density（elements 併用時の最小値採用）と call_response のプロンプト差し込みを検証する。"""
    runtime, _live = _studio(tmp_path)
    arr_spec = {
        **ELEMENTS_SPEC,
        "elements": {
            **ELEMENTS_SPEC["elements"],
            "arp": {"style": "sparse"},
            "percussion": {"style": "big_beats"},
        },
        "arrangement": {
            "density": {"arp": 4, "percussion": 6, "bass": 10},
            "call_response": [
                {"call": "guitar", "response": "horn", "sections": ["Drop"]},
            ],
        },
    }

    # 1. density: arp (elem=8, density=4 -> min=4), percussion (elem=4, density=6 -> min=4), bass (elem=None, density=10 -> 10)
    for role, expected_max, expected_limit in (
        ("arp", 4, 4),
        ("percussion", 6, 4),
        ("bass", 10, 10),
    ):
        preview = runtime.midi_composer_preview(
            {"key": "Gm", "tempo": 124, "bars": 4, "role": role},
            songspec=arr_spec,
            start_bar=5,
        )
        assert preview["ok"] is True
        assert preview["request"]["constraints"]["max_notes_per_bar"] == expected_max
        prov = _CaptureProvider()
        req = MidiCompositionRequest(
            constraints=MusicalConstraints.from_dict(preview["request"]["constraints"])
        )
        ProviderMIDIComposer(prov, max_attempts=1).compose(req)
        assert f"at most {expected_limit} notes per bar" in prov.prompts[0]

    # 2. call_response: Drop では guitar=call, horn=response。Build では対象外
    preview_call = runtime.midi_composer_preview(
        {"key": "Gm", "tempo": 124, "bars": 4, "role": "guitar"},
        songspec=arr_spec,
        start_bar=9,
    )
    preview_resp = runtime.midi_composer_preview(
        {"key": "Gm", "tempo": 124, "bars": 4, "role": "horn"},
        songspec=arr_spec,
        start_bar=9,
    )
    preview_outside = runtime.midi_composer_preview(
        {"key": "Gm", "tempo": 124, "bars": 4, "role": "guitar"},
        songspec=arr_spec,
        start_bar=5,
    )
    assert preview_call["request"]["constraints"]["call_response_role"] == "call"
    assert preview_resp["request"]["constraints"]["call_response_role"] == "response"
    assert "call_response_role" not in preview_outside["request"]["constraints"]

    prov_call = _CaptureProvider()
    ProviderMIDIComposer(prov_call, max_attempts=1).compose(
        MidiCompositionRequest(
            constraints=MusicalConstraints.from_dict(preview_call["request"]["constraints"])
        )
    )
    assert "place phrases on beats 1-2 of each bar and rest on beats 3-4" in prov_call.prompts[0]

    prov_resp = _CaptureProvider()
    ProviderMIDIComposer(prov_resp, max_attempts=1).compose(
        MidiCompositionRequest(
            constraints=MusicalConstraints.from_dict(preview_resp["request"]["constraints"])
        )
    )
    assert "respond briefly only on beats 3-4 of each bar and rest on beats 1-2" in prov_resp.prompts[0]

    prov_outside = _CaptureProvider()
    ProviderMIDIComposer(prov_outside, max_attempts=1).compose(
        MidiCompositionRequest(
            constraints=MusicalConstraints.from_dict(preview_outside["request"]["constraints"])
        )
    )
    assert "Call-and-response" not in prov_outside.prompts[0]


def test_arrangement_activity_clears_unlisted_compose_roles_and_backward_compatibility(
    tmp_path,
) -> None:
    """arrangement.activity 指定セクションで未列挙 compose role のみ 0 音化し、列挙 role・土台・未掲載セクションは不変であること。"""
    runtime, _live = _studio(tmp_path)
    parent_id = runtime.generate("Mutation Funk、124 BPM、Gm、16小節", seed=3)["candidate"][
        "candidate_id"
    ]
    parent_cand = runtime.get_candidate(parent_id)
    assert parent_cand is not None

    parent_by_slot = {(c.part, c.section_name): c for c in parent_cand.clips}
    new_bass_notes = [{"pitch": 43, "start": 0.0, "duration": 0.25, "velocity": 100}]

    # 1. arrangement 未指定時は従来どおり他パート完全不変
    plain_prop = runtime.propose_composer_adoption(
        parent_id,
        "bass",
        4,
        new_bass_notes,
        section_name="Drop",
    )
    assert plain_prop["ok"] is True
    assert plain_prop["other_parts_unchanged"] is True
    plain_child_id = runtime.decide_revision(plain_prop["revision"]["revision_id"], True)[
        "selected_candidate_id"
    ]
    plain_child = runtime.get_candidate(plain_child_id)
    assert plain_child is not None
    for clip in plain_child.clips:
        if (clip.part, clip.section_name) != ("Bass", "Drop"):
            assert clip == parent_by_slot[(clip.part, clip.section_name)]

    # 2. Drop に activity=["bass", "chords"] を指定して bass を採用
    arr = {"activity": {"Drop": ["bass", "chords"]}}
    arr_prop = runtime.propose_composer_adoption(
        parent_id,
        "bass",
        4,
        new_bass_notes,
        section_name="Drop",
        arrangement=arr,
    )
    assert arr_prop["ok"] is True
    arr_child_id = runtime.decide_revision(arr_prop["revision"]["revision_id"], True)[
        "selected_candidate_id"
    ]
    arr_child = runtime.get_candidate(arr_child_id)
    assert arr_child is not None

    # スロット数・構成自体は維持される（クリップを削除せず note_count=0 にする）
    assert [(c.part, c.section_name) for c in arr_child.clips] == [
        (c.part, c.section_name) for c in parent_cand.clips
    ]

    compose_parts = {"Bass", "Stab", "Lead", "Arp", "Perc", "Guitar", "Horn", "Vocal", "Hats"}
    active_drop_parts = {"Bass", "Stab"}
    cleared_checked = 0
    foundation_checked = 0

    for clip in arr_child.clips:
        orig = parent_by_slot[(clip.part, clip.section_name)]
        if clip.section_name != "Drop":
            # activity に載っていないセクションは完全に不変
            assert clip == orig
        elif clip.part == "Bass":
            # 採用した role は新しいノートに置換
            assert len(clip.notes) > 0
            assert clip != orig
        elif clip.part in active_drop_parts:
            # 列挙されている role（chords=Stab）は不変
            assert clip == orig
        elif clip.part in compose_parts:
            # activity に列挙されていない compose role は 0 音化（スロット維持）
            assert len(clip.notes) == 0
            assert clip.to_dict()["note_count"] == 0
            cleared_checked += 1
        else:
            # compose role でない土台パート（Kick/Snare/Sub/Pad/FX 等）は不変
            assert clip == orig
            foundation_checked += 1

    assert cleared_checked >= 1
    assert foundation_checked >= 1


def test_arrangement_mix_roundtrip_clamp_and_backward_compatibility(tmp_path) -> None:
    """arrangement.mix の往復・クランプ(-24〜+12)・未指定時の後方互換を検証する。"""
    plain_spec = {k: v for k, v in ELEMENTS_SPEC.items() if k != "elements"}
    mix_spec = {
        **plain_spec,
        "arrangement": {
            "mix": {"Pad": -6.0, "Bass": 0.0, "TooLow": -30.0, "TooHigh": 20.0}
        },
    }
    parsed = SongSpec.from_dict(mix_spec)
    assert parsed.arrangement["mix"] == {
        "Pad": -6.0,
        "Bass": 0.0,
        "TooLow": -24.0,
        "TooHigh": 12.0,
    }
    assert parsed.to_dict()["arrangement"]["mix"] == parsed.arrangement["mix"]

    # MidiCandidate への保持・往復と未指定時後方互換
    runtime, _live = _studio(tmp_path)
    parent_id = runtime.generate("Mutation Funk、124 BPM、Gm、16小節", seed=5)["candidate"][
        "candidate_id"
    ]
    parent = runtime.get_candidate(parent_id)
    assert parent is not None
    assert parent.mix == {}
    assert "mix" not in parent.to_dict()

    preview_plain = runtime.apply_preview(parent_id)
    plain_ops = [op["op"] for op in preview_plain["plan"]["operations"]]
    assert "set_track_volume" not in plain_ops


def test_arrangement_mix_plan_receipt_and_unknown_part_warning(tmp_path) -> None:
    """mix 保持時の set_track_volume op 生成・レシート記録・不正パート名警告を検証する。"""
    from kihachi_mcp.models.midi_candidate import MidiCandidate
    from kihachi_mcp.services.candidate_live_planner import db_to_live_volume

    runtime, _live = _studio(tmp_path)
    parent_id = runtime.generate("Mutation Funk、124 BPM、Gm、16小節", seed=6)["candidate"][
        "candidate_id"
    ]
    new_bass_notes = [{"pitch": 43, "start": 0.0, "duration": 0.25, "velocity": 100}]

    prop = runtime.propose_composer_adoption(
        parent_id,
        "bass",
        4,
        new_bass_notes,
        section_name="Drop",
        arrangement={"mix": {"Pad": -6.0, "Kick": 3.0, "UnknownPart": -3.0}},
    )
    assert prop["ok"] is True
    child_id = runtime.decide_revision(prop["revision"]["revision_id"], True)[
        "selected_candidate_id"
    ]
    child = runtime.get_candidate(child_id)
    assert child is not None
    assert child.mix == {"Pad": -6.0, "Kick": 3.0, "UnknownPart": -3.0}

    # MidiCandidate の to_dict / from_dict 往復
    roundtrip = MidiCandidate.from_dict(child.to_dict())
    assert roundtrip.mix == child.mix

    # apply_preview に set_track_volume op と不正パート名警告が含まれること
    preview = runtime.apply_preview(child_id)
    assert preview["ok"] is True
    vol_ops = [
        op for op in preview["plan"]["operations"] if op["op"] == "set_track_volume"
    ]
    assert {op["arguments"]["part"]: op["arguments"]["volume_db"] for op in vol_ops} == {
        "Pad": -6.0,
        "Kick": 3.0,
    }
    for op in vol_ops:
        expected_vol = round(db_to_live_volume(op["arguments"]["volume_db"]), 6)
        assert abs(op["arguments"]["volume"] - expected_vol) < 1e-6
        assert abs(op["expected_readback"]["volume"] - expected_vol) < 1e-6

    assert any("UnknownPart" in w for w in preview["warnings"])

    # apply 実行時のレシート（track_volumes）記録を検証
    calls: list[tuple[str, dict[str, Any]]] = []

    def fake_remote(command: str, **params: Any) -> dict[str, Any]:
        calls.append((command, params))
        if command == "set_track_volume":
            return {"track_index": params["track_index"], "volume": params["volume"]}
        return {}

    runtime._executor._plugin_remote = fake_remote
    applied = runtime.apply(child_id, confirmed=True)
    assert applied["ok"] is True
    assert len(applied["track_volumes"]) == 2
    by_part = {row["part"]: row for row in applied["track_volumes"]}
    assert by_part["Pad"]["target_db"] == -6.0
    assert abs(by_part["Pad"]["volume"] - round(db_to_live_volume(-6.0), 6)) < 1e-6
    assert by_part["Kick"]["target_db"] == 3.0
    assert abs(by_part["Kick"]["volume"] - round(db_to_live_volume(3.0), 6)) < 1e-6
    assert any("UnknownPart" in w for w in applied["warnings"])
    assert len([c for c in calls if c[0] == "set_track_volume"]) == 2


def test_melody_motif_prompt_and_backward_compatibility(tmp_path) -> None:
    """elements.melody.motif=True 時の指示差し込みと未指定/False 時の後方互換を検証する。"""
    provider_plain = _CaptureProvider()
    provider_false = _CaptureProvider()
    provider_motif = _CaptureProvider()

    base_kwargs = {
        "key": "Gm",
        "tempo": 124,
        "bars": 4,
        "role": "melody",
        "section_name": "Drop",
        "section_energy": 0.95,
    }
    req_plain = MidiCompositionRequest(constraints=MusicalConstraints(**base_kwargs))
    req_false = MidiCompositionRequest(
        constraints=MusicalConstraints(**base_kwargs, element_spec={"motif": False})
    )
    req_motif = MidiCompositionRequest(
        constraints=MusicalConstraints(**base_kwargs, element_spec={"motif": True})
    )

    ProviderMIDIComposer(provider_plain, max_attempts=1).compose(req_plain)
    ProviderMIDIComposer(provider_false, max_attempts=1).compose(req_false)
    ProviderMIDIComposer(provider_motif, max_attempts=1).compose(req_motif)

    prompt_plain = provider_plain.prompts[0]
    prompt_false = provider_false.prompts[0]
    prompt_motif = provider_motif.prompts[0]

    assert "2-4 note motif" not in prompt_plain
    assert "at least 70% chord tones" not in prompt_plain
    assert "2-4 note motif" not in prompt_false
    assert "motif=" not in prompt_false

    assert "2-4 note motif" in prompt_motif
    assert "do not invent new material in every bar" in prompt_motif
    assert "at least 70% chord tones" in prompt_motif
    assert ">= 5 semitones" in prompt_motif
    assert "stepwise motion (1-2 semitones)" in prompt_motif
    assert "2-bar units" in prompt_motif
    assert "rest at the end of each 2-bar phrase" in prompt_motif
    assert "motif=" not in prompt_motif

    # SongSpec 経由で未指定・motif=False・motif=True のリクエスト互換性を検証
    runtime, _live = _studio(tmp_path)
    preview_plain = runtime.midi_composer_preview(
        {"key": "Gm", "tempo": 124, "bars": 4, "role": "melody"},
        songspec=ELEMENTS_SPEC,
        start_bar=9,
    )
    false_spec = {
        **ELEMENTS_SPEC,
        "elements": {**ELEMENTS_SPEC["elements"], "melody": {"motif": False}},
    }
    preview_false = runtime.midi_composer_preview(
        {"key": "Gm", "tempo": 124, "bars": 4, "role": "melody"},
        songspec=false_spec,
        start_bar=9,
    )
    motif_spec = {
        **ELEMENTS_SPEC,
        "elements": {**ELEMENTS_SPEC["elements"], "melody": {"motif": True}},
    }
    preview_motif = runtime.midi_composer_preview(
        {"key": "Gm", "tempo": 124, "bars": 4, "role": "melody"},
        songspec=motif_spec,
        start_bar=9,
    )

    assert preview_plain["ok"] is True
    assert preview_false["ok"] is True
    assert preview_motif["ok"] is True
    assert "element_spec" not in preview_plain["request"]["constraints"]
    assert json.dumps(preview_false["request"], sort_keys=True) == json.dumps(
        preview_plain["request"], sort_keys=True
    )
    assert preview_motif["request"]["constraints"]["element_spec"] == {"motif": True}


def test_section_elements_merge_and_backward_compatibility(tmp_path) -> None:
    """SongSpec.section_elements のラウンドトリップ、上書き合成、対象セクション限定適用を検証する。"""
    plain_spec = SongSpec.from_dict(ELEMENTS_SPEC)
    assert plain_spec.section_elements == {}
    assert "section_elements" not in plain_spec.to_dict()

    spec_with_sections = {
        **ELEMENTS_SPEC,
        "section_elements": {
            "Intro": {"bass": {"figure": "entrance_hit"}},
            "Outro": {"bass": {"figure": "sparse_calls", "ending": "rise", "technique": "finger"}},
        },
    }
    parsed = SongSpec.from_dict(spec_with_sections)
    assert parsed.section_elements["Intro"]["bass"] == {"figure": "entrance_hit"}
    assert parsed.to_dict()["section_elements"] == spec_with_sections["section_elements"]

    runtime, _live = _studio(tmp_path)
    # section_elements 未指定時と、section_elements 対象外セクション（Drop, start_bar=9）は従来と同一リクエスト
    preview_drop_plain = runtime.midi_composer_preview(
        {"key": "Gm", "tempo": 124, "bars": 4, "role": "bass"},
        songspec=ELEMENTS_SPEC,
        start_bar=9,
    )
    preview_drop_with = runtime.midi_composer_preview(
        {"key": "Gm", "tempo": 124, "bars": 4, "role": "bass"},
        songspec=spec_with_sections,
        start_bar=9,
    )
    assert json.dumps(preview_drop_with["request"], sort_keys=True) == json.dumps(
        preview_drop_plain["request"], sort_keys=True
    )

    # Intro (start_bar=1): elements.bass を継承しつつ figure="entrance_hit" が上書き合成される
    preview_intro = runtime.midi_composer_preview(
        {"key": "Gm", "tempo": 124, "bars": 4, "role": "bass"},
        songspec=spec_with_sections,
        start_bar=1,
    )
    assert preview_intro["request"]["constraints"]["element_spec"] == {
        "technique": "slap",
        "ghost_notes": True,
        "octave_pops": True,
        "figure": "entrance_hit",
    }

    # Outro (start_bar=13): technique は section_elements 側("finger")が優先される
    preview_outro = runtime.midi_composer_preview(
        {"key": "Gm", "tempo": 124, "bars": 4, "role": "bass"},
        songspec=spec_with_sections,
        start_bar=13,
    )
    assert preview_outro["request"]["constraints"]["element_spec"] == {
        "technique": "finger",
        "ghost_notes": True,
        "octave_pops": True,
        "figure": "sparse_calls",
        "ending": "rise",
    }

    # 対象外パート（Intro × guitar）には bass の section_elements は入らない
    preview_intro_guitar = runtime.midi_composer_preview(
        {"key": "Gm", "tempo": 124, "bars": 4, "role": "guitar"},
        songspec=spec_with_sections,
        start_bar=1,
    )
    assert preview_intro_guitar["request"]["constraints"]["element_spec"] == {
        "technique": "cutting_16th",
        "muted": True,
    }

    # ProviderMIDIComposer へ渡した場合も Intro のみに figure 指示が入り、Drop には入らない
    provider_intro = _CaptureProvider()
    provider_drop = _CaptureProvider()
    ProviderMIDIComposer(provider_intro, max_attempts=1).compose(
        MidiCompositionRequest(
            constraints=MusicalConstraints.from_dict(preview_intro["request"]["constraints"])
        )
    )
    ProviderMIDIComposer(provider_drop, max_attempts=1).compose(
        MidiCompositionRequest(
            constraints=MusicalConstraints.from_dict(preview_drop_with["request"]["constraints"])
        )
    )
    assert "Bass figure (entrance_hit)" in provider_intro.prompts[0]
    assert "Bass figure" not in provider_drop.prompts[0]
    assert "Bass ending" not in provider_drop.prompts[0]


def test_bass_figure_ending_and_melody_phrasing_prompts() -> None:
    """elements.bass の figure 3種・ending='rise' と elements.melody.phrasing='contrast' を検証する。"""
    # Bass 未指定時のベースラインプロンプト
    provider_bass_plain = _CaptureProvider()
    req_bass_plain = MidiCompositionRequest(
        constraints=MusicalConstraints(
            key="Gm", tempo=124, bars=4, role="bass", section_name="Intro", section_energy=0.4
        )
    )
    ProviderMIDIComposer(provider_bass_plain, max_attempts=1).compose(req_bass_plain)
    plain_bass_prompt = provider_bass_plain.prompts[0]
    assert "Bass figure" not in plain_bass_prompt
    assert "Bass ending" not in plain_bass_prompt

    # figure 3種
    expected_figure_phrases = {
        "entrance_hit": ("Bass figure (entrance_hit)", "beat 1 of the section", "at most 2 additional notes"),
        "hit_and_dots": ("Bass figure (hit_and_dots)", "beat 1", "2 short notes"),
        "sparse_calls": ("Bass figure (sparse_calls)", "2-bar unit", "2 short notes"),
    }
    for fig, tokens in expected_figure_phrases.items():
        provider = _CaptureProvider()
        req = MidiCompositionRequest(
            constraints=MusicalConstraints(
                key="Gm",
                tempo=124,
                bars=4,
                role="bass",
                section_name="Intro",
                section_energy=0.4,
                element_spec={"figure": fig},
            )
        )
        ProviderMIDIComposer(provider, max_attempts=1).compose(req)
        prompt = provider.prompts[0]
        for token in tokens:
            assert token in prompt
        assert "figure=" not in prompt

    # ending: "rise"
    provider_ending = _CaptureProvider()
    req_ending = MidiCompositionRequest(
        constraints=MusicalConstraints(
            key="Gm",
            tempo=124,
            bars=4,
            role="bass",
            section_name="Build",
            section_energy=0.65,
            element_spec={"ending": "rise"},
        )
    )
    ProviderMIDIComposer(provider_ending, max_attempts=1).compose(req_ending)
    ending_prompt = provider_ending.prompts[0]
    assert "Bass ending (rise)" in ending_prompt
    assert "final bar ONLY" in ending_prompt
    assert "eighth-note offbeats" in ending_prompt
    assert "beats 1.5, 2.5, 3.5, and 4.5" in ending_prompt
    assert "on-beats rested" in ending_prompt
    assert "ascending in pitch through chord tones" in ending_prompt
    assert "place no other notes in that bar" in ending_prompt
    assert "ending=" not in ending_prompt

    # melody phrasing: "contrast"（単独および motif=True 併用）
    provider_melody_plain = _CaptureProvider()
    req_melody_plain = MidiCompositionRequest(
        constraints=MusicalConstraints(
            key="Gm", tempo=124, bars=4, role="melody", section_name="Drop", section_energy=0.95
        )
    )
    ProviderMIDIComposer(provider_melody_plain, max_attempts=1).compose(req_melody_plain)
    assert "Melodic phrasing" not in provider_melody_plain.prompts[0]

    provider_contrast = _CaptureProvider()
    req_contrast = MidiCompositionRequest(
        constraints=MusicalConstraints(
            key="Gm",
            tempo=124,
            bars=4,
            role="melody",
            section_name="Drop",
            section_energy=0.95,
            element_spec={"motif": True, "phrasing": "contrast"},
        )
    )
    ProviderMIDIComposer(provider_contrast, max_attempts=1).compose(req_contrast)
    contrast_prompt = provider_contrast.prompts[0]
    assert "2-4 note motif" in contrast_prompt
    assert "Melodic phrasing (contrast)" in contrast_prompt
    assert "at least 2 sustained notes" in contrast_prompt
    assert "always include rests" in contrast_prompt
    assert "at most 4 notes in a row" in contrast_prompt
    assert "phrasing=" not in contrast_prompt


def test_songspec_arrangement_repeat_roundtrip() -> None:
    """arrangement.repeat の from_dict / to_dict 往復と不正入力の拒否を検証する。"""
    import pytest

    base_spec = {k: v for k, v in ELEMENTS_SPEC.items() if k != "elements"}
    rep_spec = {
        **base_spec,
        "arrangement": {
            "repeat": {
                "Outro2": {"from": "Outro", "regenerate": ["Bass", ""]},
            }
        },
    }
    parsed = SongSpec.from_dict(rep_spec)
    assert parsed.arrangement == {
        "repeat": {
            "Outro2": {"from": "Outro", "regenerate": ["bass"]},
        }
    }
    assert parsed.to_dict()["arrangement"] == parsed.arrangement

    with pytest.raises(ValueError):
        SongSpec.from_dict({**base_spec, "arrangement": {"repeat": {"Outro2": {"from": ""}}}})
    with pytest.raises(TypeError):
        SongSpec.from_dict(
            {**base_spec, "arrangement": {"repeat": {"Outro2": {"from": "Outro", "regenerate": "bass"}}}}
        )


def test_bass_figure_sparse_hits_prompt_and_slide_down_excluded() -> None:
    """figure='sparse_hits' のプロンプト指示と slide_down が LLM プロンプトに出ないことを検証する。"""
    provider = _CaptureProvider()
    req = MidiCompositionRequest(
        constraints=MusicalConstraints(
            key="Gm",
            tempo=124,
            bars=4,
            role="bass",
            section_name="Intro",
            section_energy=0.4,
            element_spec={"figure": "sparse_hits", "slide_down": True},
        )
    )
    ProviderMIDIComposer(provider, max_attempts=1).compose(req)
    prompt = provider.prompts[0]
    assert "Bass figure (sparse_hits)" in prompt
    assert "4-bar section" in prompt
    assert "beat 1 (start 0.0)" in prompt
    assert "beat 9 (start 8.0)" in prompt
    assert "duration >= 1.5 beats" in prompt
    assert "velocity >= 100" in prompt
    assert "beat 15 (start 14.0" in prompt
    assert "beat 16" in prompt
    assert "figure=" not in prompt
    assert "slide_down=" not in prompt
    assert "Additional 'bass' element constraints" not in prompt


def test_bass_slide_down_keyswitch_deterministic_injection(tmp_path) -> None:
    """section_elements 経由を含め、slide_down 有効セクションの Bass 対象ノートにのみ pitch 29 が追加されることを検証する。"""
    runtime, _live = _studio(tmp_path)
    parent_id = runtime.generate("Mutation Funk、124 BPM、Gm、16小節", seed=3)["candidate"]["candidate_id"]

    bass_notes = [
        {"pitch": 43, "start": 0.0, "duration": 2.0, "velocity": 110},   # 対象: 小節頭 & vel>=100
        {"pitch": 43, "start": 4.0, "duration": 1.0, "velocity": 99},    # 非対象: vel<100
        {"pitch": 43, "start": 8.0, "duration": 1.5, "velocity": 100},   # 対象: 小節頭 & vel==100
        {"pitch": 43, "start": 14.0, "duration": 0.5, "velocity": 110},  # 非対象: 小節頭でない
        {"pitch": 43, "start": 15.0, "duration": 1.0, "velocity": 115},  # 非対象: 小節頭でない
    ]
    section_elements = {"Intro": {"bass": {"figure": "sparse_hits", "slide_down": True}}}

    # 1. slide_down なし（後方互換ベースライン）
    prop_plain = runtime.propose_composer_adoption(
        parent_id, "bass", 4, bass_notes, section_name="Intro"
    )
    assert prop_plain["ok"] is True
    plain_id = runtime.decide_revision(prop_plain["revision"]["revision_id"], True)["selected_candidate_id"]
    plain_cand = runtime.get_candidate(plain_id)
    assert plain_cand is not None
    plain_intro = next(c for c in plain_cand.clips_for_part("Bass") if c.section_name == "Intro")
    assert len(plain_intro.notes) == 5
    assert all(n.pitch == 43 for n in plain_intro.notes)

    # 2. section_elements で Intro のみ slide_down=True
    prop_intro = runtime.propose_composer_adoption(
        parent_id,
        "bass",
        4,
        bass_notes,
        section_name="Intro",
        elements=ELEMENTS_SPEC["elements"],
        section_elements=section_elements,
    )
    assert prop_intro["ok"] is True
    intro_id = runtime.decide_revision(prop_intro["revision"]["revision_id"], True)["selected_candidate_id"]
    intro_cand = runtime.get_candidate(intro_id)
    assert intro_cand is not None
    intro_clip = next(c for c in intro_cand.clips_for_part("Bass") if c.section_name == "Intro")

    keyswitches = [n for n in intro_clip.notes if n.pitch == 29]
    assert [(n.start_beats, n.duration_beats, n.velocity) for n in keyswitches] == [
        (1.0, 1.0, 110),
        (8.75, 0.75, 100),
    ]
    assert len(intro_clip.notes) == 7
    assert intro_cand.note_fingerprint != plain_cand.note_fingerprint
    assert intro_cand.content_hash != plain_cand.content_hash

    # 3. slide_down 無効な他セクション (Drop) には pitch 29 が追加されない
    prop_drop = runtime.propose_composer_adoption(
        intro_id,
        "bass",
        4,
        bass_notes,
        section_name="Drop",
        elements=ELEMENTS_SPEC["elements"],
        section_elements=section_elements,
    )
    assert prop_drop["ok"] is True
    drop_id = runtime.decide_revision(prop_drop["revision"]["revision_id"], True)["selected_candidate_id"]
    drop_cand = runtime.get_candidate(drop_id)
    assert drop_cand is not None
    drop_clip = next(c for c in drop_cand.clips_for_part("Bass") if c.section_name == "Drop")
    assert len(drop_clip.notes) == 5
    assert all(n.pitch == 43 for n in drop_clip.notes)


def test_bass_ezbass_plugin_remaps_reserved_pitches_before_keyswitch_injection(tmp_path) -> None:
    """EZbass ルーティング時に予約集合の音楽ノートをオクターブ上（+12）へ移し、キースイッチ挿入分 29 は残すことを検証する。"""
    runtime, _live = _studio(tmp_path)
    parent_id = runtime.generate("Mutation Funk、124 BPM、Gm、16小節", seed=3)["candidate"]["candidate_id"]

    bass_notes = [
        {"pitch": 29, "start": 0.0, "duration": 2.0, "velocity": 110},
        {"pitch": 43, "start": 8.0, "duration": 1.0, "velocity": 95},
        {"pitch": 29, "start": 14.0, "duration": 0.5, "velocity": 90},
    ]
    ezbass_elements = {
        "bass": {
            "instrument": "plugin",
            "source": "raw",
            "name": "EZbass",
            "path": ["VST3", "Toontrack"],
        }
    }
    section_elements = {"Intro": {"bass": {"slide_down": True}}}

    # 1. EZbass ルーティング + slide_down 無効セクション (Outro) -> pitch 29 が 41 (+12) に移る
    prop_outro = runtime.propose_composer_adoption(
        parent_id,
        "bass",
        4,
        bass_notes,
        section_name="Outro",
        elements=ezbass_elements,
        section_elements=section_elements,
    )
    assert prop_outro["ok"] is True
    outro_id = runtime.decide_revision(prop_outro["revision"]["revision_id"], True)["selected_candidate_id"]
    outro_cand = runtime.get_candidate(outro_id)
    assert outro_cand is not None
    outro_clip = next(c for c in outro_cand.clips_for_part("Bass") if c.section_name == "Outro")
    assert [(n.pitch, n.start_beats, n.duration_beats, n.velocity) for n in outro_clip.notes] == [
        (41, 0.0, 2.0, 110),
        (43, 8.0, 1.0, 95),
        (41, 14.0, 0.5, 90),
    ]

    # 2. EZbass ルーティング + slide_down 有効セクション (Intro) -> 音楽ノートは 41 (+12) へ、挿入キースイッチは 29 のまま
    prop_intro = runtime.propose_composer_adoption(
        outro_id,
        "bass",
        4,
        bass_notes,
        section_name="Intro",
        elements=ezbass_elements,
        section_elements=section_elements,
    )
    assert prop_intro["ok"] is True
    intro_id = runtime.decide_revision(prop_intro["revision"]["revision_id"], True)["selected_candidate_id"]
    intro_cand = runtime.get_candidate(intro_id)
    assert intro_cand is not None
    intro_clip = next(c for c in intro_cand.clips_for_part("Bass") if c.section_name == "Intro")
    assert [(n.pitch, n.start_beats, n.duration_beats, n.velocity) for n in intro_clip.notes] == [
        (41, 0.0, 2.0, 110),
        (29, 1.0, 1.0, 110),
        (43, 8.0, 1.0, 95),
        (41, 14.0, 0.5, 90),
    ]

    # 3. プラグインでないベース（ストック音源）では pitch 29 が移らない
    stock_elements = {"bass": {"technique": "slap"}}
    prop_stock = runtime.propose_composer_adoption(
        parent_id,
        "bass",
        4,
        bass_notes,
        section_name="Outro",
        elements=stock_elements,
    )
    assert prop_stock["ok"] is True
    stock_id = runtime.decide_revision(prop_stock["revision"]["revision_id"], True)["selected_candidate_id"]
    stock_cand = runtime.get_candidate(stock_id)
    assert stock_cand is not None
    stock_clip = next(c for c in stock_cand.clips_for_part("Bass") if c.section_name == "Outro")
    assert [(n.pitch, n.start_beats, n.duration_beats, n.velocity) for n in stock_clip.notes] == [
        (29, 0.0, 2.0, 110),
        (43, 8.0, 1.0, 95),
        (29, 14.0, 0.5, 90),
    ]


def test_avoid_ezbass_keyswitch_collision_preserves_existing_keyswitch_and_is_idempotent(
    tmp_path,
) -> None:
    """予約集合 {24, 25, 29, 30, 31, 32} の音楽ノートを +12 へ移し、挿入済み 29 のみ保持して冪等であることを検証する。"""
    from kihachi_mcp.services.session_pattern_builder import MidiNote
    from kihachi_mcp.services.studio_runtime import _avoid_ezbass_keyswitch_collision

    notes = (
        MidiNote(pitch=43, start_beats=0.0, duration_beats=2.0, velocity=110),   # H: 小節頭 & vel>=100
        MidiNote(pitch=29, start_beats=1.0, duration_beats=1.0, velocity=110),   # 挿入済みキースイッチ -> 29維持
        MidiNote(pitch=30, start_beats=1.0, duration_beats=1.0, velocity=110),   # 他予約 pitch は KS 例外なし -> 42へ
        MidiNote(pitch=43, start_beats=4.0, duration_beats=2.0, velocity=99),    # 非H: vel<100
        MidiNote(pitch=29, start_beats=5.0, duration_beats=1.0, velocity=99),    # 非KS位置 -> 41へ
        MidiNote(pitch=29, start_beats=8.0, duration_beats=2.0, velocity=110),   # 音楽ノート(小節頭) -> 41へ
        MidiNote(pitch=43, start_beats=12.0, duration_beats=2.0, velocity=100),  # H: 小節頭 & vel==100
        MidiNote(pitch=29, start_beats=13.0, duration_beats=0.5, velocity=100),  # duration不一致 -> 41へ
        MidiNote(pitch=29, start_beats=14.0, duration_beats=0.5, velocity=90),   # 非小節頭 -> 41へ
        # 予約集合の全境界値（24, 25, 29, 30, 31, 32）と域外（26, 27, 28, 33, 34）
        MidiNote(pitch=24, start_beats=16.0, duration_beats=0.5, velocity=90),   # -> 36 (+12)
        MidiNote(pitch=25, start_beats=16.5, duration_beats=0.5, velocity=90),   # -> 37 (+12)
        MidiNote(pitch=26, start_beats=17.0, duration_beats=0.5, velocity=90),   # -> 26 (維持)
        MidiNote(pitch=27, start_beats=17.5, duration_beats=0.5, velocity=90),   # -> 27 (維持)
        MidiNote(pitch=28, start_beats=18.0, duration_beats=0.5, velocity=90),   # -> 28 (維持)
        MidiNote(pitch=30, start_beats=18.5, duration_beats=0.5, velocity=90),   # -> 42 (+12)
        MidiNote(pitch=31, start_beats=19.0, duration_beats=0.5, velocity=90),   # -> 43 (+12)
        MidiNote(pitch=32, start_beats=19.5, duration_beats=0.5, velocity=90),   # -> 44 (+12)
        MidiNote(pitch=33, start_beats=20.0, duration_beats=0.5, velocity=90),   # -> 33 (維持)
        MidiNote(pitch=34, start_beats=20.5, duration_beats=0.5, velocity=90),   # -> 34 (維持)
    )

    once = _avoid_ezbass_keyswitch_collision(notes)
    twice = _avoid_ezbass_keyswitch_collision(once)

    expected = (
        (43, 0.0, 2.0, 110),
        (29, 1.0, 1.0, 110),
        (42, 1.0, 1.0, 110),
        (43, 4.0, 2.0, 99),
        (41, 5.0, 1.0, 99),
        (41, 8.0, 2.0, 110),
        (43, 12.0, 2.0, 100),
        (41, 13.0, 0.5, 100),
        (41, 14.0, 0.5, 90),
        (36, 16.0, 0.5, 90),
        (37, 16.5, 0.5, 90),
        (26, 17.0, 0.5, 90),
        (27, 17.5, 0.5, 90),
        (28, 18.0, 0.5, 90),
        (42, 18.5, 0.5, 90),
        (43, 19.0, 0.5, 90),
        (44, 19.5, 0.5, 90),
        (33, 20.0, 0.5, 90),
        (34, 20.5, 0.5, 90),
    )
    assert [(n.pitch, n.start_beats, n.duration_beats, n.velocity) for n in once] == list(expected)
    assert twice == once

    # 再利用クリップ（挿入済みキースイッチを含む）を plugin ルーティングで採用しても 29 のまま重複しないこと
    runtime, _live = _studio(tmp_path)
    parent_id = runtime.generate("Mutation Funk、124 BPM、Gm、16小節", seed=3)["candidate"]["candidate_id"]
    ezbass_elements = {
        "bass": {
            "instrument": "plugin",
            "source": "raw",
            "name": "EZbass",
            "path": ["VST3", "Toontrack"],
        }
    }
    reused_notes = [
        {"pitch": 43, "start": 0.0, "duration": 2.0, "velocity": 110},
        {"pitch": 29, "start": 1.0, "duration": 1.0, "velocity": 110},
        {"pitch": 29, "start": 14.0, "duration": 0.5, "velocity": 90},
    ]
    prop = runtime.propose_composer_adoption(
        parent_id,
        "bass",
        4,
        reused_notes,
        section_name="Outro",
        elements=ezbass_elements,
    )
    assert prop["ok"] is True
    child_id = runtime.decide_revision(prop["revision"]["revision_id"], True)["selected_candidate_id"]
    child = runtime.get_candidate(child_id)
    assert child is not None
    outro_clip = next(c for c in child.clips_for_part("Bass") if c.section_name == "Outro")
    assert [(n.pitch, n.start_beats, n.duration_beats, n.velocity) for n in outro_clip.notes] == [
        (43, 0.0, 2.0, 110),
        (29, 1.0, 1.0, 110),
        (41, 14.0, 0.5, 90),
    ]


def test_inject_slide_down_keyswitches_is_idempotent_across_postprocess(tmp_path) -> None:
    """スライド処理済みクリップへの後処理2回適用でノート集合が変わらず、新規クリップには従来どおり挿入されることを検証する。"""
    from kihachi_mcp.services.session_pattern_builder import MidiNote
    from kihachi_mcp.services.studio_runtime import (
        _avoid_ezbass_keyswitch_collision,
        _inject_slide_down_keyswitches,
    )

    # 1. 新規クリップ（キースイッチ未挿入）への後処理 -> 2回適用しても冪等
    fresh_notes = (
        MidiNote(pitch=43, start_beats=0.0, duration_beats=2.0, velocity=110),
        MidiNote(pitch=43, start_beats=8.0, duration_beats=1.5, velocity=100),
        MidiNote(pitch=29, start_beats=14.0, duration_beats=0.5, velocity=90),
    )

    def _postprocess(notes: tuple[MidiNote, ...]) -> tuple[MidiNote, ...]:
        return _inject_slide_down_keyswitches(_avoid_ezbass_keyswitch_collision(notes))

    once = _postprocess(fresh_notes)
    twice = _postprocess(once)
    expected = (
        (43, 0.0, 2.0, 110),
        (29, 1.0, 1.0, 110),
        (43, 8.0, 1.5, 100),
        (29, 8.75, 0.75, 100),
        (41, 14.0, 0.5, 90),
    )
    assert [(n.pitch, n.start_beats, n.duration_beats, n.velocity) for n in once] == list(expected)
    assert twice == once

    # 2. propose_composer_adoption 経由でスライド処理済みクリップを再利用しても2重挿入されないこと
    runtime, _live = _studio(tmp_path)
    parent_id = runtime.generate("Mutation Funk、124 BPM、Gm、16小節", seed=3)["candidate"]["candidate_id"]
    ezbass_elements = {
        "bass": {
            "instrument": "plugin",
            "source": "raw",
            "name": "EZbass",
            "path": ["VST3", "Toontrack"],
        }
    }
    section_elements = {"Intro": {"bass": {"slide_down": True}}}

    reused_notes = [
        {"pitch": n.pitch, "start": n.start_beats, "duration": n.duration_beats, "velocity": n.velocity}
        for n in once
    ]
    prop = runtime.propose_composer_adoption(
        parent_id,
        "bass",
        4,
        reused_notes,
        section_name="Intro",
        elements=ezbass_elements,
        section_elements=section_elements,
    )
    assert prop["ok"] is True
    child_id = runtime.decide_revision(prop["revision"]["revision_id"], True)["selected_candidate_id"]
    child = runtime.get_candidate(child_id)
    assert child is not None
    intro_clip = next(c for c in child.clips_for_part("Bass") if c.section_name == "Intro")
    assert [(n.pitch, n.start_beats, n.duration_beats, n.velocity) for n in intro_clip.notes] == list(expected)


def test_songspec_arrangement_sound_variants_roundtrip() -> None:
    """SongSpec.arrangement.sound_variants が正規化・ラウンドトリップできること。"""
    from kihachi_mcp.models.songspec import SongSpec

    spec = SongSpec.from_dict(
        {
            "tempo": 124,
            "key": "Gm",
            "genre": "Mutation Funk",
            "arrangement": {
                "sound_variants": {
                    "Bass": {
                        "variant_part": "BassMut",
                        "sections": ["Build", "Drop"],
                    }
                }
            },
        }
    )
    assert spec.arrangement["sound_variants"] == {
        "bass": {"variant_part": "BassMut", "sections": ["Build", "Drop"]}
    }
    reloaded = SongSpec.from_dict(spec.to_dict())
    assert reloaded.arrangement["sound_variants"] == spec.arrangement["sound_variants"]


def test_sound_variants_routes_only_specified_sections_with_identical_notes(
    tmp_path,
) -> None:
    """指定セクションの bass クリップだけが variant_part になり、ノート内容は通常側と同一であること。"""
    runtime, _live = _studio(tmp_path)
    cand_id = runtime.generate("Mutation Funk、124 BPM、Gm、16小節", seed=11)["candidate"][
        "candidate_id"
    ]
    arrangement = {
        "sound_variants": {
            "bass": {"variant_part": "BassMut", "sections": ["Build", "Drop"]}
        }
    }
    base_notes = [
        {"pitch": 43, "start": 0.0, "duration": 0.5, "velocity": 100},
        {"pitch": 46, "start": 1.5, "duration": 0.5, "velocity": 95},
    ]

    for sec in ("Intro", "Build", "Drop", "Outro"):
        prop = runtime.propose_composer_adoption(
            cand_id,
            "bass",
            4,
            base_notes,
            section_name=sec,
            arrangement=arrangement,
        )
        assert prop["ok"] is True
        cand_id = runtime.decide_revision(prop["revision"]["revision_id"], True)[
            "selected_candidate_id"
        ]

    child = runtime.get_candidate(cand_id)
    assert child is not None
    bass_secs = [c.section_name for c in child.clips_for_part("Bass")]
    mut_secs = [c.section_name for c in child.clips_for_part("BassMut")]
    assert bass_secs == ["Intro", "Outro"]
    assert mut_secs == ["Build", "Drop"]

    intro_notes = next(
        c.notes for c in child.clips_for_part("Bass") if c.section_name == "Intro"
    )
    build_notes = next(
        c.notes for c in child.clips_for_part("BassMut") if c.section_name == "Build"
    )
    assert intro_notes == build_notes


def test_sound_variants_applies_ezbass_guard_and_slide_down_to_variant(
    tmp_path,
) -> None:
    """EZbass 予約域ガードと slide_down キースイッチ注入が variant 側（BassMut）にも効くこと。"""
    runtime, _live = _studio(tmp_path)
    cand_id = runtime.generate("Mutation Funk、124 BPM、Gm、16小節", seed=12)["candidate"][
        "candidate_id"
    ]
    ezbass_elements = {
        "bass": {
            "instrument": "plugin",
            "source": "raw",
            "name": "EZbass",
            "path": ["VST3", "Toontrack"],
            "slide_down": True,
        }
    }
    arrangement = {
        "sound_variants": {
            "bass": {"variant_part": "BassMut", "sections": ["Build"]}
        }
    }
    raw_notes = [
        {"pitch": 43, "start": 0.0, "duration": 2.0, "velocity": 110},
        {"pitch": 29, "start": 6.0, "duration": 0.5, "velocity": 90},
    ]
    prop = runtime.propose_composer_adoption(
        cand_id,
        "bass",
        4,
        raw_notes,
        section_name="Build",
        elements=ezbass_elements,
        arrangement=arrangement,
    )
    assert prop["ok"] is True
    child_id = runtime.decide_revision(prop["revision"]["revision_id"], True)[
        "selected_candidate_id"
    ]
    child = runtime.get_candidate(child_id)
    assert child is not None
    mut_clip = next(c for c in child.clips_for_part("BassMut") if c.section_name == "Build")
    assert [(n.pitch, n.start_beats, n.duration_beats, n.velocity) for n in mut_clip.notes] == [
        (43, 0.0, 2.0, 110),
        (29, 1.0, 1.0, 110),  # slide_down キースイッチ注入
        (41, 6.0, 0.5, 90),  # 予約域 29 -> 41 ガード
    ]


def test_sound_variants_planner_creates_track_inherits_ezbass_effects_mix_and_arrangement(
    tmp_path,
) -> None:
    """variant_part がトラック作成・EZbass 継承・PART_EFFECTS・mix・アレンジメント展開に乗ること。"""
    from live_fixtures import executor as make_executor
    from live_fixtures import gate, snapshot_of

    from kihachi_mcp.knowledge.part_sounds import PART_EFFECTS
    from kihachi_mcp.models.live_contract import (
        OP_LOAD_PLUGIN,
        OP_SET_PLUGIN_PARAM,
        OP_SET_TRACK_VOLUME,
    )
    from kihachi_mcp.services.candidate_live_planner import CandidateLivePlanner
    from kihachi_mcp.services.live_transport_fake import FakeLiveSet, FakeLiveTransport

    runtime, _live = _studio(tmp_path)
    cand_id = runtime.generate("Mutation Funk、124 BPM、Gm、16小節", seed=13)["candidate"][
        "candidate_id"
    ]
    ezbass_elements = {
        "bass": {
            "instrument": "plugin",
            "source": "raw",
            "name": "EZbass",
            "path": ["VST3", "Toontrack"],
            "preset": "KIHACHI EZbass Slap.vstpreset",
            "params": {"Sub": 0.75},
        }
    }
    arrangement = {
        "sound_variants": {
            "bass": {"variant_part": "BassMut", "sections": ["Build", "Drop"]}
        },
        "mix": {"Bass": -12.0, "BassMut": -9.0},
    }
    notes = [{"pitch": 43, "start": 0.0, "duration": 1.0, "velocity": 100}]
    for sec in ("Build", "Drop"):
        prop = runtime.propose_composer_adoption(
            cand_id,
            "bass",
            4,
            notes,
            section_name=sec,
            elements=ezbass_elements,
            arrangement=arrangement,
        )
        assert prop["ok"] is True
        cand_id = runtime.decide_revision(prop["revision"]["revision_id"], True)[
            "selected_candidate_id"
        ]

    child = runtime.get_candidate(cand_id)
    assert child is not None
    assert "BassMut" in child.parts

    live_set = FakeLiveSet(live_version="12.4.5", tempo=124.0)
    live_set.browser_items = ["VST3/Toontrack/EZbass"]
    transport = FakeLiveTransport(live_set)
    planner = CandidateLivePlanner()
    approval = gate()
    exec_fn = make_executor(transport, approval)

    def _apply(p):
        token = approval.approve(p)
        return exec_fn.execute(p, approved=True, approval_token=token)

    # 1. トラック作成 + EZbass プリセット/パラメータ継承 + arrangement.mix
    plan = planner.create_plan(child, snapshot_of(transport))
    assert plan.conflicts == []
    mut_track_ops = [
        op
        for op in plan.operations
        if op.op == "create_midi_track" and "BassMut" in str(op.arguments.get("name"))
    ]
    assert len(mut_track_ops) == 1
    mut_vol_ops = [
        op
        for op in plan.operations
        if op.op == OP_SET_TRACK_VOLUME and op.arguments.get("part") == "BassMut"
    ]
    assert len(mut_vol_ops) == 1
    assert mut_vol_ops[0].arguments["volume_db"] == -9.0
    plugin_ops = [op for op in plan.operations if op.op == OP_LOAD_PLUGIN]
    param_ops = [op for op in plan.operations if op.op == OP_SET_PLUGIN_PARAM]
    assert len(plugin_ops) == 2  # Bass と BassMut の両方
    assert len(param_ops) == 2

    # 2. 標準デバイス版で FakeLiveTransport 上へトラック・Sessionクリップ・エフェクト・アレンジメント展開を検証
    from dataclasses import replace as dc_replace

    stock_child = dc_replace(child, elements={})
    stock_live = FakeLiveSet(live_version="12.4.5", tempo=124.0)
    stock_transport = FakeLiveTransport(stock_live)
    stock_exec = make_executor(stock_transport, approval)

    def _apply_stock(p):
        token = approval.approve(p)
        return stock_exec.execute(p, approved=True, approval_token=token)

    stock_plan = planner.create_plan(stock_child, snapshot_of(stock_transport))
    assert stock_plan.conflicts == []
    assert _apply_stock(stock_plan).status == "verified"

    mut_track = next(t for t in stock_live.tracks if "BassMut" in t["name"])

    # エフェクト（PART_EFFECTS["BassMut"]）
    fx_plan = planner.create_effects_plan(stock_child, snapshot_of(stock_transport))
    assert fx_plan.conflicts == []
    assert _apply_stock(fx_plan).status == "verified"
    assert mut_track["device_names"][1:] == [d.device for d in PART_EFFECTS["BassMut"]]

    # アレンジメント展開
    arr_plan = planner.create_arrangement_plan(stock_child, snapshot_of(stock_transport))
    assert arr_plan.conflicts == []
    assert _apply_stock(arr_plan).status == "verified"
    assert len(snapshot_of(stock_transport).arrangement_clips_on(mut_track["index"])) == 2


def test_sound_variants_omitted_matches_legacy_behavior(tmp_path) -> None:
    """sound_variants 未指定時は従来と完全に同一の候補・計画になること。"""
    from live_fixtures import snapshot_of

    from kihachi_mcp.services.candidate_live_planner import CandidateLivePlanner
    from kihachi_mcp.services.live_transport_fake import FakeLiveSet, FakeLiveTransport

    runtime, _live = _studio(tmp_path)
    cand_id = runtime.generate("Mutation Funk、124 BPM、Gm、16小節", seed=14)["candidate"][
        "candidate_id"
    ]
    notes = [{"pitch": 43, "start": 0.0, "duration": 1.0, "velocity": 100}]
    prop_none = runtime.propose_composer_adoption(
        cand_id, "bass", 4, notes, section_name="Build"
    )
    prop_empty = runtime.propose_composer_adoption(
        cand_id, "bass", 4, notes, section_name="Build", arrangement={}
    )
    assert prop_none["ok"] is True
    assert prop_empty["ok"] is True
    id_none = runtime.decide_revision(prop_none["revision"]["revision_id"], True)[
        "selected_candidate_id"
    ]
    id_empty = runtime.decide_revision(prop_empty["revision"]["revision_id"], True)[
        "selected_candidate_id"
    ]
    c_none = runtime.get_candidate(id_none)
    c_empty = runtime.get_candidate(id_empty)
    assert c_none is not None
    assert c_empty is not None
    assert "variant_parents" not in c_none.to_dict()
    assert c_none.parts == c_empty.parts
    assert c_none.clips == c_empty.clips

    live = FakeLiveSet(live_version="12.4.5", tempo=124.0)
    plan_none = CandidateLivePlanner().create_plan(c_none, snapshot_of(FakeLiveTransport(live)))
    plan_empty = CandidateLivePlanner().create_plan(c_empty, snapshot_of(FakeLiveTransport(live)))

    def _normalize_ops(plan, sid):
        import re

        return [
            (
                op.op,
                {
                    k: (
                        re.sub(r"\[K:[0-9a-f]{8}\]", "[K:<HASH>]", v.replace(sid, "<ID>"))
                        if isinstance(v, str)
                        else v
                    )
                    for k, v in op.arguments.items()
                },
            )
            for op in plan.operations
        ]

    assert _normalize_ops(plan_none, c_none.candidate_id[:8]) == _normalize_ops(
        plan_empty, c_empty.candidate_id[:8]
    )




