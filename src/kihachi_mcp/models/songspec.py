import math
from dataclasses import dataclass, field
from typing import Any, Self

_MIX_DB_MIN = -24.0
_MIX_DB_MAX = 12.0


@dataclass(frozen=True)
class SectionSpec:
    """One section of a song: its length, energy, chords and active tracks.

    ``chords`` divides the section evenly: with N chords over B bars, chord
    index ``(local_bar * N) // B`` applies, so a 16-bar section with four
    chords gives each chord four bars. An empty ``tracks`` means every track
    in the SongSpec participates.
    """

    name: str
    bars: int
    energy: float = 0.5
    chords: tuple[str, ...] = ()
    tracks: tuple[str, ...] = ()
    note: str = ""

    def __post_init__(self) -> None:
        if not str(self.name or "").strip():
            raise ValueError("section name must not be empty")
        if self.bars < 1:
            raise ValueError("section bars must be at least 1")
        if not 0 <= self.energy <= 1:
            raise ValueError("section energy must be between 0 and 1")
        object.__setattr__(self, "chords", tuple(str(c) for c in self.chords))
        object.__setattr__(self, "tracks", tuple(str(t) for t in self.tracks))

    def chord_at(self, local_bar: int) -> str | None:
        """Chord symbol for a 0-based bar inside this section, if any."""
        if not self.chords or not 0 <= local_bar < self.bars:
            return None
        return self.chords[(local_bar * len(self.chords)) // self.bars]

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "bars": self.bars,
            "energy": self.energy,
            "chords": list(self.chords),
            "tracks": list(self.tracks),
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        return cls(
            name=str(data.get("name") or ""),
            bars=int(data.get("bars") or 0),
            energy=float(data.get("energy") if data.get("energy") is not None else 0.5),
            chords=tuple(str(c) for c in data.get("chords") or ()),
            tracks=tuple(str(t) for t in data.get("tracks") or ()),
            note=str(data.get("note") or ""),
        )


@dataclass(frozen=True)
class GrooveSpec:
    """Groove defaults for a song, typically seeded from a StyleProfile.

    ``None`` density/syncopation/rest values mean "unspecified"; the
    generation layer then falls back to its own defaults.
    """

    swing: float = 0.5
    kick_style: str = ""
    bass_density: float | None = None
    syncopation: float | None = None
    rest_ratio: float | None = None
    preserve_space: bool = True
    dynamic_profile: str = ""
    profile_id: str = ""

    def __post_init__(self) -> None:
        if not 0 <= self.swing <= 1:
            raise ValueError("swing must be between 0 and 1")
        for name in ("syncopation", "rest_ratio"):
            value = getattr(self, name)
            if value is not None and not 0 <= value <= 1:
                raise ValueError(f"{name} must be between 0 and 1")
        if self.bass_density is not None and self.bass_density <= 0:
            raise ValueError("bass_density must be positive")

    def to_dict(self) -> dict[str, Any]:
        return {
            "swing": self.swing,
            "kick_style": self.kick_style,
            "bass_density": self.bass_density,
            "syncopation": self.syncopation,
            "rest_ratio": self.rest_ratio,
            "preserve_space": self.preserve_space,
            "dynamic_profile": self.dynamic_profile,
            "profile_id": self.profile_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        def _opt_float(key: str) -> float | None:
            value = data.get(key)
            return float(value) if value is not None else None

        return cls(
            swing=float(data.get("swing") if data.get("swing") is not None else 0.5),
            kick_style=str(data.get("kick_style") or ""),
            bass_density=_opt_float("bass_density"),
            syncopation=_opt_float("syncopation"),
            rest_ratio=_opt_float("rest_ratio"),
            preserve_space=bool(data.get("preserve_space", True)),
            dynamic_profile=str(data.get("dynamic_profile") or ""),
            profile_id=str(data.get("profile_id") or ""),
        )


@dataclass(frozen=True)
class SongSpec:
    """Musical specification used to build a KIHACHI project.

    The v1 fields (genre/tempo/key/length/bars/tracks) remain the public
    contract. ``sections`` and ``groove`` are the v2 additions: when absent,
    ``to_dict`` produces exactly the v1 JSON shape.
    """

    genre: str
    tempo: int
    key: str
    length_minutes: float
    bars: int
    tracks: list[str]
    sections: tuple[SectionSpec, ...] = ()
    groove: GrooveSpec | None = None
    # パート／ロール別の奏法・音色・役割定義（省略時は空で従来と同一JSON）
    elements: dict[str, dict[str, Any]] = field(default_factory=dict)
    # セクション×パート別の上書き elements 定義 {section: {role: dict}}（省略時は空で従来と同一JSON）
    section_elements: dict[str, dict[str, dict[str, Any]]] = field(default_factory=dict)
    # ドライバ等で有効化する追加セル定義 {role: [section, ...]}（省略時は空で従来17セル）
    compose_cells: dict[str, list[str]] = field(default_factory=dict)
    # セクション別活性・密度・コール＆レスポンス定義（省略時は空で従来と同一JSON）
    arrangement: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        sections = tuple(
            s if isinstance(s, SectionSpec) else SectionSpec.from_dict(s)
            for s in self.sections
        )
        object.__setattr__(self, "sections", sections)
        if sections:
            total = sum(s.bars for s in sections)
            if total != self.bars:
                raise ValueError(
                    f"section bars total {total} does not match song bars {self.bars}"
                )
        normalized_elements: dict[str, dict[str, Any]] = {}
        for role_key, spec in (self.elements or {}).items():
            if not isinstance(spec, dict):
                raise TypeError(f"elements['{role_key}'] must be a mapping")
            normalized_key = str(role_key).strip().lower()
            if normalized_key:
                normalized_elements[normalized_key] = dict(spec)
        object.__setattr__(self, "elements", normalized_elements)
        normalized_section_elements: dict[str, dict[str, dict[str, Any]]] = {}
        for sec_key, parts in (self.section_elements or {}).items():
            if not isinstance(parts, dict):
                raise TypeError(f"section_elements['{sec_key}'] must be a mapping")
            sec_name = str(sec_key).strip()
            if not sec_name:
                continue
            sec_parts: dict[str, dict[str, Any]] = {}
            for role_key, spec in parts.items():
                if not isinstance(spec, dict):
                    raise TypeError(
                        f"section_elements['{sec_key}']['{role_key}'] must be a mapping"
                    )
                normalized_role = str(role_key).strip().lower()
                if normalized_role:
                    sec_parts[normalized_role] = dict(spec)
            if sec_parts:
                normalized_section_elements[sec_name] = sec_parts
        object.__setattr__(self, "section_elements", normalized_section_elements)
        normalized_cells: dict[str, list[str]] = {}
        for role_key, sec_list in (self.compose_cells or {}).items():
            if not isinstance(sec_list, (list, tuple)):
                raise TypeError(f"compose_cells['{role_key}'] must be a sequence")
            normalized_key = str(role_key).strip().lower()
            if normalized_key:
                normalized_cells[normalized_key] = [str(s).strip() for s in sec_list if str(s).strip()]
        object.__setattr__(self, "compose_cells", normalized_cells)
        object.__setattr__(
            self,
            "arrangement",
            _normalize_arrangement(self.arrangement or {}),
        )

    def section_at(self, bar: int) -> SectionSpec | None:
        """Section containing the 0-based absolute bar, if sections exist."""
        start = 0
        for section in self.sections:
            if start <= bar < start + section.bars:
                return section
            start += section.bars
        return None

    def chord_at(self, bar: int) -> str | None:
        """Chord symbol in force at the 0-based absolute bar, if specified."""
        start = 0
        for section in self.sections:
            if start <= bar < start + section.bars:
                return section.chord_at(bar - start)
            start += section.bars
        return None

    def chords_for_span(self, start_bar: int, bar_count: int) -> tuple[str, ...]:
        """Chord symbol for each bar of ``[start_bar, start_bar + bar_count)``.

        Returns "" for bars whose section names no chord. This is the span a
        per-clip ``MusicalConstraints.chords_by_bar`` is built from.
        """
        if start_bar < 0 or bar_count < 1 or start_bar + bar_count > self.bars:
            raise ValueError("span is outside the song")
        return tuple(self.chord_at(start_bar + offset) or "" for offset in range(bar_count))

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        """Create a SongSpec from a JSON-compatible dict."""
        length_minutes = float(data.get("length_minutes") or 0)
        sections = tuple(
            SectionSpec.from_dict(item) for item in data.get("sections") or ()
        )
        raw_bars = data.get("bars")
        if raw_bars is not None:
            bars = int(raw_bars)
        elif sections:
            bars = sum(s.bars for s in sections)
        else:
            bars = max(1, round(length_minutes * 32))
        groove_data = data.get("groove")
        raw_elements = data.get("elements")
        if raw_elements is not None and not isinstance(raw_elements, dict):
            raise TypeError("SongSpec elements must be a mapping")
        raw_section_elements = data.get("section_elements")
        if raw_section_elements is not None and not isinstance(raw_section_elements, dict):
            raise TypeError("SongSpec section_elements must be a mapping")
        raw_cells = data.get("compose_cells")
        if raw_cells is not None and not isinstance(raw_cells, dict):
            raise TypeError("SongSpec compose_cells must be a mapping")
        raw_arrangement = data.get("arrangement")
        if raw_arrangement is not None and not isinstance(raw_arrangement, dict):
            raise TypeError("SongSpec arrangement must be a mapping")
        return cls(
            genre=str(data.get("genre") or ""),
            tempo=int(data.get("tempo") or 0),
            key=str(data.get("key") or ""),
            length_minutes=length_minutes,
            bars=max(1, bars),
            tracks=[str(name) for name in data.get("tracks") or []],
            sections=sections,
            groove=GrooveSpec.from_dict(groove_data) if groove_data else None,
            elements=dict(raw_elements) if raw_elements else {},
            section_elements=dict(raw_section_elements) if raw_section_elements else {},
            compose_cells=dict(raw_cells) if raw_cells else {},
            arrangement=dict(raw_arrangement) if raw_arrangement else {},
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize this SongSpec to a JSON-compatible dict."""
        data: dict[str, Any] = {
            "genre": self.genre,
            "tempo": self.tempo,
            "key": self.key,
            "length_minutes": self.length_minutes,
            "bars": self.bars,
            "tracks": list(self.tracks),
        }
        if self.sections:
            data["sections"] = [section.to_dict() for section in self.sections]
        if self.groove is not None:
            data["groove"] = self.groove.to_dict()
        if self.elements:
            data["elements"] = {k: dict(v) for k, v in self.elements.items()}
        if self.section_elements:
            data["section_elements"] = {
                sec: {role: dict(spec) for role, spec in parts.items()}
                for sec, parts in self.section_elements.items()
            }
        if self.compose_cells:
            data["compose_cells"] = {k: list(v) for k, v in self.compose_cells.items()}
        if self.arrangement:
            data["arrangement"] = _copy_arrangement(self.arrangement)
        return data


def _normalize_arrangement(raw: dict[str, Any]) -> dict[str, Any]:
    """SongSpec.arrangement を検証・正規化する。"""
    if not isinstance(raw, dict):
        raise TypeError("SongSpec arrangement must be a mapping")
    normalized: dict[str, Any] = {}
    if "activity" in raw and raw["activity"] is not None:
        raw_act = raw["activity"]
        if not isinstance(raw_act, dict):
            raise TypeError("arrangement['activity'] must be a mapping")
        act: dict[str, list[str]] = {}
        for sec, roles in raw_act.items():
            if not isinstance(roles, (list, tuple)):
                raise TypeError(f"arrangement['activity']['{sec}'] must be a sequence")
            sec_name = str(sec).strip()
            if sec_name:
                act[sec_name] = [str(r).strip().lower() for r in roles if str(r).strip()]
        if act:
            normalized["activity"] = act
    if "density" in raw and raw["density"] is not None:
        raw_dens = raw["density"]
        if not isinstance(raw_dens, dict):
            raise TypeError("arrangement['density'] must be a mapping")
        dens: dict[str, int] = {}
        for role, limit in raw_dens.items():
            role_key = str(role).strip().lower()
            if not role_key:
                continue
            limit_int = int(limit)
            if limit_int < 1:
                raise ValueError(f"arrangement['density']['{role_key}'] must be at least 1")
            dens[role_key] = limit_int
        if dens:
            normalized["density"] = dens
    if "call_response" in raw and raw["call_response"] is not None:
        raw_cr = raw["call_response"]
        if not isinstance(raw_cr, (list, tuple)):
            raise TypeError("arrangement['call_response'] must be a sequence")
        cr_list: list[dict[str, Any]] = []
        for idx, item in enumerate(raw_cr):
            if not isinstance(item, dict):
                raise TypeError(f"arrangement['call_response'][{idx}] must be a mapping")
            call_role = str(item.get("call") or "").strip().lower()
            resp_role = str(item.get("response") or "").strip().lower()
            raw_secs = item.get("sections") or ()
            if not isinstance(raw_secs, (list, tuple)):
                raise TypeError(f"arrangement['call_response'][{idx}]['sections'] must be a sequence")
            secs = [str(s).strip() for s in raw_secs if str(s).strip()]
            cr_list.append({"call": call_role, "response": resp_role, "sections": secs})
        if cr_list:
            normalized["call_response"] = cr_list
    if "mix" in raw and raw["mix"] is not None:
        mix_dict = normalize_arrangement_mix(raw["mix"])
        if mix_dict:
            normalized["mix"] = mix_dict
    if "repeat" in raw and raw["repeat"] is not None:
        raw_rep = raw["repeat"]
        if not isinstance(raw_rep, dict):
            raise TypeError("arrangement['repeat'] must be a mapping")
        rep_dict: dict[str, dict[str, Any]] = {}
        for sec, cfg in raw_rep.items():
            sec_name = str(sec).strip()
            if not sec_name:
                continue
            if not isinstance(cfg, dict):
                raise TypeError(f"arrangement['repeat']['{sec_name}'] must be a mapping")
            from_sec = str(cfg.get("from") or "").strip()
            if not from_sec:
                raise ValueError(f"arrangement['repeat']['{sec_name}']['from'] must be non-empty")
            raw_regen = cfg.get("regenerate")
            if raw_regen is None:
                regen_roles: list[str] = []
            elif isinstance(raw_regen, (list, tuple)):
                regen_roles = [str(r).strip().lower() for r in raw_regen if str(r).strip()]
            else:
                raise TypeError(
                    f"arrangement['repeat']['{sec_name}']['regenerate'] must be a sequence"
                )
            rep_dict[sec_name] = {"from": from_sec, "regenerate": regen_roles}
        if rep_dict:
            normalized["repeat"] = rep_dict
    if "sound_variants" in raw and raw["sound_variants"] is not None:
        raw_sv = raw["sound_variants"]
        if not isinstance(raw_sv, dict):
            raise TypeError("arrangement['sound_variants'] must be a mapping")
        sv_dict: dict[str, dict[str, Any]] = {}
        for role, cfg in raw_sv.items():
            role_key = str(role).strip().lower()
            if not role_key:
                continue
            if not isinstance(cfg, dict):
                raise TypeError(
                    f"arrangement['sound_variants']['{role_key}'] must be a mapping"
                )
            variant_part = str(cfg.get("variant_part") or "").strip()
            if not variant_part:
                raise ValueError(
                    f"arrangement['sound_variants']['{role_key}']['variant_part'] must be non-empty"
                )
            raw_secs = cfg.get("sections")
            if not isinstance(raw_secs, (list, tuple)):
                raise TypeError(
                    f"arrangement['sound_variants']['{role_key}']['sections'] must be a sequence"
                )
            secs = [str(s).strip() for s in raw_secs if str(s).strip()]
            sv_dict[role_key] = {"variant_part": variant_part, "sections": secs}
        if sv_dict:
            normalized["sound_variants"] = sv_dict
    return normalized


def normalize_arrangement_mix(raw_mix: Any) -> dict[str, float]:
    """arrangement.mix のパート別 dB 指定を検証し -24.0〜+12.0 にクランプする。"""
    if not isinstance(raw_mix, dict):
        raise TypeError("arrangement['mix'] must be a mapping")
    normalized: dict[str, float] = {}
    for part, db_val in raw_mix.items():
        part_key = str(part).strip()
        if not part_key:
            continue
        if isinstance(db_val, bool) or not isinstance(db_val, (int, float)):
            raise TypeError(f"arrangement['mix']['{part_key}'] must be a number")
        val = float(db_val)
        if not math.isfinite(val):
            raise ValueError(f"arrangement['mix']['{part_key}'] must be finite")
        normalized[part_key] = max(_MIX_DB_MIN, min(_MIX_DB_MAX, val))
    return normalized


def _copy_arrangement(arr: dict[str, Any]) -> dict[str, Any]:
    """arrangement 辞書の防御的コピーを返す。"""
    copied: dict[str, Any] = {}
    if "activity" in arr:
        copied["activity"] = {k: list(v) for k, v in arr["activity"].items()}
    if "density" in arr:
        copied["density"] = dict(arr["density"])
    if "call_response" in arr:
        copied["call_response"] = [
            {"call": item["call"], "response": item["response"], "sections": list(item["sections"])}
            for item in arr["call_response"]
        ]
    if "mix" in arr:
        copied["mix"] = {str(k): float(v) for k, v in arr["mix"].items()}
    if "repeat" in arr:
        copied["repeat"] = {
            str(sec): {
                "from": str(cfg["from"]),
                "regenerate": list(cfg.get("regenerate") or ()),
            }
            for sec, cfg in arr["repeat"].items()
        }
    if "sound_variants" in arr:
        copied["sound_variants"] = {
            str(role): {
                "variant_part": str(cfg["variant_part"]),
                "sections": list(cfg.get("sections") or ()),
            }
            for role, cfg in arr["sound_variants"].items()
        }
    return copied
