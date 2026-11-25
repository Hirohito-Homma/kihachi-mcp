"""phase_v_driver.py の --reuse-from / --reuse セル再利用機能の回帰テスト。"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

from test_studio_workflow import _studio


def _load_driver():
    driver_path = Path(__file__).resolve().parents[2] / "tasks" / "phase_v_driver.py"
    spec = importlib.util.spec_from_file_location("phase_v_driver", driver_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _prepare_candidate(runtime: Any, seed: int) -> str:
    generated = runtime.generate("Mutation Funk、124 BPM、Gm、16小節", seed=seed)
    assert generated["ok"] is True
    return str(generated["candidate"]["candidate_id"])


def _adopt_custom_notes(
    runtime: Any,
    candidate_id: str,
    section_name: str,
    role: str,
    notes: list[dict[str, Any]],
) -> str:
    proposal = runtime.propose_composer_adoption(
        candidate_id=candidate_id,
        role=role,
        pattern_bars=4,
        notes=notes,
        section_name=section_name,
    )
    assert proposal["ok"] is True, proposal
    decision = runtime.decide_revision(proposal["revision"]["revision_id"], accept=True)
    assert decision["ok"] is True, decision
    return str(decision["selected_candidate_id"])


def test_phase_v_driver_reuse_preserves_exact_notes_and_applies_mix(
    tmp_path: Path, monkeypatch: Any, capsys: Any
) -> None:
    """--reuse 指定セルは compose をスキップし再利用元と完全一致するノートを採用し、他仕様も維持されること。"""
    driver = _load_driver()
    monkeypatch.setattr(driver, "__file__", str(tmp_path / "phase_v_driver.py"))

    runtime, _live = _studio(tmp_path)
    base_spec = {
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
    }
    base_id = _prepare_candidate(runtime, seed=101)
    reuse_id = _prepare_candidate(runtime, seed=202)

    expected_drop_melody = [
        {"pitch": 67, "start": 0.0, "duration": 0.5, "velocity": 101},
        {"pitch": 70, "start": 1.5, "duration": 1.0, "velocity": 88},
        {"pitch": 74, "start": 3.0, "duration": 0.75, "velocity": 95},
    ]
    expected_intro_bass = [
        {"pitch": 43, "start": 0.0, "duration": 0.75, "velocity": 110},
        {"pitch": 46, "start": 2.25, "duration": 0.5, "velocity": 92},
    ]
    reuse_id = _adopt_custom_notes(runtime, reuse_id, "Drop", "melody", expected_drop_melody)
    reuse_id = _adopt_custom_notes(runtime, reuse_id, "Intro", "bass", expected_intro_bass)

    new_spec = {
        **base_spec,
        "compose_cells": {
            "bass": ["Intro", "Drop"],
            "chords": [],
            "melody": ["Drop"],
            "arp": [],
            "percussion": [],
        },
        "elements": {"bass": {"technique": "slap", "ghost_notes": True}},
        "arrangement": {"mix": {"Pad": -6.0, "Bass": 1.5}},
    }
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps(new_spec), encoding="utf-8")

    composed_cells: list[tuple[str, str]] = []
    fresh_drop_bass = [
        {"pitch": 43, "start": 0.0, "duration": 0.25, "velocity": 100},
        {"pitch": 55, "start": 1.0, "duration": 0.25, "velocity": 90},
    ]

    def fake_get(path: str, timeout: float = 60.0) -> dict[str, Any]:
        assert path == f"/api/candidate/{reuse_id}"
        cand = runtime.get_candidate(reuse_id)
        assert cand is not None
        return {"ok": True, "candidate": cand.to_dict()}

    def fake_post(path: str, payload: dict[str, Any], timeout: float = 600.0) -> dict[str, Any]:
        if path == "/api/midi/compose":
            role = payload["constraints"]["role"]
            start_bar = payload["start_bar"]
            sec = {1: "Intro", 5: "Build", 9: "Drop", 13: "Outro"}[start_bar]
            composed_cells.append((sec, role))
            return {"ok": True, "notes": fresh_drop_bass, "note_count": len(fresh_drop_bass)}
        if path == "/api/midi/adopt-preview":
            return runtime.propose_composer_adoption(
                candidate_id=payload["candidate_id"],
                role=payload["role"],
                pattern_bars=payload["pattern_bars"],
                notes=payload["notes"],
                section_name=payload["section_name"],
                elements=payload.get("elements"),
                arrangement=payload.get("arrangement"),
            )
        if path == "/api/revision/decide":
            return runtime.decide_revision(payload["revision_id"], accept=bool(payload["accept"]))
        raise AssertionError(f"unexpected path: {path}")

    monkeypatch.setattr(driver, "get", fake_get)
    monkeypatch.setattr(driver, "post", fake_post)

    rc = driver.main(
        [
            "--spec",
            str(spec_path),
            "--candidate",
            base_id,
            "--reuse-from",
            reuse_id,
            "--reuse",
            "Drop:melody",
            "--reuse",
            "Intro:bass",
            "--yes",
        ]
    )
    assert rc == 0
    assert composed_cells == [("Drop", "bass")]

    run_log = json.loads((tmp_path / "phase_v_run_log.json").read_text(encoding="utf-8"))
    final_id = run_log[-1]["candidate_id"]
    final_cand = runtime.get_candidate(final_id)
    assert final_cand is not None

    drop_lead = next(
        c for c in final_cand.clips if c.section_name == "Drop" and c.part == "Lead"
    )
    intro_bass = next(
        c for c in final_cand.clips if c.section_name == "Intro" and c.part == "Bass"
    )
    drop_bass = next(
        c for c in final_cand.clips if c.section_name == "Drop" and c.part == "Bass"
    )
    assert [n.to_dict() for n in drop_lead.notes] == expected_drop_melody
    assert [n.to_dict() for n in intro_bass.notes] == expected_intro_bass
    assert [n.to_dict() for n in drop_bass.notes] == fresh_drop_bass
    assert final_cand.mix == {"Pad": -6.0, "Bass": 1.5}
    assert final_cand.elements == {"bass": {"technique": "slap", "ghost_notes": True}}

    out = capsys.readouterr().out
    assert f"Drop/melody: REUSED from {reuse_id[:8]} (3 notes)" in out
    assert f"Intro/bass: REUSED from {reuse_id[:8]} (2 notes)" in out


def test_phase_v_driver_reuse_errors(tmp_path: Path, monkeypatch: Any) -> None:
    """--reuse-from 欠落・形式不正・再利用元クリップ不在時はエラー終了し compose へフォールバックしないこと。"""
    driver = _load_driver()
    monkeypatch.setattr(driver, "__file__", str(tmp_path / "phase_v_driver.py"))

    runtime, _live = _studio(tmp_path)
    base_spec = {
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
    }
    base_id = _prepare_candidate(runtime, seed=303)
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps(base_spec), encoding="utf-8")

    # 1. --reuse-from なしで --reuse 指定
    rc_missing_from = driver.main(
        ["--spec", str(spec_path), "--candidate", base_id, "--reuse", "Drop:melody", "--yes"]
    )
    assert rc_missing_from != 0

    # 2. --reuse 形式不正
    rc_bad_fmt = driver.main(
        [
            "--spec",
            str(spec_path),
            "--candidate",
            base_id,
            "--reuse-from",
            base_id,
            "--reuse",
            "Dropmelody",
            "--yes",
        ]
    )
    assert rc_bad_fmt != 0

    # 3. 再利用元候補に該当クリップが存在しない場合（Intro:arp は base 候補に存在しない）
    post_called = False

    def fake_get(_path: str, timeout: float = 60.0) -> dict[str, Any]:
        cand = runtime.get_candidate(base_id)
        assert cand is not None
        return {"ok": True, "candidate": cand.to_dict()}

    def fake_post(_path: str, _payload: dict[str, Any], timeout: float = 600.0) -> dict[str, Any]:
        nonlocal post_called
        post_called = True
        return {"ok": True}

    monkeypatch.setattr(driver, "get", fake_get)
    monkeypatch.setattr(driver, "post", fake_post)

    rc_missing_clip = driver.main(
        [
            "--spec",
            str(spec_path),
            "--candidate",
            base_id,
            "--reuse-from",
            base_id,
            "--reuse",
            "Intro:arp",
            "--yes",
        ]
    )
    assert rc_missing_clip != 0
    assert post_called is False


def test_phase_v_driver_without_reuse_runs_legacy_path(tmp_path: Path, monkeypatch: Any) -> None:
    """--reuse 指定なしの従来実行では GET を呼ばず全セルが従来どおり compose/adopt されること。"""
    driver = _load_driver()
    monkeypatch.setattr(driver, "__file__", str(tmp_path / "phase_v_driver.py"))

    runtime, _live = _studio(tmp_path)
    spec_dict = {
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
    }
    base_id = _prepare_candidate(runtime, seed=404)
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps(spec_dict), encoding="utf-8")

    get_called = False
    composed_count = 0
    sample_notes = [{"pitch": 43, "start": 0.0, "duration": 0.5, "velocity": 100}]

    def fake_get(_path: str, timeout: float = 60.0) -> dict[str, Any]:
        nonlocal get_called
        get_called = True
        return {}

    def fake_post(path: str, payload: dict[str, Any], timeout: float = 600.0) -> dict[str, Any]:
        nonlocal composed_count
        if path == "/api/midi/compose":
            composed_count += 1
            return {"ok": True, "notes": sample_notes, "note_count": 1}
        if path == "/api/midi/adopt-preview":
            return runtime.propose_composer_adoption(
                candidate_id=payload["candidate_id"],
                role=payload["role"],
                pattern_bars=payload["pattern_bars"],
                notes=payload["notes"],
                section_name=payload["section_name"],
            )
        if path == "/api/revision/decide":
            return runtime.decide_revision(payload["revision_id"], accept=bool(payload["accept"]))
        raise AssertionError(f"unexpected path: {path}")

    monkeypatch.setattr(driver, "get", fake_get)
    monkeypatch.setattr(driver, "post", fake_post)

    rc = driver.main(["--spec", str(spec_path), "--candidate", base_id, "--yes"])
    assert rc == 0
    assert get_called is False
    assert composed_count == 17


def test_phase_v_driver_resolves_six_sections_and_falls_back_without_sections(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """6セクション仕様で累積 start_bar/bars が解決され、sections なしでは従来動作になること。"""
    driver = _load_driver()
    monkeypatch.setattr(driver, "__file__", str(tmp_path / "phase_v_driver.py"))

    six_sections = [
        {"name": "Intro", "bars": 4},
        {"name": "Verse", "bars": 8},
        {"name": "Build", "bars": 4},
        {"name": "Drop", "bars": 8},
        {"name": "Bridge", "bars": 4},
        {"name": "Outro", "bars": 4},
    ]
    spec_six = {
        "genre": "mutation_funk",
        "tempo": 124,
        "key": "Gm",
        "bars": 32,
        "sections": six_sections,
        "compose_cells": {
            "bass": ["Intro", "Verse", "Build", "Drop", "Bridge", "Outro"],
            "chords": [],
            "melody": [],
            "arp": [],
            "percussion": [],
        },
    }
    resolved = driver.resolve_sections(spec_six)
    assert resolved == [
        ("Intro", 1, 4),
        ("Verse", 5, 8),
        ("Build", 13, 4),
        ("Drop", 17, 8),
        ("Bridge", 25, 4),
        ("Outro", 29, 4),
    ]
    assert driver.build_cells(spec_six) == [
        ("Intro", "bass"),
        ("Verse", "bass"),
        ("Build", "bass"),
        ("Drop", "bass"),
        ("Bridge", "bass"),
        ("Outro", "bass"),
    ]

    # sections なしの場合は従来の4セクション×4小節（17セル）にフォールバック
    spec_no_sections = {"genre": "mutation_funk", "tempo": 124, "key": "Gm", "bars": 16}
    assert driver.resolve_sections(spec_no_sections) == [
        ("Intro", 1, 4),
        ("Build", 5, 4),
        ("Drop", 9, 4),
        ("Outro", 13, 4),
    ]
    assert len(driver.build_cells(spec_no_sections)) == 17

    # ドライバ実行時にセクションごとの bars/start_bar と配置が渡されること
    runtime, _live = _studio(tmp_path)
    base_id = _prepare_candidate(runtime, seed=505)
    spec_path = tmp_path / "spec_six.json"
    spec_path.write_text(json.dumps(spec_six), encoding="utf-8")

    compose_calls: list[tuple[str, int, int]] = []
    adopt_calls: list[tuple[str, int, list[dict[str, Any]]]] = []
    sample_notes = [{"pitch": 43, "start": 0.0, "duration": 0.5, "velocity": 100}]

    def fake_post(path: str, payload: dict[str, Any], timeout: float = 600.0) -> dict[str, Any]:
        if path == "/api/midi/compose":
            c = payload["constraints"]
            sec_name = next(
                name
                for name, s_bar, _b in resolved
                if s_bar == payload["start_bar"]
            )
            compose_calls.append((sec_name, payload["start_bar"], c["bars"]))
            return {"ok": True, "notes": sample_notes, "note_count": 1}
        if path == "/api/midi/adopt-preview":
            adopt_calls.append((payload["section_name"], payload["pattern_bars"], payload["sections"]))
            return runtime.propose_composer_adoption(
                candidate_id=payload["candidate_id"],
                role=payload["role"],
                pattern_bars=payload["pattern_bars"],
                notes=payload["notes"],
                section_name=payload["section_name"],
                sections=payload.get("sections"),
            )
        if path == "/api/revision/decide":
            return runtime.decide_revision(payload["revision_id"], accept=bool(payload["accept"]))
        raise AssertionError(f"unexpected path: {path}")

    monkeypatch.setattr(driver, "post", fake_post)
    rc = driver.main(["--spec", str(spec_path), "--candidate", base_id, "--yes"])
    assert rc == 0
    assert compose_calls == resolved
    assert [(sec, bars) for sec, bars, _ in adopt_calls] == [
        ("Intro", 4),
        ("Verse", 8),
        ("Build", 4),
        ("Drop", 8),
        ("Bridge", 4),
        ("Outro", 4),
    ]


def test_propose_composer_adoption_creates_missing_section_clip_and_updates_brief(
    tmp_path: Path,
) -> None:
    """土台にない (part, section) への採用でクリップ作成と brief 更新が行われ、配置外は拒否されること。"""
    runtime, _live = _studio(tmp_path)
    base_id = _prepare_candidate(runtime, seed=606)
    base_cand = runtime.get_candidate(base_id)
    assert base_cand is not None

    six_layout = [
        {"name": "Intro", "start_bar": 1, "length_bars": 4},
        {"name": "Verse", "start_bar": 5, "length_bars": 8},
        {"name": "Build", "start_bar": 13, "length_bars": 4},
        {"name": "Drop", "start_bar": 17, "length_bars": 8},
        {"name": "Bridge", "start_bar": 25, "length_bars": 4},
        {"name": "Outro", "start_bar": 29, "length_bars": 4},
    ]
    notes_4bars = [{"pitch": 43, "start": 0.0, "duration": 0.5, "velocity": 105}]

    # 配置にないセクションは従来どおり拒否
    refused = runtime.propose_composer_adoption(
        candidate_id=base_id,
        role="bass",
        pattern_bars=4,
        notes=notes_4bars,
        section_name="Solo",
        sections=six_layout,
    )
    assert refused["ok"] is False
    assert "BassのSoloクリップがありません" in refused["error"]

    # 配置なしでも土台にないセクションは拒否
    refused_no_layout = runtime.propose_composer_adoption(
        candidate_id=base_id,
        role="bass",
        pattern_bars=4,
        notes=notes_4bars,
        section_name="Verse",
    )
    assert refused_no_layout["ok"] is False

    # 配置にある新セクション (Bass, Verse) へ採用 → クリップ新規作成 + タイリング + brief 更新
    proposal = runtime.propose_composer_adoption(
        candidate_id=base_id,
        role="bass",
        pattern_bars=4,
        notes=notes_4bars,
        section_name="Verse",
        sections=six_layout,
    )
    assert proposal["ok"] is True, proposal
    assert proposal["other_parts_unchanged"] is True
    assert proposal["changed_clips"] == 1

    decision = runtime.decide_revision(proposal["revision"]["revision_id"], accept=True)
    assert decision["ok"] is True
    child = runtime.get_candidate(decision["selected_candidate_id"])
    assert child is not None

    verse_bass = [c for c in child.clips if c.part == "Bass" and c.section_name == "Verse"]
    assert len(verse_bass) == 1
    assert verse_bass[0].start_bar == 5
    assert verse_bass[0].length_bars == 8
    # 4小節パターンが8小節クリップに2回タイリングされる（beat 0.0 と beat 16.0）
    assert [(n.pitch, n.start_beats) for n in verse_bass[0].notes] == [(43, 0.0), (43, 16.0)]

    # brief が新構成（32小節・6セクション）に更新されていること
    assert child.brief.bars.value == 32
    assert [(s.name, s.start_bar, s.length_bars) for s in child.brief.sections] == [
        ("Intro", 1, 4),
        ("Verse", 5, 8),
        ("Build", 13, 4),
        ("Drop", 17, 8),
        ("Bridge", 25, 4),
        ("Outro", 29, 4),
    ]
    assert child.brief.drop_start_bar.value == 17

    # 後方互換: 従来の4セクション配置を渡した場合は brief が変化しない
    four_layout = [
        {"name": "Intro", "start_bar": 1, "length_bars": 4},
        {"name": "Build", "start_bar": 5, "length_bars": 4},
        {"name": "Drop", "start_bar": 9, "length_bars": 4},
        {"name": "Outro", "start_bar": 13, "length_bars": 4},
    ]
    compat_prop = runtime.propose_composer_adoption(
        candidate_id=base_id,
        role="bass",
        pattern_bars=4,
        notes=notes_4bars,
        section_name="Intro",
        sections=four_layout,
    )
    assert compat_prop["ok"] is True
    compat_dec = runtime.decide_revision(compat_prop["revision"]["revision_id"], accept=True)
    compat_child = runtime.get_candidate(compat_dec["selected_candidate_id"])
    assert compat_child is not None
    assert compat_child.brief == base_cand.brief


def test_propose_composer_adoption_relocates_existing_clips_with_six_section_layout(
    tmp_path: Path,
) -> None:
    """16小節土台＋24小節6セクション配置で既存クリップも含め全クリップが配置どおりに再配置され衝突しないこと。"""
    runtime, _live = _studio(tmp_path)
    base_id = _prepare_candidate(runtime, seed=707)
    base_cand = runtime.get_candidate(base_id)
    assert base_cand is not None

    layout_24 = [
        {"name": "Intro", "start_bar": 1, "length_bars": 4},
        {"name": "Verse", "start_bar": 5, "length_bars": 4},
        {"name": "Build", "start_bar": 9, "length_bars": 4},
        {"name": "Drop", "start_bar": 13, "length_bars": 4},
        {"name": "Bridge", "start_bar": 17, "length_bars": 4},
        {"name": "Outro", "start_bar": 21, "length_bars": 4},
    ]
    expected_pos = {sec["name"]: (sec["start_bar"], sec["length_bars"]) for sec in layout_24}
    notes_4bars = [{"pitch": 43, "start": 0.0, "duration": 0.5, "velocity": 100}]

    # 新規セクション Verse に Bass を採用
    proposal = runtime.propose_composer_adoption(
        candidate_id=base_id,
        role="bass",
        pattern_bars=4,
        notes=notes_4bars,
        section_name="Verse",
        sections=layout_24,
    )
    assert proposal["ok"] is True, proposal
    assert proposal["other_parts_unchanged"] is True

    decision = runtime.decide_revision(proposal["revision"]["revision_id"], accept=True)
    assert decision["ok"] is True
    child = runtime.get_candidate(decision["selected_candidate_id"])
    assert child is not None

    # 既存クリップも含め全クリップの start_bar / length_bars が配置どおり（Drop 9→13、Outro 13→21 を含む）
    for clip in child.clips:
        assert (clip.start_bar, clip.length_bars) == expected_pos[clip.section_name]

    # 既存クリップのノート内容は変更されていないこと
    base_by_sec_part = {(c.part, c.section_name): c.notes for c in base_cand.clips}
    for clip in child.clips:
        if (clip.part, clip.section_name) in base_by_sec_part:
            assert clip.notes == base_by_sec_part[(clip.part, clip.section_name)]

    # 同一トラック（part）で start_bar が重なるクリップがないこと
    by_part: dict[str, list[int]] = {}
    for clip in child.clips:
        by_part.setdefault(clip.part, []).append(clip.start_bar)
    for part_name, starts in by_part.items():
        assert len(starts) == len(set(starts)), f"{part_name} has overlapping start_bars: {starts}"

    # 配置に含まれないセクション（Outro を除外した16小節の部分配置）のクリップは変更されないこと
    partial_layout = [
        {"name": "Intro", "start_bar": 1, "length_bars": 4},
        {"name": "Verse", "start_bar": 5, "length_bars": 4},
        {"name": "Build", "start_bar": 9, "length_bars": 4},
        {"name": "Drop", "start_bar": 13, "length_bars": 4},
    ]
    partial_prop = runtime.propose_composer_adoption(
        candidate_id=base_id,
        role="bass",
        pattern_bars=4,
        notes=notes_4bars,
        section_name="Intro",
        sections=partial_layout,
    )
    assert partial_prop["ok"] is True, partial_prop
    partial_dec = runtime.decide_revision(partial_prop["revision"]["revision_id"], accept=True)
    partial_child = runtime.get_candidate(partial_dec["selected_candidate_id"])
    assert partial_child is not None
    for clip in partial_child.clips:
        if clip.section_name == "Outro":
            assert (clip.start_bar, clip.length_bars) == (13, 4)

    # 配置なしの従来ケースは土台の start_bar / length_bars を維持すること
    no_layout_prop = runtime.propose_composer_adoption(
        candidate_id=base_id,
        role="bass",
        pattern_bars=4,
        notes=notes_4bars,
        section_name="Intro",
    )
    assert no_layout_prop["ok"] is True
    no_layout_dec = runtime.decide_revision(no_layout_prop["revision"]["revision_id"], accept=True)
    no_layout_child = runtime.get_candidate(no_layout_dec["selected_candidate_id"])
    assert no_layout_child is not None
    assert [
        (c.part, c.section_name, c.start_bar, c.length_bars) for c in no_layout_child.clips
    ] == [(c.part, c.section_name, c.start_bar, c.length_bars) for c in base_cand.clips]


def test_dry_run_passes_for_24bar_six_section_and_legacy_16bar_candidates(
    tmp_path: Path,
) -> None:
    """24小節6セクション候補（再配置＋新規クリップ）と16小節従来候補の dry-run でノート照合が通過すること。"""
    from kihachi_mcp.models.midi_candidate import PART_ORDER, MidiCandidate

    runtime, _live = _studio(tmp_path)
    base_id = _prepare_candidate(runtime, seed=808)

    # 1. 従来の16小節4セクション候補での dry-run PASS
    legacy_notes = [{"pitch": 43, "start": 0.0, "duration": 0.5, "velocity": 100}]
    legacy_id = _adopt_custom_notes(runtime, base_id, "Intro", "bass", legacy_notes)
    runtime.approve(legacy_id)
    legacy_dry = runtime.dry_run(legacy_id)
    assert legacy_dry["safety_verdict"] == "PASS", legacy_dry
    assert legacy_dry["blockers"] == []
    assert runtime.apply_preview(legacy_id)["notes_match_preview"] is True

    # 2. 24小節6セクション（Verse/Break 新規追加 + 既存セクション再配置）での dry-run PASS
    layout_24 = [
        {"name": "Intro", "start_bar": 1, "length_bars": 4},
        {"name": "Verse", "start_bar": 5, "length_bars": 4},
        {"name": "Build", "start_bar": 9, "length_bars": 4},
        {"name": "Drop", "start_bar": 13, "length_bars": 4},
        {"name": "Break", "start_bar": 17, "length_bars": 4},
        {"name": "Outro", "start_bar": 21, "length_bars": 4},
    ]
    verse_prop = runtime.propose_composer_adoption(
        candidate_id=base_id,
        role="bass",
        pattern_bars=4,
        notes=[{"pitch": 43, "start": 0.0, "duration": 0.5, "velocity": 102}],
        section_name="Verse",
        sections=layout_24,
    )
    assert verse_prop["ok"] is True, verse_prop
    verse_id = runtime.decide_revision(verse_prop["revision"]["revision_id"], accept=True)[
        "selected_candidate_id"
    ]
    break_prop = runtime.propose_composer_adoption(
        candidate_id=verse_id,
        role="chords",
        pattern_bars=4,
        notes=[{"pitch": 55, "start": 0.0, "duration": 1.0, "velocity": 90}],
        section_name="Break",
        sections=layout_24,
    )
    assert break_prop["ok"] is True, break_prop
    cand_24_id = runtime.decide_revision(break_prop["revision"]["revision_id"], accept=True)[
        "selected_candidate_id"
    ]
    cand_24 = runtime.get_candidate(cand_24_id)
    assert cand_24 is not None

    # clips が PART_ORDER × start_bar 順に整列されていること
    part_rank = {p: idx for idx, p in enumerate(PART_ORDER)}
    clip_keys = [(part_rank[c.part], c.start_bar) for c in cand_24.clips]
    assert clip_keys == sorted(clip_keys)

    runtime.approve(cand_24_id)
    dry_24 = runtime.dry_run(cand_24_id)
    assert dry_24["safety_verdict"] == "PASS", dry_24
    assert dry_24["blockers"] == []
    assert runtime.apply_preview(cand_24_id)["notes_match_preview"] is True

    # 3. 既存の clips 末尾に新規クリップが追加された並びの候補でも照合が通過すること
    unsorted_clips = tuple(reversed(cand_24.clips))
    unsorted_cand = MidiCandidate(
        candidate_id="unsorted24test000000000000000001",
        seed=cand_24.seed,
        brief=cand_24.brief,
        clips=unsorted_clips,
        parent_candidate_id=cand_24.candidate_id,
    )
    runtime._candidates[unsorted_cand.candidate_id] = unsorted_cand
    runtime.approve(unsorted_cand.candidate_id)
    unsorted_dry = runtime.dry_run(unsorted_cand.candidate_id)
    assert unsorted_dry["safety_verdict"] == "PASS", unsorted_dry
    assert unsorted_dry["blockers"] == []
    assert runtime.apply_preview(unsorted_cand.candidate_id)["notes_match_preview"] is True

    # 安全判定が緩んでいないこと（計画側のノートが1音でもズレれば FAIL）
    orig_create_plan = runtime._planner.create_plan

    def _tampered_create_plan(*args: Any, **kwargs: Any) -> Any:
        plan = orig_create_plan(*args, **kwargs)
        for op in plan.operations:
            if op.op == "replace_clip_notes" and op.arguments.get("notes"):
                op.arguments["notes"][0]["pitch"] += 1
                break
        return plan

    runtime._planner.create_plan = _tampered_create_plan  # type: ignore[method-assign]
    assert runtime.apply_preview(cand_24_id)["notes_match_preview"] is False
    tampered_dry = runtime.dry_run(cand_24_id)
    assert tampered_dry["safety_verdict"] == "FAIL"
    assert "プレビューと適用計画のノートが一致しません" in tampered_dry["blockers"]


def test_arrangement_repeat_copies_source_and_regenerates_specified_roles(
    tmp_path: Path,
    monkeypatch: Any,
    capsys: Any,
) -> None:
    """arrangement.repeat で非対象ロールを完全コピーし、regenerate ロールのみ生成、欠落時停止を検証する。"""
    driver = _load_driver()
    monkeypatch.setattr(driver, "__file__", str(tmp_path / "phase_v_driver.py"))

    runtime, _live = _studio(tmp_path)
    base_id = _prepare_candidate(runtime, seed=707)

    sections = [
        {"name": "Intro", "bars": 4},
        {"name": "Build", "bars": 4},
        {"name": "Drop", "bars": 4},
        {"name": "Outro", "bars": 4},
        {"name": "Outro2", "bars": 4},
    ]
    spec = {
        "genre": "mutation_funk",
        "tempo": 124,
        "key": "Gm",
        "bars": 20,
        "sections": sections,
        "section_elements": {
            "Outro2": {"bass": {"ending": "rise"}},
        },
        "arrangement": {
            "activity": {
                "Intro": ["bass", "chords"],
                "Build": ["bass", "chords"],
                "Drop": ["bass", "chords"],
                "Outro": ["bass", "chords"],
                "Outro2": ["bass", "chords"],
            },
            "repeat": {
                "Outro2": {"from": "Outro", "regenerate": ["bass"]},
            },
        },
    }
    spec_path = tmp_path / "spec_repeat.json"
    spec_path.write_text(json.dumps(spec), encoding="utf-8")

    compose_calls: list[tuple[int, str, dict[str, Any]]] = []
    outro_chords_notes = [
        {"pitch": 55, "start": 0.0, "duration": 1.0, "velocity": 88},
        {"pitch": 58, "start": 2.0, "duration": 1.0, "velocity": 92},
    ]
    outro_bass_notes = [{"pitch": 43, "start": 0.0, "duration": 0.5, "velocity": 100}]
    outro2_bass_notes = [
        {"pitch": 43, "start": 12.5, "duration": 0.25, "velocity": 95},
        {"pitch": 46, "start": 13.5, "duration": 0.25, "velocity": 98},
        {"pitch": 50, "start": 14.5, "duration": 0.25, "velocity": 101},
        {"pitch": 55, "start": 15.5, "duration": 0.25, "velocity": 105},
    ]

    def fake_post(path: str, payload: dict[str, Any], timeout: float = 600.0) -> dict[str, Any]:
        if path == "/api/midi/compose":
            c = payload["constraints"]
            start_bar = int(payload["start_bar"])
            role = str(c["role"])
            compose_calls.append((start_bar, role, payload.get("songspec") or {}))
            if start_bar == 13 and role == "chords":
                notes = outro_chords_notes
            elif start_bar == 13 and role == "bass":
                notes = outro_bass_notes
            elif start_bar == 17 and role == "bass":
                notes = outro2_bass_notes
            else:
                notes = [{"pitch": 48, "start": 0.0, "duration": 0.5, "velocity": 90}]
            return {"ok": True, "notes": notes, "note_count": len(notes)}
        if path == "/api/midi/adopt-preview":
            return runtime.propose_composer_adoption(
                candidate_id=payload["candidate_id"],
                role=payload["role"],
                pattern_bars=payload["pattern_bars"],
                notes=payload["notes"],
                section_name=payload["section_name"],
                sections=payload.get("sections"),
            )
        if path == "/api/revision/decide":
            return runtime.decide_revision(payload["revision_id"], accept=bool(payload["accept"]))
        raise AssertionError(f"unexpected path: {path}")

    def fake_get(_path: str, timeout: float = 60.0) -> dict[str, Any]:
        cand = runtime.get_candidate(base_id)
        assert cand is not None
        return {"ok": True, "candidate": cand.to_dict()}

    monkeypatch.setattr(driver, "get", fake_get)
    monkeypatch.setattr(driver, "post", fake_post)
    rc = driver.main(["--spec", str(spec_path), "--candidate", base_id, "--yes"])
    assert rc == 0

    # Outro2/chords は compose されず、Outro2/bass のみ compose される
    assert (17, "chords") not in [(sb, r) for sb, r, _s in compose_calls]
    assert (17, "bass") in [(sb, r) for sb, r, _s in compose_calls]
    outro2_bass_spec = next(s for sb, r, s in compose_calls if sb == 17 and r == "bass")
    assert outro2_bass_spec["section_elements"]["Outro2"]["bass"] == {"ending": "rise"}

    captured = capsys.readouterr().out
    assert "Outro2/chords: COPIED from Outro (2 notes)" in captured

    log_data = json.loads((tmp_path / "phase_v_run_log.json").read_text(encoding="utf-8"))
    final_id = log_data[-1]["candidate_id"]
    final_cand = runtime.get_candidate(final_id)
    assert final_cand is not None

    outro_chords = next(
        c for c in final_cand.clips if c.section_name == "Outro" and c.part == "Stab"
    )
    outro2_chords = next(
        c for c in final_cand.clips if c.section_name == "Outro2" and c.part == "Stab"
    )
    assert outro2_chords.notes == outro_chords.notes

    outro2_bass = next(
        c for c in final_cand.clips if c.section_name == "Outro2" and c.part == "Bass"
    )
    assert [n.pitch for n in outro2_bass.notes] == [43, 46, 50, 55]

    # 元セクションのクリップがチェーン内に存在しない場合は代替生成せずエラー停止
    missing_spec = {
        **spec,
        "arrangement": {
            "activity": {
                "Intro": ["bass", "chords"],
                "Build": ["bass", "chords"],
                "Drop": ["bass", "chords"],
                "Outro": ["bass"],  # chords が元セクションに存在しない
                "Outro2": ["bass", "chords"],
            },
            "repeat": {
                "Outro2": {"from": "Outro", "regenerate": ["bass"]},
            },
        },
    }
    missing_spec_path = tmp_path / "spec_repeat_missing.json"
    missing_spec_path.write_text(json.dumps(missing_spec), encoding="utf-8")
    compose_calls.clear()
    rc_missing = driver.main(["--spec", str(missing_spec_path), "--candidate", base_id, "--yes"])
    assert rc_missing == 1
    assert (17, "chords") not in [(sb, r) for sb, r, _s in compose_calls]
    assert "REPEAT FAILED: source clip Outro/chords not found in chain" in capsys.readouterr().out


def test_arrangement_repeat_copies_base_parts_without_duplicates_and_skips_empty_source(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    """repeat で土台パートを同一ノートでコピーし、元になければ追加せず、既存があれば重複させないこと。"""
    from dataclasses import replace as dc_replace

    from kihachi_mcp.models.midi_candidate import CandidateClip, MidiNote

    driver = _load_driver()
    monkeypatch.setattr(driver, "__file__", str(tmp_path / "phase_v_driver.py"))

    runtime, _live = _studio(tmp_path)
    base_id = _prepare_candidate(runtime, seed=808)
    base_cand = runtime.get_candidate(base_id)
    assert base_cand is not None

    # Intro には土台パートを置かず（Verse 相当）、Drop には Kick/Snare/Pad を置き、
    # Drop2 には既存 Kick だけを持たせて重複防止を検証する。
    drop_kick_notes = (
        MidiNote(36, 0.0, 0.25, 110),
        MidiNote(36, 2.0, 0.25, 108),
    )
    drop_snare_notes = (MidiNote(38, 1.0, 0.25, 102),)
    drop_pad_notes = (MidiNote(60, 0.0, 4.0, 72),)
    existing_drop2_kick_notes = (MidiNote(36, 0.0, 0.5, 120),)

    compose_only_clips = [
        c for c in base_cand.clips if c.part in {"Bass", "Stab", "Lead", "Arp", "Perc", "Hats"}
    ]
    custom_clips = tuple(
        compose_only_clips
        + [
            CandidateClip("Kick", "Drop", 9, 4, drop_kick_notes),
            CandidateClip("Snare", "Drop", 9, 4, drop_snare_notes),
            CandidateClip("Pad", "Drop", 9, 4, drop_pad_notes),
            CandidateClip("Kick", "Drop2", 13, 4, existing_drop2_kick_notes),
        ]
    )
    runtime._candidates[base_id] = dc_replace(base_cand, clips=custom_clips)

    sections = [
        {"name": "Intro", "bars": 4},
        {"name": "Intro2", "bars": 4},
        {"name": "Drop", "bars": 4},
        {"name": "Drop2", "bars": 4},
        {"name": "Outro", "bars": 4},
    ]
    spec = {
        "genre": "mutation_funk",
        "tempo": 124,
        "key": "Gm",
        "bars": 20,
        "sections": sections,
        "arrangement": {
            "activity": {
                "Intro": ["bass"],
                "Intro2": ["bass"],
                "Drop": ["bass"],
                "Drop2": ["bass"],
                "Outro": ["bass"],
            },
            "repeat": {
                "Intro2": {"from": "Intro"},
                "Drop2": {"from": "Drop", "regenerate": ["bass"]},
            },
        },
    }
    spec_path = tmp_path / "spec_repeat_base.json"
    spec_path.write_text(json.dumps(spec), encoding="utf-8")

    compose_calls: list[tuple[int, str]] = []

    def fake_get(_path: str, timeout: float = 60.0) -> dict[str, Any]:
        cand = runtime.get_candidate(base_id)
        assert cand is not None
        return {"ok": True, "candidate": cand.to_dict()}

    def fake_post(path: str, payload: dict[str, Any], timeout: float = 600.0) -> dict[str, Any]:
        if path == "/api/midi/compose":
            sb = int(payload["start_bar"])
            role = str(payload["constraints"]["role"])
            compose_calls.append((sb, role))
            notes = [{"pitch": 43 if sb != 13 else 55, "start": 0.0, "duration": 0.5, "velocity": 100}]
            return {"ok": True, "notes": notes, "note_count": len(notes)}
        if path == "/api/midi/adopt-preview":
            return runtime.propose_composer_adoption(
                candidate_id=payload["candidate_id"],
                role=payload["role"],
                pattern_bars=payload["pattern_bars"],
                notes=payload["notes"],
                section_name=payload["section_name"],
                sections=payload.get("sections"),
            )
        if path == "/api/revision/decide":
            return runtime.decide_revision(payload["revision_id"], accept=bool(payload["accept"]))
        raise AssertionError(f"unexpected path: {path}")

    monkeypatch.setattr(driver, "get", fake_get)
    monkeypatch.setattr(driver, "post", fake_post)

    rc = driver.main(["--spec", str(spec_path), "--candidate", base_id, "--yes"])
    assert rc == 0
    # regenerate 指定された Drop2/bass のみ再生成され、Intro2/bass は再生成されない
    assert (13, "bass") in compose_calls
    assert (5, "bass") not in compose_calls

    log_data = json.loads((tmp_path / "phase_v_run_log.json").read_text(encoding="utf-8"))
    final_cand = runtime.get_candidate(log_data[-1]["candidate_id"])
    assert final_cand is not None

    # 1. コピー元に土台パートがある場合、繰り返し先に同一ノートのクリップが出来る
    drop2_snares = [c for c in final_cand.clips if c.section_name == "Drop2" and c.part == "Snare"]
    drop2_pads = [c for c in final_cand.clips if c.section_name == "Drop2" and c.part == "Pad"]
    assert len(drop2_snares) == 1
    assert len(drop2_pads) == 1
    assert drop2_snares[0].notes == drop_snare_notes
    assert drop2_pads[0].notes == drop_pad_notes

    # 2. コピー元に土台パートがない場合（Intro）は Intro2 に土台パートが追加されない
    intro2_base = [
        c
        for c in final_cand.clips
        if c.section_name == "Intro2" and c.part not in {"Bass", "Stab", "Lead", "Arp", "Perc"}
    ]
    assert intro2_base == []

    # 3. 既存クリップがある繰り返し先（Drop2/Kick）で重複が起きず既存が残る
    drop2_kicks = [c for c in final_cand.clips if c.section_name == "Drop2" and c.part == "Kick"]
    assert len(drop2_kicks) == 1
    assert drop2_kicks[0].notes == existing_drop2_kick_notes


def test_collect_repeat_base_clips_uses_current_chain_and_supports_chained_repeat(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    """土台コピーは現在の候補チェーンから取得し、連鎖 repeat（Drop2R←Drop2←Drop）と非タイル一致、Verse系無追加を満たすこと。"""
    from dataclasses import replace as dc_replace

    from kihachi_mcp.models.midi_candidate import CandidateClip, MidiNote

    driver = _load_driver()
    monkeypatch.setattr(driver, "__file__", str(tmp_path / "phase_v_driver.py"))

    runtime, _live = _studio(tmp_path)
    base_id = _prepare_candidate(runtime, seed=909)
    base_cand = runtime.get_candidate(base_id)
    assert base_cand is not None

    # 初期土台候補: Build は 4小節の短い Kick（もしここから拾うと Build2 で 4小節タイルになる）
    initial_build_kick_notes = (MidiNote(36, 0.0, 0.25, 110),)
    # 実行中の Build 実セクション（8小節化＋固有ノート配置済み）で期待されるノート
    actual_build_kick_notes = (
        MidiNote(36, 0.0, 0.25, 110),
        MidiNote(36, 20.0, 0.25, 96),
    )
    drop_kick_notes = (
        MidiNote(36, 0.0, 0.25, 112),
        MidiNote(36, 2.0, 0.25, 108),
    )
    drop_snare_notes = (MidiNote(38, 1.0, 0.25, 102),)

    compose_only_clips = [
        c for c in base_cand.clips if c.part in {"Bass", "Stab", "Lead", "Arp", "Perc", "Hats"}
    ]
    custom_clips = tuple(
        compose_only_clips
        + [
            CandidateClip("Kick", "Build", 5, 4, initial_build_kick_notes),
            CandidateClip("Kick", "Drop", 9, 4, drop_kick_notes),
            CandidateClip("Snare", "Drop", 9, 4, drop_snare_notes),
        ]
    )
    runtime._candidates[base_id] = dc_replace(base_cand, clips=custom_clips)

    sections = [
        {"name": "Verse", "bars": 4},
        {"name": "Verse2", "bars": 4},
        {"name": "Build", "bars": 8},
        {"name": "Build2", "bars": 8},
        {"name": "Drop", "bars": 4},
        {"name": "Drop2", "bars": 4},
        {"name": "Drop2R", "bars": 4},
    ]
    spec = {
        "genre": "mutation_funk",
        "tempo": 124,
        "key": "Gm",
        "bars": 40,
        "sections": sections,
        "arrangement": {
            "activity": {
                "Verse": ["bass"],
                "Verse2": ["bass"],
                "Build": ["bass"],
                "Build2": ["bass"],
                "Drop": ["bass"],
                "Drop2": ["bass"],
                "Drop2R": ["bass"],
            },
            "repeat": {
                "Verse2": {"from": "Verse"},
                "Build2": {"from": "Build"},
                "Drop2": {"from": "Drop", "regenerate": ["bass"]},
                "Drop2R": {"from": "Drop2"},
            },
        },
    }
    spec_path = tmp_path / "spec_chain_repeat.json"
    spec_path.write_text(json.dumps(spec), encoding="utf-8")

    def fake_get(path: str, timeout: float = 60.0) -> dict[str, Any]:
        cand_id = path.rsplit("/", 1)[-1]
        cand = runtime.get_candidate(cand_id)
        assert cand is not None
        return {"ok": True, "candidate": cand.to_dict()}

    def fake_post(path: str, payload: dict[str, Any], timeout: float = 600.0) -> dict[str, Any]:
        if path == "/api/midi/compose":
            notes = [{"pitch": 43, "start": 0.0, "duration": 0.5, "velocity": 100}]
            return {"ok": True, "notes": notes, "note_count": len(notes)}
        if path == "/api/midi/adopt-preview":
            return runtime.propose_composer_adoption(
                candidate_id=payload["candidate_id"],
                role=payload["role"],
                pattern_bars=payload["pattern_bars"],
                notes=payload["notes"],
                section_name=payload["section_name"],
                sections=payload.get("sections"),
            )
        if path == "/api/revision/decide":
            res = runtime.decide_revision(payload["revision_id"], accept=bool(payload["accept"]))
            # Build/bass 採用後のチェーン状態で Build/Kick が 8小節実クリップに更新された状況を模擬
            new_cid = res.get("selected_candidate_id")
            if new_cid:
                cand = runtime.get_candidate(new_cid)
                if cand is not None and any(
                    c.section_name == "Build" and c.part == "Bass" and c.length_bars == 8
                    for c in cand.clips
                ):
                    updated_clips = tuple(
                        dc_replace(c, length_bars=8, notes=actual_build_kick_notes)
                        if c.section_name == "Build" and c.part == "Kick"
                        else c
                        for c in cand.clips
                    )
                    runtime._candidates[new_cid] = dc_replace(cand, clips=updated_clips)
            return res
        raise AssertionError(f"unexpected path: {path}")

    monkeypatch.setattr(driver, "get", fake_get)
    monkeypatch.setattr(driver, "post", fake_post)

    rc = driver.main(["--spec", str(spec_path), "--candidate", base_id, "--yes"])
    assert rc == 0

    log_data = json.loads((tmp_path / "phase_v_run_log.json").read_text(encoding="utf-8"))
    final_cand = runtime.get_candidate(log_data[-1]["candidate_id"])
    assert final_cand is not None

    # 1. Drop2R のような連鎖先にも最終コピー元（Drop）と同一ノートの土台パートが入る
    for sec in ("Drop2", "Drop2R"):
        kicks = [c for c in final_cand.clips if c.section_name == sec and c.part == "Kick"]
        snares = [c for c in final_cand.clips if c.section_name == sec and c.part == "Snare"]
        assert len(kicks) == 1
        assert len(snares) == 1
        assert kicks[0].notes == drop_kick_notes
        assert snares[0].notes == drop_snare_notes

    # 2. Build2 の土台パートが、その実行の Build セクションとノート単位で一致する（タイルにならない）
    build_kicks = [c for c in final_cand.clips if c.section_name == "Build" and c.part == "Kick"]
    build2_kicks = [c for c in final_cand.clips if c.section_name == "Build2" and c.part == "Kick"]
    assert len(build_kicks) == 1
    assert len(build2_kicks) == 1
    assert build_kicks[0].notes == actual_build_kick_notes
    assert build2_kicks[0].notes == build_kicks[0].notes

    # 3. 土台パートのないセクション（Verse 系）には何も追加されない
    verse_base = [
        c
        for c in final_cand.clips
        if c.section_name in {"Verse", "Verse2"}
        and c.part not in {"Bass", "Stab", "Lead", "Arp", "Perc", "Hats"}
    ]
    assert verse_base == []


def test_phase_v_driver_applies_songspec_tempo_to_candidate_plan_and_readback(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """SongSpec に tempo 指定がある場合、候補・適用計画・読み戻し検証に反映されること。"""
    driver = _load_driver()
    monkeypatch.setattr(driver, "__file__", str(tmp_path / "phase_v_driver.py"))

    runtime, live = _studio(tmp_path)
    base_id = _prepare_candidate(runtime, seed=31)
    base_cand = runtime.get_candidate(base_id)
    assert base_cand is not None
    assert base_cand.brief.tempo.value == 124
    live.tempo = 124.0

    spec = {
        "genre": "mutation_funk",
        "tempo": 122,
        "key": "Gm",
        "bars": 16,
        "sections": [
            {"name": "Intro", "bars": 4, "energy": 0.4, "chords": ["Gm"]},
            {"name": "Build", "bars": 4, "energy": 0.6, "chords": ["Gm"]},
            {"name": "Drop", "bars": 4, "energy": 0.9, "chords": ["Gm"]},
            {"name": "Outro", "bars": 4, "energy": 0.4, "chords": ["Gm"]},
        ],
        "arrangement": {"activity": {"Intro": ["bass"]}},
    }
    spec_path = tmp_path / "spec_tempo_122.json"
    spec_path.write_text(json.dumps(spec), encoding="utf-8")

    def fake_post(path: str, payload: dict[str, Any], timeout: float = 600.0) -> dict[str, Any]:
        if path == "/api/midi/compose":
            notes = [{"pitch": 43, "start": 0.0, "duration": 0.5, "velocity": 100}]
            return {"ok": True, "notes": notes, "note_count": len(notes)}
        if path == "/api/midi/adopt-preview":
            return runtime.propose_composer_adoption(
                candidate_id=payload["candidate_id"],
                role=payload["role"],
                pattern_bars=payload["pattern_bars"],
                notes=payload["notes"],
                section_name=payload["section_name"],
                sections=payload.get("sections"),
                tempo=payload.get("tempo"),
            )
        if path == "/api/revision/decide":
            return runtime.decide_revision(payload["revision_id"], accept=bool(payload["accept"]))
        raise AssertionError(f"unexpected path: {path}")

    monkeypatch.setattr(driver, "post", fake_post)

    rc = driver.main(["--spec", str(spec_path), "--candidate", base_id, "--yes"])
    assert rc == 0

    log_data = json.loads((tmp_path / "phase_v_run_log.json").read_text(encoding="utf-8"))
    final_id = str(log_data[-1]["candidate_id"])
    final_cand = runtime.get_candidate(final_id)
    assert final_cand is not None
    assert final_cand.brief.tempo.value == 122
    assert final_cand.brief.tempo.source == "user"

    preview = runtime.apply_preview(final_id, change_tempo=True, skip_instruments=True)
    assert preview["ok"] is True
    tempo_ops = [op for op in preview["plan"]["operations"] if op["op"] == "set_tempo"]
    assert len(tempo_ops) == 1
    assert tempo_ops[0]["arguments"]["tempo"] == 122.0
    assert tempo_ops[0]["expected_readback"]["tempo"] == 122.0

    assert runtime.approve(final_id)["ok"] is True
    dry = runtime.dry_run(final_id, change_tempo=True, skip_instruments=True)
    assert dry["ok"] is True, dry
    applied = runtime.send_to_ableton(
        final_id, confirmed=True, change_tempo=True, skip_instruments=True
    )
    assert applied["ok"] is True, applied
    assert applied["delivery_status"] == "VERIFIED"
    assert live.tempo == 122.0
    tempo_lines = [
        line
        for line in applied["verification"]["lines"]
        if line["name"] == "Tempo"
    ]
    assert len(tempo_lines) == 1
    assert tempo_lines[0]["status"] == "PASS"
    assert "122" in tempo_lines[0]["detail"]


def test_phase_v_driver_without_songspec_tempo_keeps_base_tempo(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """SongSpec に tempo 未指定の場合、従来どおり土台候補のテンポを維持すること。"""
    driver = _load_driver()
    monkeypatch.setattr(driver, "__file__", str(tmp_path / "phase_v_driver.py"))

    runtime, _live = _studio(tmp_path)
    base_id = _prepare_candidate(runtime, seed=32)
    base_cand = runtime.get_candidate(base_id)
    assert base_cand is not None
    assert base_cand.brief.tempo.value == 124

    spec = {
        "genre": "mutation_funk",
        "key": "Gm",
        "bars": 16,
        "sections": [
            {"name": "Intro", "bars": 4, "energy": 0.4, "chords": ["Gm"]},
            {"name": "Build", "bars": 4, "energy": 0.6, "chords": ["Gm"]},
            {"name": "Drop", "bars": 4, "energy": 0.9, "chords": ["Gm"]},
            {"name": "Outro", "bars": 4, "energy": 0.4, "chords": ["Gm"]},
        ],
        "arrangement": {"activity": {"Intro": ["bass"]}},
    }
    spec_path = tmp_path / "spec_no_tempo.json"
    spec_path.write_text(json.dumps(spec), encoding="utf-8")

    adopt_payloads: list[dict[str, Any]] = []

    def fake_post(path: str, payload: dict[str, Any], timeout: float = 600.0) -> dict[str, Any]:
        if path == "/api/midi/compose":
            notes = [{"pitch": 43, "start": 0.0, "duration": 0.5, "velocity": 100}]
            return {"ok": True, "notes": notes, "note_count": len(notes)}
        if path == "/api/midi/adopt-preview":
            adopt_payloads.append(dict(payload))
            return runtime.propose_composer_adoption(
                candidate_id=payload["candidate_id"],
                role=payload["role"],
                pattern_bars=payload["pattern_bars"],
                notes=payload["notes"],
                section_name=payload["section_name"],
                sections=payload.get("sections"),
                tempo=payload.get("tempo"),
            )
        if path == "/api/revision/decide":
            return runtime.decide_revision(payload["revision_id"], accept=bool(payload["accept"]))
        raise AssertionError(f"unexpected path: {path}")

    monkeypatch.setattr(driver, "post", fake_post)

    rc = driver.main(["--spec", str(spec_path), "--candidate", base_id, "--yes"])
    assert rc == 0
    assert adopt_payloads
    assert all("tempo" not in payload for payload in adopt_payloads)

    log_data = json.loads((tmp_path / "phase_v_run_log.json").read_text(encoding="utf-8"))
    final_cand = runtime.get_candidate(str(log_data[-1]["candidate_id"]))
    assert final_cand is not None
    assert final_cand.brief.tempo.value == 124


def test_phase_v_driver_reuse_finds_variant_parent_part_clip(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """sound_variants で変容先パート（例: BassMut）に振り分けられたクリップを --reuse で再利用できること。"""
    driver = _load_driver()
    monkeypatch.setattr(driver, "__file__", str(tmp_path / "phase_v_driver.py"))

    runtime, _live = _studio(tmp_path)
    base_id = _prepare_candidate(runtime, seed=33)

    # 再利用元候補: Drop の Bass が sound_variants により BassMut に振り分けられている
    variant_arrangement = {
        "activity": {"Drop": ["bass"]},
        "sound_variants": {"bass": {"variant_part": "BassMut", "sections": ["Drop"]}},
    }
    reused_notes = [
        {"pitch": 31, "start": 0.0, "duration": 0.5, "velocity": 110},
        {"pitch": 43, "start": 1.0, "duration": 0.5, "velocity": 95},
    ]
    prop_src = runtime.propose_composer_adoption(
        candidate_id=base_id,
        role="bass",
        pattern_bars=4,
        notes=reused_notes,
        section_name="Drop",
        arrangement=variant_arrangement,
    )
    assert prop_src["ok"] is True
    src_id = str(
        runtime.decide_revision(prop_src["revision"]["revision_id"], accept=True)[
            "selected_candidate_id"
        ]
    )
    src_cand = runtime.get_candidate(src_id)
    assert src_cand is not None
    assert src_cand.variant_parents == {"BassMut": "Bass"}
    assert any(c.section_name == "Drop" and c.part == "BassMut" for c in src_cand.clips)
    assert not any(c.section_name == "Drop" and c.part == "Bass" for c in src_cand.clips)

    spec = {
        "genre": "mutation_funk",
        "tempo": 124,
        "key": "Gm",
        "bars": 16,
        "sections": [
            {"name": "Intro", "bars": 4, "energy": 0.4, "chords": ["Gm"]},
            {"name": "Build", "bars": 4, "energy": 0.6, "chords": ["Gm"]},
            {"name": "Drop", "bars": 4, "energy": 0.9, "chords": ["Gm"]},
            {"name": "Outro", "bars": 4, "energy": 0.4, "chords": ["Gm"]},
        ],
        "arrangement": variant_arrangement,
    }
    spec_path = tmp_path / "spec_reuse_variant.json"
    spec_path.write_text(json.dumps(spec), encoding="utf-8")

    compose_calls: list[tuple[str, str]] = []

    def fake_get(path: str, timeout: float = 60.0) -> dict[str, Any]:
        cand_id = path.rsplit("/", 1)[-1]
        cand = runtime.get_candidate(cand_id)
        assert cand is not None
        return {"ok": True, "candidate": cand.to_dict()}

    def fake_post(path: str, payload: dict[str, Any], timeout: float = 600.0) -> dict[str, Any]:
        if path == "/api/midi/compose":
            c = payload["constraints"]
            compose_calls.append((str(payload.get("start_bar")), str(c["role"])))
            notes = [{"pitch": 43, "start": 0.0, "duration": 0.5, "velocity": 100}]
            return {"ok": True, "notes": notes, "note_count": len(notes)}
        if path == "/api/midi/adopt-preview":
            return runtime.propose_composer_adoption(
                candidate_id=payload["candidate_id"],
                role=payload["role"],
                pattern_bars=payload["pattern_bars"],
                notes=payload["notes"],
                section_name=payload["section_name"],
                arrangement=payload.get("arrangement"),
                sections=payload.get("sections"),
                tempo=payload.get("tempo"),
            )
        if path == "/api/revision/decide":
            return runtime.decide_revision(payload["revision_id"], accept=bool(payload["accept"]))
        raise AssertionError(f"unexpected path: {path}")

    monkeypatch.setattr(driver, "get", fake_get)
    monkeypatch.setattr(driver, "post", fake_post)

    rc = driver.main(
        [
            "--spec",
            str(spec_path),
            "--candidate",
            base_id,
            "--reuse-from",
            src_id,
            "--reuse",
            "Drop:bass",
            "--yes",
        ]
    )
    assert rc == 0
    # Drop:bass は compose されず再利用されていること
    assert ("9", "bass") not in compose_calls

    log_data = json.loads((tmp_path / "phase_v_run_log.json").read_text(encoding="utf-8"))
    final_cand = runtime.get_candidate(str(log_data[-1]["candidate_id"]))
    assert final_cand is not None
    drop_bassmut = [c for c in final_cand.clips if c.section_name == "Drop" and c.part == "BassMut"]
    assert len(drop_bassmut) == 1
    assert [(n.pitch, n.start_beats, n.duration_beats, n.velocity) for n in drop_bassmut[0].notes] == [
        (31, 0.0, 0.5, 110),
        (43, 1.0, 0.5, 95),
    ]

    # 通常パートにも変容先パートにも存在しない場合は従来どおりエラーで止まること
    rc_missing = driver.main(
        [
            "--spec",
            str(spec_path),
            "--candidate",
            base_id,
            "--reuse-from",
            src_id,
            "--reuse",
            "Drop:guitar",
            "--yes",
        ]
    )
    assert rc_missing == 1
