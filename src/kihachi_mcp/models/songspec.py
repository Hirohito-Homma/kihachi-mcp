from dataclasses import dataclass
from typing import Any, Self


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
        return cls(
            genre=str(data.get("genre") or ""),
            tempo=int(data.get("tempo") or 0),
            key=str(data.get("key") or ""),
            length_minutes=length_minutes,
            bars=max(1, bars),
            tracks=[str(name) for name in data.get("tracks") or []],
            sections=sections,
            groove=GrooveSpec.from_dict(groove_data) if groove_data else None,
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
        return data
