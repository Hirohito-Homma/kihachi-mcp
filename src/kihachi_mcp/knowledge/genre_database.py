"""Recognise genres named in a brief, from the 1020-row genre database.

Ported from the KIHACHI music-ai repository (``kihachi_music_ai/genres.py`` and
``data/genres.json`` at commit 60c5876, built from
Music_Genre_Master_Database_v0.2.xlsx). The data file is copied byte for byte;
this module keeps the recognition rules and drops what only that repository's
audio pipeline used.

Recognition is deliberately conservative. Two lessons from the original, both
kept here:

* Longest form first, and a form inside a longer match is dropped, so "Tech
  House" is tech house alone and "Dubstep" never also reads as "Dub".
* Japanese has no word boundaries, so a katakana or kanji form that continues
  into more of the same script is not a match: ``ラップ`` inside
  ``スラップベース`` is a slap bass, not a rap request. A wrong genre is worse
  than no genre.

Pure and stdlib-only.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

_DATA = Path(__file__).resolve().parents[1] / "resources" / "genre_database.json"
_LATIN = re.compile(r"^[a-z0-9&'\- ./]+$")
_NUMERIC = re.compile(r"[0-9 .\-/]+")
#: "DUB ディレイ" asks for a delay in the dub manner, not for the genre Dub.
#: A genre word directly followed by an effect or mix word describes a sound.
_EFFECT_AFTER = re.compile(
    r"[\s・]*(ディレイ|delay|エコー|echo|リバーブ|reverb|サイレン|siren|"
    r"スペース|space|ミックス|mix|処理|風の|っぽい)",
    re.IGNORECASE,
)

#: A tempo range only says something when it is narrow. Most rows inherit their
#: family's span ("70 to 180" is an absence of a tempo, not a tempo).
MAX_INFORMATIVE_BPM_RANGE = 40.0


@dataclass(frozen=True)
class Genre:
    """One database row, as much of it as the Studio uses."""

    slug: str
    name: str
    parent: str | None
    aliases: tuple[str, ...]
    bpm_min: float | None
    bpm_max: float | None
    meter: str
    rhythm_character: str
    mood_tags: tuple[str, ...]

    @property
    def family(self) -> str:
        """A top-level row is its own family."""
        return self.parent or self.name

    @property
    def informative_bpm(self) -> tuple[float, float] | None:
        """The tempo range, or None when it is too wide to mean anything."""
        if not self.bpm_min or not self.bpm_max:
            return None
        if self.bpm_max - self.bpm_min > MAX_INFORMATIVE_BPM_RANGE:
            return None
        return self.bpm_min, self.bpm_max


@dataclass(frozen=True)
class GenreMatch:
    genre: Genre
    matched: str
    start: int
    end: int


@lru_cache(maxsize=1)
def load_database() -> tuple[Genre, ...]:
    payload = json.loads(_DATA.read_text(encoding="utf-8"))
    return tuple(
        Genre(
            slug=entry["slug"],
            name=entry["name"],
            parent=entry["parent"],
            aliases=tuple(entry["aliases"]),
            bpm_min=entry["bpm_min"],
            bpm_max=entry["bpm_max"],
            meter=entry["meter"],
            rhythm_character=entry.get("rhythm_character", ""),
            mood_tags=tuple(entry["mood_tags"]),
        )
        for entry in payload["genres"]
    )


@lru_cache(maxsize=1)
def _by_slug() -> dict[str, Genre]:
    return {genre.slug: genre for genre in load_database()}


def find(slug: str) -> Genre | None:
    """The row for a slug, or None when it is not a known genre."""
    return _by_slug().get(slug)


def is_known(slug: str) -> bool:
    return slug in _by_slug()


@lru_cache(maxsize=1)
def _surface_forms() -> tuple[tuple[str, Genre], ...]:
    """Every name and alias, longest first; a style beats its family header."""
    claimed: dict[str, Genre] = {}
    for genre in load_database():
        for form in (genre.name, *genre.aliases):
            key = form.strip().lower()
            # Deep Dubstep's alias "140" would turn "Hard Techno 140" into a
            # dubstep request. A bare number in a brief is a tempo or a bar.
            if not key or _NUMERIC.fullmatch(key):
                continue
            previous = claimed.get(key)
            if previous is None or (previous.parent is None and genre.parent is not None):
                claimed[key] = genre
    for key, genre in list(claimed.items()):
        if "-" in key:
            for variant in (key.replace("-", " "), key.replace("-", "")):
                if variant.strip():
                    claimed.setdefault(variant, genre)
    return tuple(sorted(claimed.items(), key=lambda item: (-len(item[0]), item[0])))


def _is_katakana(char: str) -> bool:
    return bool(char) and ("゠" <= char <= "ヿ" or char in "ー・")


def _is_kanji(char: str) -> bool:
    return bool(char) and "一" <= char <= "鿿"


def _continues_run(form: str, before: str, after: str) -> bool:
    if _is_katakana(form[0]) and _is_katakana(before):
        return True
    if _is_katakana(form[-1]) and _is_katakana(after):
        return True
    if _is_kanji(form[0]) and _is_kanji(before):
        return True
    return _is_kanji(form[-1]) and _is_kanji(after)


def _spans(text: str, form: str) -> list[tuple[int, int]]:
    if form not in text:
        return []  # a plain substring test first: ~3000 forms per brief
    if _LATIN.match(form):
        pattern = rf"(?<![a-z0-9]){re.escape(form)}(?![a-z0-9])"
        return [(match.start(), match.end()) for match in re.finditer(pattern, text)]
    found = []
    start = text.find(form)
    while start != -1:
        end = start + len(form)
        before = text[start - 1] if start else ""
        after = text[end] if end < len(text) else ""
        if not _continues_run(form, before, after):
            found.append((start, end))
        start = text.find(form, start + 1)
    return found


def match_genres(text: str) -> tuple[GenreMatch, ...]:
    """Genres named in the text, in the order they appear."""
    lowered = text.lower()
    hits: list[tuple[int, int, str, Genre]] = []
    for form, genre in _surface_forms():
        for start, end in _spans(lowered, form):
            if _EFFECT_AFTER.match(lowered, end):
                continue
            hits.append((start, end, form, genre))
    kept: list[tuple[int, int, str, Genre]] = []
    for hit in sorted(hits, key=lambda item: (-(item[1] - item[0]), item[0])):
        start, end, _form, genre = hit
        if any(other[0] <= start and end <= other[1] for other in kept):
            continue
        if any(other[3].slug == genre.slug for other in kept):
            continue
        kept.append(hit)
    kept.sort(key=lambda item: item[0])
    return tuple(
        GenreMatch(genre=genre, matched=form, start=start, end=end)
        for start, end, form, genre in kept
    )


def typical_bpm(slugs: Sequence[str]) -> int | None:
    """The midpoint of the informative ranges, or None when the data is mute."""
    ranges = [
        genre.informative_bpm
        for genre in (find(slug) for slug in slugs)
        if genre is not None and genre.informative_bpm is not None
    ]
    if not ranges:
        return None
    return round(sum((low + high) / 2 for low, high in ranges) / len(ranges))
