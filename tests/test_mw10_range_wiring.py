"""MW10 pitch-range wiring: gateway distribution -> MusicalConstraints.

The gateway accumulates the player's pitch distribution in Obsidian
session notes; the composer's constraints can now carry that range, the
provider validation enforces it, and the studio context fills it from
OBSIDIAN_VAULT_PATH when the caller did not set one.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kihachi_mcp.models.midi_composer import ComposedMidiNote, MusicalConstraints
from kihachi_mcp.services.midi_composer import _validate_result
from kihachi_mcp.services.mw10_range import pitch_range_from_vault
from kihachi_mcp.services.studio_runtime import _apply_songspec_context


def _constraints(**overrides) -> MusicalConstraints:
    base = {"key": "D#m", "tempo": 110, "bars": 4, "role": "bass"}
    base.update(overrides)
    return MusicalConstraints(**base)


def _note(pitch: int) -> ComposedMidiNote:
    return ComposedMidiNote(pitch=pitch, start=0.0, duration=0.25, velocity=96)


def _write_vault(tmp_path: Path) -> Path:
    from automation_log import MidiStats, update_midi_log

    note = tmp_path / "Session_MW10_2026-11-09.md"
    note.write_text("# Session\n", encoding="utf-8")
    update_midi_log(
        tmp_path,
        MidiStats(
            session_id="s1",
            track_name="KIHACHI_Slap_Bass",
            total_notes=25,
            avg_velocity=94.6,
            max_velocity=118,
            min_velocity=35,
            note_distribution={"C3": 12, "D3": 8, "G#3": 5},
        ),
    )
    return tmp_path


def test_pitch_range_round_trip_and_omission() -> None:
    with_range = _constraints(pitch_range=(58, 70))
    data = with_range.to_dict()
    assert data["pitch_range"] == (58, 70)
    assert MusicalConstraints.from_dict(data).pitch_range == (58, 70)
    assert MusicalConstraints.from_dict({**data, "pitch_range": [58, 70]}).pitch_range == (58, 70)
    assert "pitch_range" not in _constraints().to_dict()


def test_pitch_range_validation() -> None:
    with pytest.raises(ValueError):
        _constraints(pitch_range=(70, 58))
    with pytest.raises(ValueError):
        _constraints(pitch_range=(0, 128))


def test_validate_result_enforces_pitch_range() -> None:
    c = _constraints(pitch_range=(58, 70))
    _validate_result((_note(63), _note(70)), c)  # in scale, in range
    with pytest.raises(ValueError, match="音域レンジ"):
        _validate_result((_note(71),), c)  # in scale (B), above the range
    # Without a range, the same note is fine (previous behaviour).
    _validate_result((_note(71),), _constraints())


def test_loader_reads_real_distribution(tmp_path: Path) -> None:
    vault = _write_vault(tmp_path)
    assert pitch_range_from_vault(vault) == (58, 70)


def test_loader_returns_none_without_data(tmp_path: Path) -> None:
    assert pitch_range_from_vault(tmp_path) is None
    assert pitch_range_from_vault(tmp_path / "missing") is None


def test_context_fills_range_from_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    vault = _write_vault(tmp_path)
    monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(vault))
    enriched = _apply_songspec_context({"bars": 4}, None, 1)
    assert enriched["pitch_range"] == [58, 70]


def test_context_caller_range_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    vault = _write_vault(tmp_path)
    monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(vault))
    enriched = _apply_songspec_context({"bars": 4, "pitch_range": [40, 50]}, None, 1)
    assert enriched["pitch_range"] == [40, 50]


def test_context_without_env_is_untouched(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OBSIDIAN_VAULT_PATH", raising=False)
    enriched = _apply_songspec_context({"bars": 4}, None, 1)
    assert "pitch_range" not in enriched
