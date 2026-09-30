"""Validated production intent that preserves the user's Japanese brief."""

from dataclasses import asdict, dataclass, field
from typing import Any, Self

from kihachi_mcp.knowledge.genre_database import is_known as is_known_genre

SOURCE_USER = "user"
SOURCE_AI = "ai"
SOURCE_DEFAULT = "default"
#: Taken from the genre database rather than from the brief or the model.
SOURCE_GENRE = "genre"
FIELD_SOURCES = frozenset({SOURCE_USER, SOURCE_AI, SOURCE_DEFAULT, SOURCE_GENRE})

#: What the local model may answer for genre. A genre the brief names is read
#: by rule from the 1020-row database instead, and any of those is valid.
SUPPORTED_GENRES = ("tech_house", "dub_techno", "melodic_techno")
DENSITY_VALUES = ("sparse", "normal", "dense")
REGISTER_VALUES = ("low", "mid", "high")
STUDIO_PARTS = ("Kick", "Hats", "Bass", "Stab")
#: Parts every genre gets when its rules write notes for them. Lead stays
#: mutation_funk only.
ARRANGEMENT_PARTS = (
    "Snare",
    "OpenHat",
    "Perc",
    "Sub",
    "Pad",
    "Arp",
    "Guitar",
    "Horn",
    "Vocal",
    "FX",
)
OPTIONAL_STUDIO_PARTS = ("Lead", *ARRANGEMENT_PARTS)
#: Live track order, top to bottom: drums, low end, harmony, top line, effects.
PART_ORDER = (
    "Kick",
    "Snare",
    "Hats",
    "OpenHat",
    "Perc",
    "Sub",
    "Bass",
    "Stab",
    "Pad",
    "Arp",
    "Guitar",
    "Horn",
    "Lead",
    "Vocal",
    "FX",
)
#: Parts played by a Drum Rack of one-shots, one pad per MIDI note.
DRUM_PARTS = frozenset({"Kick", "Snare", "Hats", "OpenHat", "Perc", "FX"})

KEY_ENUM = tuple(
    f"{root}{suffix}"
    for root in ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
    for suffix in ("", "m")
)


@dataclass(frozen=True)
class SourcedValue:
    """One field together with whether the user, the model, or a default set it."""

    value: Any
    source: str

    def __post_init__(self) -> None:
        if self.source not in FIELD_SOURCES:
            raise ValueError(f"unsupported field source '{self.source}'")

    def to_dict(self) -> dict[str, Any]:
        """Serialize the sourced value."""
        return {"value": self.value, "source": self.source}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        """Create a sourced value from JSON-compatible data."""
        return cls(value=data.get("value"), source=str(data.get("source") or SOURCE_DEFAULT))


@dataclass(frozen=True)
class SectionIntent:
    """One named bar range inside the song."""

    name: str
    start_bar: int
    length_bars: int

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("section name must not be empty")
        if self.start_bar < 1:
            raise ValueError("section start_bar must be at least 1")
        if self.length_bars < 1:
            raise ValueError("section length_bars must be at least 1")

    @property
    def end_bar(self) -> int:
        """Return the inclusive last bar of the section."""
        return self.start_bar + self.length_bars - 1

    def to_dict(self) -> dict[str, Any]:
        """Serialize the section."""
        data = asdict(self)
        data["end_bar"] = self.end_bar
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        """Create a section from JSON-compatible data."""
        return cls(
            name=str(data.get("name") or ""),
            start_bar=int(data.get("start_bar") or 0),
            length_bars=int(data.get("length_bars") or 0),
        )


@dataclass(frozen=True)
class ProductionBrief:
    """A reviewable interpretation of one Japanese production brief."""

    original_text: str
    tempo: SourcedValue
    key: SourcedValue
    bars: SourcedValue
    genre: SourcedValue
    mood: SourcedValue
    hats_first_half: SourcedValue
    hats_second_half: SourcedValue
    bass_register: SourcedValue
    note_density: SourcedValue
    drop_start_bar: SourcedValue
    meter_numerator: int = 4
    meter_denominator: int = 4
    sections: tuple[SectionIntent, ...] = field(default_factory=tuple)
    interpretations: tuple[str, ...] = field(default_factory=tuple)
    unhandled: tuple[str, ...] = field(default_factory=tuple)
    ambiguous: tuple[str, ...] = field(default_factory=tuple)
    contradictions: tuple[str, ...] = field(default_factory=tuple)
    model: str = "gemma4:latest"
    provider: str = "local_ollama"
    #: Tone controls, -2..+2 steps of a sound recipe. 0 leaves the recipe as is.
    tone_brightness: SourcedValue = field(default_factory=lambda: _no_tone())
    tone_length: SourcedValue = field(default_factory=lambda: _no_tone())
    tone_delay: SourcedValue = field(default_factory=lambda: _no_tone())

    def __post_init__(self) -> None:
        if not self.original_text.strip():
            raise ValueError("original_text must not be empty")
        if self.meter_numerator < 1 or self.meter_denominator < 1:
            raise ValueError("meter must be positive")
        _require_int(self.tempo, 60, 180, "tempo")
        _require_int(self.bars, 4, 256, "bars")
        if int(self.bars.value) % 4:
            raise ValueError("bars must be a multiple of four")
        if self.key.value not in KEY_ENUM:
            raise ValueError("unsupported key")
        if self.genre.value not in SUPPORTED_GENRES and not is_known_genre(
            str(self.genre.value)
        ):
            raise ValueError("unsupported genre")
        for name in (
            "hats_first_half",
            "hats_second_half",
            "note_density",
        ):
            sourced = getattr(self, name)
            if sourced.value not in DENSITY_VALUES:
                raise ValueError(f"unsupported {name}")
        if self.bass_register.value not in REGISTER_VALUES:
            raise ValueError("unsupported bass_register")
        drop = int(self.drop_start_bar.value)
        if drop < 0 or drop > int(self.bars.value):
            raise ValueError("drop_start_bar is outside the song")
        if drop and drop < 1:
            raise ValueError("drop_start_bar must be at least 1 when set")
        _assert_sections_cover(self.sections, int(self.bars.value))
        for name in TONE_FIELDS:
            _require_int(getattr(self, name), -2, 2, name)

    @property
    def beats_per_bar(self) -> float:
        """Return one bar in quarter-note beats."""
        return self.meter_numerator * 4.0 / self.meter_denominator

    @property
    def duration_minutes(self) -> float:
        """Return song length from bars, meter, and tempo."""
        return int(self.bars.value) * self.beats_per_bar / int(self.tempo.value)

    def to_dict(self) -> dict[str, Any]:
        """Serialize the brief for the UI and apply planner."""
        return {
            "original_text": self.original_text,
            "tempo": self.tempo.to_dict(),
            "key": self.key.to_dict(),
            "bars": self.bars.to_dict(),
            "genre": self.genre.to_dict(),
            "mood": self.mood.to_dict(),
            "hats_first_half": self.hats_first_half.to_dict(),
            "hats_second_half": self.hats_second_half.to_dict(),
            "bass_register": self.bass_register.to_dict(),
            "note_density": self.note_density.to_dict(),
            "drop_start_bar": self.drop_start_bar.to_dict(),
            "meter_numerator": self.meter_numerator,
            "meter_denominator": self.meter_denominator,
            "beats_per_bar": self.beats_per_bar,
            "duration_minutes": self.duration_minutes,
            "sections": [section.to_dict() for section in self.sections],
            "interpretations": list(self.interpretations),
            "unhandled": list(self.unhandled),
            "ambiguous": list(self.ambiguous),
            "contradictions": list(self.contradictions),
            "model": self.model,
            "provider": self.provider,
            **{name: getattr(self, name).to_dict() for name in TONE_FIELDS},
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        """Create a brief from JSON-compatible data."""
        return cls(
            original_text=str(data.get("original_text") or ""),
            tempo=SourcedValue.from_dict(data.get("tempo") or {}),
            key=SourcedValue.from_dict(data.get("key") or {}),
            bars=SourcedValue.from_dict(data.get("bars") or {}),
            genre=SourcedValue.from_dict(data.get("genre") or {}),
            mood=SourcedValue.from_dict(data.get("mood") or {}),
            hats_first_half=SourcedValue.from_dict(data.get("hats_first_half") or {}),
            hats_second_half=SourcedValue.from_dict(data.get("hats_second_half") or {}),
            bass_register=SourcedValue.from_dict(data.get("bass_register") or {}),
            note_density=SourcedValue.from_dict(data.get("note_density") or {}),
            drop_start_bar=SourcedValue.from_dict(data.get("drop_start_bar") or {}),
            meter_numerator=int(data.get("meter_numerator") or 4),
            meter_denominator=int(data.get("meter_denominator") or 4),
            sections=tuple(
                SectionIntent.from_dict(item)
                for item in data.get("sections") or []
                if isinstance(item, dict)
            ),
            interpretations=tuple(str(item) for item in data.get("interpretations") or []),
            unhandled=tuple(str(item) for item in data.get("unhandled") or []),
            ambiguous=tuple(str(item) for item in data.get("ambiguous") or []),
            contradictions=tuple(str(item) for item in data.get("contradictions") or []),
            model=str(data.get("model") or "gemma4:latest"),
            provider=str(data.get("provider") or "local_ollama"),
            # Candidates saved before tone controls existed carry none.
            **{
                name: SourcedValue.from_dict(data[name])
                if isinstance(data.get(name), dict)
                else _no_tone()
                for name in TONE_FIELDS
            },
        )


TONE_FIELDS = ("tone_brightness", "tone_length", "tone_delay")


def _no_tone() -> SourcedValue:
    return SourcedValue(0, SOURCE_DEFAULT)


def _require_int(sourced: SourcedValue, minimum: int, maximum: int, name: str) -> None:
    value = sourced.value
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"invalid {name}")


def _assert_sections_cover(sections: tuple[SectionIntent, ...], bars: int) -> None:
    if not sections:
        raise ValueError("sections must not be empty")
    cursor = 1
    for section in sections:
        if section.start_bar != cursor:
            raise ValueError("sections must be contiguous from bar 1")
        cursor = section.end_bar + 1
    if cursor - 1 != bars:
        raise ValueError("sections must cover the full bar count")
