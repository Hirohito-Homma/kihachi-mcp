"""Bounded, seed-reproducible musical variety; no provider or Live operations."""

import re
from dataclasses import replace
from random import Random

from kihachi_mcp.knowledge.genre_database import find
from kihachi_mcp.knowledge.genre_profiles import Profile
from kihachi_mcp.models.production_brief import (
    KEY_ENUM,
    SOURCE_RANDOM,
    SOURCE_USER,
    ProductionBrief,
    SectionIntent,
    SourcedValue,
)
from kihachi_mcp.services.brief_parser import read_explicit

# Genres with distinct supported family patterns, with useful tempo bands.
_GENRES = (
    ("tech_house", 122, 130),
    ("dub_techno", 112, 128),
    ("melodic_techno", 120, 140),
    ("boom_bap", 80, 100),
    ("city_pop", 95, 118),
    ("funk", 95, 120),
    ("reggae", 70, 95),
    ("ambient", 60, 85),
    ("disco", 108, 128),
    ("j_pop", 105, 145),
    ("liquid_drum_bass", 168, 176),
    ("mutation_funk", 105, 125),
)
# Articulation, bass role, harmonic rhythm, swing: one coherent song-level choice.
_PROFILES = {
    "pocket": ("laid_back_stabs", "dominant", 2, 0.58),
    "floating": ("sustained_chords", "supporting", 4, 0.5),
    "driving": ("driving_downstrokes", "present", 1, 0.5),
    "offbeat": ("chopped_stabs", "dominant", 1, 0.54),
}
_LABELS = {
    "pocket": "ベース主役・後ろ重心",
    "floating": "持続和音・広い余白",
    "driving": "細かい刻み・速い和声展開",
    "offbeat": "裏拍の掛け合い",
}
_FORMS = (
    (("Intro", 1), ("Build", 1), ("Drop", 3), ("Break", 1), ("Drop", 3), ("Outro", 1)),
    (
        ("Intro", 1),
        ("VerseA", 3),
        ("ChorusA", 2),
        ("VerseB", 2),
        ("Break", 1),
        ("ChorusB", 3),
        ("Outro", 1),
    ),
    (("Intro", 1), ("Drop", 2), ("Break", 2), ("Build", 1), ("Drop", 4), ("Outro", 1)),
)
_FORM_WORDS = re.compile(
    r"intro|outro|verse|chorus|break|drop|build|イントロ|アウトロ|サビ|ブレイク|ブレーク|ドロップ|ビルド|[ABＡＢ]メロ|構成|展開",
    re.IGNORECASE,
)


def profile_for_variety(brief: ProductionBrief, base: Profile) -> Profile:
    """Leave the genre's drum family and explicit swing intact."""
    if not brief.variation_profile:
        return base
    articulation, bass_role, harmonic_rhythm, swing = _PROFILES[brief.variation_profile]
    return replace(
        base,
        articulation=articulation,
        bass_role=bass_role,
        harmonic_rhythm_bars=harmonic_rhythm,
        swing=swing,
    )


def _sections(bars: int, rng: Random) -> tuple[SectionIntent, ...]:
    # Allocate complete 4-bar phrases; never truncate the user's song length.
    choices = [form for form in _FORMS if len(form) <= bars // 4]
    if not choices:
        return ()
    form = rng.choice(choices)
    units = [1] * len(form)
    for _ in range(bars // 4 - len(form)):
        units[
            rng.choices(range(len(form)), weights=[weight for _, weight in form])[0]
        ] += 1
    result, cursor = [], 1
    for (name, _), length in zip(form, units):
        result.append(SectionIntent(name, cursor, length * 4))
        cursor += length * 4
    return tuple(result)


def variety_signature(brief: ProductionBrief) -> dict:
    return {
        "genre": brief.genre.value,
        "tempo_band": int(brief.tempo.value) // 10,
        "key": brief.key.value,
        "profile": brief.variation_profile,
        "density": brief.note_density.value,
        "hats": [brief.hats_first_half.value, brief.hats_second_half.value],
        "register": brief.bass_register.value,
        "form": [[s.name, s.length_bars] for s in brief.sections],
    }


def _draw(brief: ProductionBrief, rng: Random, explicit: dict) -> ProductionBrief:
    values = {}

    def unlocked(field):
        return field not in explicit and getattr(brief, field).source != SOURCE_USER

    def choose(field, value):
        if unlocked(field):
            values[field] = SourcedValue(value, SOURCE_RANDOM)

    available = [entry for entry in _GENRES if find(entry[0])]
    genre, low, high = rng.choice(available)
    choose("genre", genre)
    selected = values.get("genre", brief.genre).value
    if selected != genre:
        bounds = next((entry[1:] for entry in available if entry[0] == selected), None)
        row = find(str(selected))
        low, high = bounds or (
            row.informative_bpm if row and row.informative_bpm else (85, 140)
        )
    low, high = max(60, min(180, int(low))), max(60, min(180, int(high)))
    if not re.search(r"\d+(?:\.\d+)?\s*分", brief.original_text):
        choose("tempo", rng.randint(min(low, high), max(low, high)))
    # Preserve the mode already chosen from mood or a modal instruction.
    minor = str(brief.key.value).endswith("m")
    choose("key", rng.choice([key for key in KEY_ENUM if key.endswith("m") == minor]))
    profile = rng.choice(tuple(_PROFILES))
    density = (
        "sparse"
        if profile == "floating"
        else "dense"
        if profile == "driving"
        else "normal"
    )
    choose("note_density", density)
    choose("hats_first_half", rng.choice(("sparse", "normal", "dense")))
    choose("hats_second_half", rng.choice(("sparse", "normal", "dense")))
    choose("bass_register", rng.choice(("low", "mid")))
    sections = brief.sections
    if not brief.drop_start_bar.value and not _FORM_WORDS.search(brief.original_text):
        sections = _sections(int(brief.bars.value), rng) or sections
    note = (
        f"ランダム選択: {_LABELS[profile]}。"
        f"ジャンル {selected}、{values.get('tempo', brief.tempo).value} BPM、"
        f"キー {values.get('key', brief.key).value}。拍子と明示指定は保持。"
    )
    # Display final choices, rather than stale pre-randomization default explanations.
    return replace(
        brief,
        **values,
        variation_profile=profile,
        sections=sections,
        interpretations=(note,),
    )


def diversify_brief(brief: ProductionBrief, seed: int, history=()) -> ProductionBrief:
    """Choose among 24 bounded designs, favoring distance from eight recent designs.

    This compares structural choices, not perceptual audio similarity. Saved brief
    plus seed reproduces the exact notes without consulting this history again.
    """
    if type(seed) is not int or seed < 0:
        raise ValueError("seed must be a non-negative integer")
    rng = Random(seed)
    recent = [item for item in history[-8:] if isinstance(item, dict)]
    explicit, _ = read_explicit(brief.original_text)
    candidates = [_draw(brief, rng, explicit) for _ in range(24 if recent else 1)]
    if not recent:
        return candidates[0]

    def score(candidate):
        signature = variety_signature(candidate)
        distances = [
            sum(signature[k] != old.get(k) for k in signature) for old in recent
        ]
        return (
            signature["profile"] != recent[-1].get("profile"),
            signature["genre"] != recent[-1].get("genre"),
            min(distances),
            sum(distances),
        )

    return max(candidates, key=score)
