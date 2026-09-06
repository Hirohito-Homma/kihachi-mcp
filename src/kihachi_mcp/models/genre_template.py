from dataclasses import dataclass


@dataclass(frozen=True)
class GenreTemplate:
    """External genre knowledge used to build a SongSpec."""

    name: str
    default_bpm: int
    default_key: str
    tracks: list[str]
    arrangement: dict[str, int]
    mood: str
