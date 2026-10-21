from __future__ import annotations

from kihachi_mcp.models.instrument_spec import (
    InstrumentSpec,
    PitchRange,
    slap_bass_spec,
)
from kihachi_mcp.models.music_plan import (
    GenerationScope,
    MusicClip,
    MusicNote,
    MusicPlan,
    MusicTrack,
)
from kihachi_mcp.services.music_plan_validator import (
    MUSIC_VALIDATION_FAILED,
    validate_music_plan,
)


def test_slap_bass_spec_round_trips() -> None:
    spec = slap_bass_spec()
    assert InstrumentSpec.from_dict(spec.to_dict()) == spec
    assert spec.to_dict()["range"] == {"min": 28, "max": 64}
    assert spec.articulations["slap"] == 2


def test_polyphony_boundary_conditions() -> None:
    spec_poly2 = InstrumentSpec(
        instrument_id="poly2_lead",
        pitch_range=PitchRange(20, 80),
        preferred_range=PitchRange(30, 70),
        articulations={},
        velocity={},
        max_polyphony=2,
    )
    spec_poly1 = InstrumentSpec(
        instrument_id="poly1_bass",
        pitch_range=PitchRange(20, 80),
        preferred_range=PitchRange(30, 70),
        articulations={},
        velocity={},
        max_polyphony=1,
    )

    def _make_plan(notes: tuple[MusicNote, ...]) -> MusicPlan:
        return MusicPlan(
            tempo=120,
            key="C",
            bars=2,
            tracks=(MusicTrack("test_track", "TEST", clips=(MusicClip(1, 2, notes),)),),
            scope=GenerationScope(tracks=("test_track",), start_bar=1, length_bars=2),
        )

    # 1. max_polyphony=2: 長いノート1音に、互いには重ならない短いノート2音が異なる時刻で重なるケースは合格
    long_and_two_shorts = (
        MusicNote(60, 0.0, 4.0, 80),
        MusicNote(64, 1.0, 0.5, 80),
        MusicNote(67, 2.0, 0.5, 80),
    )
    res_a = validate_music_plan(_make_plan(long_and_two_shorts), {"test_track": spec_poly2})
    assert res_a.ok is True, [issue.message for issue in res_a.issues]

    # 2. max_polyphony=2: ある時点で3音が実際に重なるケースは不合格
    three_overlapping = (
        MusicNote(60, 0.0, 3.0, 80),
        MusicNote(64, 1.0, 2.0, 80),
        MusicNote(67, 1.5, 1.0, 80),
    )
    res_b = validate_music_plan(_make_plan(three_overlapping), {"test_track": spec_poly2})
    assert res_b.ok is False
    assert any(
        issue.code == MUSIC_VALIDATION_FAILED and "同時発音" in issue.message for issue in res_b.issues
    )

    # 3. max_polyphony=1: 同時刻重複ノートは不合格（既存のBass重複検出を維持）
    duplicate_start = (
        MusicNote(40, 1.0, 0.5, 80),
        MusicNote(52, 1.0, 0.5, 80),
    )
    res_c = validate_music_plan(_make_plan(duplicate_start), {"test_track": spec_poly1})
    assert res_c.ok is False
    assert any(
        issue.code == MUSIC_VALIDATION_FAILED and "同時発音" in issue.message for issue in res_c.issues
    )

    # 4. max_polyphony=1: 先行ノートの終了時刻と次ノート開始時刻が一致するケースは重複としない（合格）
    abutting_notes = (
        MusicNote(40, 0.0, 1.0, 80),
        MusicNote(40, 1.0, 1.0, 80),
    )
    res_d = validate_music_plan(_make_plan(abutting_notes), {"test_track": spec_poly1})
    assert res_d.ok is True, [issue.message for issue in res_d.issues]

    # 5. max_polyphony=1: 許容誤差 1e-6 内で接しているケースも重複としない（合格）
    near_abutting = (
        MusicNote(40, 0.0, 1.0, 80),
        MusicNote(40, 1.0 - 1e-7, 1.0, 80),
    )
    res_e = validate_music_plan(_make_plan(near_abutting), {"test_track": spec_poly1})
    assert res_e.ok is True, [issue.message for issue in res_e.issues]

    # 6. max_polyphony=1: 明らかに重なるケースは不合格
    truly_overlapping = (
        MusicNote(40, 0.0, 1.0, 80),
        MusicNote(40, 0.95, 1.0, 80),
    )
    res_f = validate_music_plan(_make_plan(truly_overlapping), {"test_track": spec_poly1})
    assert res_f.ok is False
    assert any(
        issue.code == MUSIC_VALIDATION_FAILED and "同時発音" in issue.message for issue in res_f.issues
    )

    # 7. max_polyphony=1: 同一開始時刻の短時間ノート2つ（duration <= 1e-6）も同時発音として不合格
    micro_notes_same_start = (
        MusicNote(40, 1.0, 1e-7, 80),
        MusicNote(52, 1.0, 1e-7, 80),
    )
    res_g = validate_music_plan(_make_plan(micro_notes_same_start), {"test_track": spec_poly1})
    assert res_g.ok is False
    assert any(
        issue.code == MUSIC_VALIDATION_FAILED and "同時発音" in issue.message for issue in res_g.issues
    )

    # 8. max_polyphony=1: 通常長ノートの中に短時間ノートが含まれる場合も不合格
    micro_note_inside_long = (
        MusicNote(40, 0.5, 2.0, 80),
        MusicNote(52, 1.0, 1e-7, 80),
    )
    res_h = validate_music_plan(_make_plan(micro_note_inside_long), {"test_track": spec_poly1})
    assert res_h.ok is False
    assert any(
        issue.code == MUSIC_VALIDATION_FAILED and "同時発音" in issue.message for issue in res_h.issues
    )

    # 9. max_polyphony=1: 短時間ノート同士が連続して接合する場合（終了時刻と次開始時刻が一致）は合格
    abutting_micro_notes = (
        MusicNote(40, 1.0, 1e-7, 80),
        MusicNote(40, 1.0 + 1e-7, 1e-7, 80),
    )
    res_i = validate_music_plan(_make_plan(abutting_micro_notes), {"test_track": spec_poly1})
    assert res_i.ok is True, [issue.message for issue in res_i.issues]
