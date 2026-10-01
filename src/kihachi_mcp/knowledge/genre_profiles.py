"""What a genre's family sounds like, in the terms the Studio builder plays.

Ported from the KIHACHI music-ai repository (``derive.FAMILY_PROFILES``,
``groove_tables``) at commit 60c5876, with one deliberate change of meaning:

**Kick positions are restated here, not copied.** In music-ai a pattern's kick
list was read by that repository's own composer, which added quarter-note kicks
on its own; its "four_on_floor" row lists ``(0, 2, 1.5, 3.25, ...)``. Copied
as-is into this builder those numbers would stop meaning four on the floor. So
each pattern names its kicks outright, in groove order: the first
``kick_steps[0]`` always play, and up to ``kick_steps[1]`` play at full energy.

Everything else keeps its source meaning: backbeat slots and pitch, the hat
grid and the rule that thins it, the chord articulations' slots and lengths,
the bass roles, and each family's choice among them.

The families are the database's own spelling; a test checks every key still
names a family, so a renamed family fails loudly instead of silently falling
back to the default.
"""

from __future__ import annotations

from dataclasses import dataclass, fields, replace

from kihachi_mcp.knowledge.genre_database import find

KICK = 36
SIDE_STICK = 37
SNARE = 38
CLAP = 39
CLOSED_HAT = 42
OPEN_HAT = 46
RIDE = 51


@dataclass(frozen=True)
class DrumPattern:
    kick_positions: tuple[float, ...]
    kick_steps: tuple[int, int]
    backbeat_positions: tuple[float, ...] = (1.0, 3.0)
    backbeat_pitch: int = CLAP
    hat_pitch: int = CLOSED_HAT
    hat_offset: float = 0.5
    hat_step: float = 0.5
    hat_sparse_step: float = 1.0
    #: Offbeat open hats in energetic sections: the house and techno lift.
    offbeat_open_hat: bool = False

    def __post_init__(self) -> None:
        low, high = self.kick_steps
        if low < 1 or high < low or high > len(self.kick_positions):
            raise ValueError(f"invalid kick steps {self.kick_steps}")


DRUM_PATTERNS: dict[str, DrumPattern] = {
    "four_on_floor": DrumPattern(
        (0.0, 1.0, 2.0, 3.0), (4, 4), offbeat_open_hat=True
    ),
    "syncopated_tech_house": DrumPattern(
        (0.0, 1.0, 2.0, 3.0, 3.75), (4, 5), offbeat_open_hat=True
    ),
    # The alternating kick phrases are chosen by the MIDI builder. This row
    # supplies the funk backbeat and hat grid without implying a four-on-floor.
    "mutation_funk": DrumPattern(
        (0.0, 1.5, 2.75, 3.5), (2, 4), backbeat_pitch=CLAP,
        hat_offset=0.0, hat_step=0.25, hat_sparse_step=0.5,
    ),
    # Reggae: beat 1 is the hole; the kick lands with the snare on 3.
    "one_drop": DrumPattern(
        (2.0, 3.5, 0.75), (1, 2), backbeat_positions=(2.0,), backbeat_pitch=SNARE
    ),
    "breakbeat": DrumPattern(
        (0.0, 2.5, 1.75, 3.25, 0.75), (2, 4), backbeat_pitch=SNARE, hat_offset=0.25
    ),
    # UK garage skips beat 3, which is the shuffle.
    "two_step": DrumPattern((0.0, 2.5, 3.5, 1.75), (2, 3), backbeat_pitch=SNARE),
    "boom_bap": DrumPattern(
        (0.0, 2.5, 1.75, 3.5), (2, 3), backbeat_pitch=SNARE, hat_offset=0.0
    ),
    # A pulse, not a beat: no backbeat at all.
    "sparse_pulse": DrumPattern(
        (0.0, 2.0), (1, 2), backbeat_positions=(), hat_offset=1.0,
        hat_step=1.0, hat_sparse_step=2.0,
    ),
    "broken_grid": DrumPattern(
        (0.0, 1.25, 2.75, 3.5, 0.75), (2, 4), backbeat_positions=(1.75, 3.0),
        backbeat_pitch=SNARE, hat_offset=0.25,
    ),
    "swung_ride": DrumPattern(
        (0.0, 2.0), (1, 2), backbeat_pitch=SNARE, hat_pitch=RIDE, hat_offset=0.0
    ),
    "shuffle": DrumPattern(
        (0.0, 2.0, 3.5), (2, 3), backbeat_pitch=SNARE, hat_pitch=RIDE, hat_offset=0.0
    ),
    "samba": DrumPattern(
        (1.5, 3.5, 0.0, 2.0), (2, 4), backbeat_positions=(), hat_offset=0.0,
        hat_step=0.25, hat_sparse_step=0.5,
    ),
    "clave": DrumPattern(
        (0.0, 2.0, 3.5), (2, 3), backbeat_positions=(0.0, 1.5, 3.0),
        backbeat_pitch=SIDE_STICK, hat_offset=0.0,
    ),
    "backbeat": DrumPattern(
        (0.0, 2.0, 2.5, 3.5), (2, 3), backbeat_pitch=SNARE, hat_offset=0.0
    ),
    "double_kick": DrumPattern(
        (0.0, 1.0, 2.0, 3.0, 0.5, 1.5, 2.5, 3.5), (4, 8), backbeat_pitch=SNARE,
        hat_pitch=RIDE, hat_offset=0.0,
    ),
    "train_beat": DrumPattern(
        (0.0, 2.0, 1.0, 3.0), (2, 4), backbeat_pitch=SNARE, hat_offset=0.0,
        hat_step=0.25, hat_sparse_step=0.5,
    ),
    # KIHACHI Studio additions, not from music-ai.
    # Trap: half time, the clap on beat 3, sixteenth hats over a sliding 808.
    "trap": DrumPattern(
        (0.0, 1.75, 2.5, 3.25), (2, 4), backbeat_positions=(2.0,),
        hat_offset=0.0, hat_step=0.25, hat_sparse_step=0.5,
    ),
    # Dembow: the reggaeton kick on every beat under a 3+3+2 snare.
    "dembow": DrumPattern(
        (0.0, 1.0, 2.0, 3.0), (4, 4), backbeat_positions=(0.75, 1.5, 2.75, 3.5),
        backbeat_pitch=SNARE, hat_offset=0.0,
    ),
    # Drum & bass two-step: kick on 1 and the and of 3, snare on 2 and 4.
    "dnb_two_step": DrumPattern(
        (0.0, 2.5, 2.75), (2, 3), backbeat_pitch=SNARE, hat_offset=0.0,
    ),
    # Dubstep and halftime: one snare, on beat 3.
    "halftime": DrumPattern(
        (0.0, 2.75, 1.5), (1, 3), backbeat_positions=(2.0,), backbeat_pitch=SNARE,
        hat_offset=0.0,
    ),
    # 808 electro: the syncopated kick of Planet Rock under a clap on 2 and 4.
    "electro": DrumPattern(
        (0.0, 2.5, 1.75, 3.25), (2, 4), hat_offset=0.0, hat_step=0.25,
        hat_sparse_step=0.5,
    ),
    "afrobeat": DrumPattern(
        (0.0, 2.0, 2.75, 1.5), (2, 4), backbeat_positions=(1.0, 3.0),
        backbeat_pitch=SIDE_STICK, hat_offset=0.0, hat_step=0.25, hat_sparse_step=0.5,
    ),
    # Maqsum, D T - T D - T -: dum on the kick, tek on the side stick.
    "maqsum": DrumPattern(
        (0.0, 2.0), (2, 2), backbeat_positions=(0.5, 1.5, 3.0),
        backbeat_pitch=SIDE_STICK, hat_offset=0.0,
    ),
}
DEFAULT_PATTERN = "four_on_floor"


@dataclass(frozen=True)
class ChordArticulation:
    #: Slots in groove order; the first ``steps[0]`` always play.
    positions: tuple[float, ...]
    steps: tuple[int, int]
    duration: float
    velocity_scale: float = 1.0
    #: Dub: each hit repeats at a dotted eighth, quieter each time.
    echo: bool = False


CHORD_ARTICULATIONS: dict[str, ChordArticulation] = {
    "short_offbeat_stabs": ChordArticulation((1.5, 0.75, 2.75, 3.5, 2.25), (1, 4), 0.2),
    "offbeat_skank": ChordArticulation((1.5, 3.5, 0.5, 2.5), (2, 4), 0.18, 0.9),
    "muted_upstrokes": ChordArticulation((1.5, 3.5, 0.5, 2.5, 1.75, 3.75), (3, 6), 0.14, 0.85),
    "hypnotic_stabs": ChordArticulation((1.5, 3.5, 2.75, 0.75), (1, 3), 0.16, 0.9),
    "stab_hits": ChordArticulation((0.0, 2.0, 3.0, 1.0), (1, 3), 0.22, 1.1),
    "sparse_stabs": ChordArticulation((0.0, 2.5, 1.5), (1, 2), 0.3),
    "chopped_stabs": ChordArticulation((0.75, 2.25, 1.5, 3.25, 0.25), (2, 5), 0.12),
    "clipped_stabs": ChordArticulation((0.5, 2.5, 1.75, 3.5), (2, 4), 0.12),
    "fragmented_stabs": ChordArticulation((0.25, 1.75, 2.5, 3.25, 1.25), (2, 5), 0.14),
    "laid_back_stabs": ChordArticulation((0.25, 2.25, 1.25, 3.25), (1, 3), 0.5),
    "sustained_chords": ChordArticulation((0.0, 2.0), (1, 2), 2.0, 0.9),
    "sustained_pads": ChordArticulation((0.0,), (1, 1), 4.0, 0.8),
    "sustained_power_chords": ChordArticulation((0.0, 2.0), (1, 2), 1.9, 1.1),
    "palm_muted_chugs": ChordArticulation(
        (0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5), (4, 8), 0.22, 1.1
    ),
    "driving_downstrokes": ChordArticulation((0.0, 1.0, 2.0, 3.0, 0.5, 1.5), (4, 6), 0.4, 1.1),
    "strummed_chords": ChordArticulation((0.0, 2.0, 1.0, 3.0), (2, 4), 0.9, 0.95),
    "comped_chords": ChordArticulation((1.5, 2.75, 0.5, 3.25), (1, 3), 0.6, 0.85),
    "syncopated_comping": ChordArticulation((0.5, 1.75, 3.0, 2.25), (2, 4), 0.35, 0.85),
    "montuno": ChordArticulation((0.0, 0.75, 1.5, 2.5, 3.25, 2.0), (4, 6), 0.3, 0.9),
    # KIHACHI Studio addition, not from music-ai: the dub-techno chord. A short
    # offbeat stab whose repeats do what the delay send does on a record.
    "dub_chords": ChordArticulation((0.5, 2.5, 1.75), (1, 2), 0.3, 0.9, echo=True),
}
DEFAULT_ARTICULATION = "short_offbeat_stabs"


@dataclass(frozen=True)
class BassRole:
    #: Share of the bass pattern's notes that play.
    density_scale: float
    velocity: int


BASS_ROLES: dict[str, BassRole] = {
    "dominant": BassRole(1.0, 102),
    "present": BassRole(0.82, 94),
    "supporting": BassRole(0.62, 86),
}
DEFAULT_BASS_ROLE = "dominant"


@dataclass(frozen=True)
class Profile:
    """One family's choices. ``None`` means no opinion, not zero."""

    drum_pattern: str | None = None
    articulation: str | None = None
    bass_role: str | None = None
    hat_density: float | None = None
    harmonic_rhythm_bars: int | None = None
    #: Where an offbeat eighth lands inside its beat: 0.5 straight, 2/3 triplet.
    swing: float | None = None
    #: A key of music_theory.HARMONY_STYLES: the progressions the genre plays.
    harmony: str | None = None
    #: A key of music_theory.MODES, when the genre leans on one.
    mode: str | None = None

    def overlaid_with(self, other: Profile) -> Profile:
        stated = {
            item.name: getattr(other, item.name)
            for item in fields(other)
            if getattr(other, item.name) is not None
        }
        return replace(self, **stated)


FAMILY_PROFILES: dict[str, Profile] = {
    "R&B / Soul / Funk": Profile(
        None, "short_offbeat_stabs", "dominant", None, 1, harmony="funk", mode="dorian"
    ),
    "House": Profile("four_on_floor", "short_offbeat_stabs", "present", 0.85, 1, harmony="deep_house"),
    "Disco": Profile("four_on_floor", "muted_upstrokes", "dominant", 0.8, 1, harmony="funk"),
    "Techno": Profile("four_on_floor", "hypnotic_stabs", "supporting", 0.92, 2, harmony="techno"),
    "Trance": Profile("four_on_floor", "sustained_chords", "supporting", 0.9, 2, harmony="edm"),
    "EDM / Future Bass": Profile(
        "four_on_floor", "sustained_chords", "supporting", 0.85, 2, harmony="edm"
    ),
    "Hardcore Electronic": Profile("four_on_floor", "stab_hits", "supporting", 0.95, 2, harmony="edm"),
    "Reggae / Dub / Ska": Profile("one_drop", "offbeat_skank", "dominant", 0.45, 2, harmony="reggae"),
    "Jungle / Drum & Bass": Profile("breakbeat", "sparse_stabs", "dominant", 0.9, 4, harmony="dnb"),
    "Breakbeat / Breaks": Profile("breakbeat", "chopped_stabs", "dominant", 0.85, 2, harmony="dnb"),
    "UK Garage / Bass": Profile("two_step", "clipped_stabs", "dominant", 0.8, 1, harmony="deep_house"),
    "Hip-Hop / Rap": Profile("boom_bap", "laid_back_stabs", "dominant", 0.6, 2, harmony="jazz"),
    "Ambient / Downtempo": Profile(
        "sparse_pulse", "sustained_pads", "supporting", 0.15, 4, harmony="ambient"
    ),
    "IDM / Experimental Electronic": Profile("broken_grid", "fragmented_stabs", "present", 0.7, 2),
    "Jazz": Profile("swung_ride", "comped_chords", "present", 0.85, 1, harmony="jazz", mode="dorian"),
    "Blues": Profile("shuffle", "comped_chords", "present", 0.6, 1, harmony="blues"),
    "Brazilian": Profile("samba", "syncopated_comping", "present", 0.9, 1, harmony="jazz"),
    "Latin": Profile("clave", "montuno", "present", 0.8, 1, harmony="latin"),
    "Rock": Profile("backbeat", "sustained_power_chords", "supporting", 0.55, 2, harmony="rock"),
    "Punk / Hardcore": Profile(
        "backbeat", "driving_downstrokes", "supporting", 0.7, 2, harmony="rock"
    ),
    "Metal": Profile("double_kick", "palm_muted_chugs", "supporting", 0.8, 2, harmony="rock"),
    "Country / Americana": Profile("train_beat", "strummed_chords", "present", 0.65, 2, harmony="pop"),
    "Folk": Profile("sparse_pulse", "strummed_chords", "present", 0.3, 2, harmony="pop"),
    # KIHACHI Studio additions: families music-ai had no opinion about, which
    # fell back to four on the floor with house stabs.
    "Pop": Profile("backbeat", "sustained_chords", "present", 0.7, 1, harmony="pop"),
    "East Asian": Profile("backbeat", "sustained_chords", "present", 0.7, 1, harmony="j_pop"),
    "Electro / Synth / Industrial": Profile(
        "electro", "stab_hits", "supporting", 0.85, 2, harmony="techno"
    ),
    "African": Profile("afrobeat", "syncopated_comping", "present", 0.9, 1, harmony="afro"),
    "Global Bass / Club": Profile("dembow", "chopped_stabs", "dominant", 0.8, 2, harmony="trap"),
    "Vaporwave / Internet": Profile(
        "backbeat", "sustained_chords", "supporting", 0.5, 2, harmony="city_pop"
    ),
    "Middle East / North Africa": Profile(
        "maqsum", "sparse_stabs", "present", 0.6, 2,
        harmony="phrygian_dominant", mode="phrygian_dominant",
    ),
    "Soundtrack / Stage / Vocal": Profile(
        "sparse_pulse", "sustained_pads", "supporting", 0.3, 4, harmony="ambient"
    ),
    "Classical / Art Music": Profile("sparse_pulse", "sustained_chords", "supporting", 0.2, 2),
    "Experimental / Noise / Drone": Profile(
        "broken_grid", "fragmented_stabs", "present", 0.5, 4
    ),
}

#: Opinions about one genre that its family does not share. Keep this short:
#: a row here claims the other genres of the family play differently.
GENRE_PROFILES: dict[str, Profile] = {
    "tech_house": Profile(drum_pattern="syncopated_tech_house"),
    # Its own progressions and plain minor, not the family's funk vamp.
    "mutation_funk": Profile(
        drum_pattern="mutation_funk", articulation="syncopated_comping",
        bass_role="dominant", hat_density=0.65, harmonic_rhythm_bars=2,
        swing=0.56, harmony="", mode="",
    ),
    # KIHACHI Studio addition: dub techno keeps Techno's kick but not its
    # hypnotic stab -- the chord is the echoing dub stab -- and it breathes:
    # fewer hats, and the harmony moves slowly.
    "dub_techno": Profile(articulation="dub_chords", hat_density=0.7, harmonic_rhythm_bars=4),
    "trap": Profile(drum_pattern="trap", bass_role="dominant", harmony="trap", hat_density=0.9),
    "dubstep": Profile(drum_pattern="halftime", harmony="trap"),
    "drum_bass": Profile(drum_pattern="dnb_two_step"),
    "liquid_drum_bass": Profile(drum_pattern="dnb_two_step", articulation="sustained_chords"),
    "liquid_funk": Profile(drum_pattern="dnb_two_step", articulation="sustained_chords"),
    "neurofunk": Profile(drum_pattern="dnb_two_step"),
    "techstep": Profile(drum_pattern="dnb_two_step"),
    "reggaeton": Profile(drum_pattern="dembow", harmony="pop"),
    "dance_pop": Profile(drum_pattern="four_on_floor"),
    "electropop": Profile(drum_pattern="four_on_floor", harmony="edm"),
    "synthpop": Profile(drum_pattern="four_on_floor", harmony="edm"),
    "europop": Profile(drum_pattern="four_on_floor"),
    "j_pop": Profile(harmony="j_pop"),
    "city_pop": Profile(harmony="city_pop", swing=0.54),
    "lo_fi_hip_hop": Profile(swing=0.58, articulation="sustained_chords", hat_density=0.5),
    "boom_bap": Profile(swing=0.56),
    "afro_house": Profile(harmony="afro"),
}

#: Swing the database states in its own words. Jazz's 0.58 is a lean, chosen by
#: ear in music-ai over the triplet its name suggests; 12/8 meters are a
#: shuffle because the database states the subdivision outright.
_SWING_PHRASES = {"swing or syncopated improvisation": 0.58}
_TRIPLET = 2 / 3

#: The profile used when a genre is unknown or its family states nothing.
BASE_PROFILE = Profile(DEFAULT_PATTERN, DEFAULT_ARTICULATION, DEFAULT_BASS_ROLE, 0.85, 2, 0.5)


def profile_for(slug: str) -> Profile:
    """Family choices, then the database's own swing, then per-genre rows."""
    profile = BASE_PROFILE
    genre = find(slug)
    if genre is not None and genre.family in FAMILY_PROFILES:
        profile = profile.overlaid_with(FAMILY_PROFILES[genre.family])
    if genre is not None:
        if genre.rhythm_character.strip() in _SWING_PHRASES:
            profile = replace(profile, swing=_SWING_PHRASES[genre.rhythm_character.strip()])
        if "12/8" in (genre.meter or ""):
            profile = replace(profile, swing=_TRIPLET)
    if slug in GENRE_PROFILES:
        profile = profile.overlaid_with(GENRE_PROFILES[slug])
    return profile


def drum_pattern(name: str | None) -> DrumPattern:
    return DRUM_PATTERNS.get(name or "", DRUM_PATTERNS[DEFAULT_PATTERN])


def chord_articulation(name: str | None) -> ChordArticulation:
    return CHORD_ARTICULATIONS.get(name or "", CHORD_ARTICULATIONS[DEFAULT_ARTICULATION])


def bass_role(name: str | None) -> BassRole:
    return BASS_ROLES.get(name or "", BASS_ROLES[DEFAULT_BASS_ROLE])


def hat_positions(pattern: DrumPattern, density: float, bar_beats: float = 4.0) -> tuple[float, ...]:
    """Hat slots for a density in 0..1, thinning from the full grid.

    Ported as-is: the full grid at ``hat_step`` is thinned toward the
    ``hat_sparse_step`` skeleton, dropping offbeats before beats and later
    slots before earlier ones, so every step of density changes the file.
    """
    density = max(0.0, min(1.0, density))
    full = _grid(pattern.hat_offset, pattern.hat_step, bar_beats)
    if not full:
        return ()
    skeleton = _grid(pattern.hat_offset, pattern.hat_sparse_step, bar_beats)
    keep = len(skeleton) + round(density * (len(full) - len(skeleton)))
    if keep >= len(full):
        return tuple(full)
    protected = set(skeleton)
    droppable = sorted(
        (slot for slot in full if slot not in protected),
        key=lambda slot: (slot % 1.0 == 0.0, -slot),
    )
    dropped = set(droppable[: len(full) - keep])
    return tuple(slot for slot in full if slot not in dropped)


def swung(position: float, swing: float) -> float:
    """Warp one beat so its offbeat eighth lands at ``swing``."""
    if abs(swing - 0.5) < 1e-9:
        return position
    beat, inner = divmod(position, 1.0)
    if inner <= 0.5:
        warped = inner / 0.5 * swing
    else:
        warped = swing + (inner - 0.5) / 0.5 * (1.0 - swing)
    return round(beat + warped, 6)


def _grid(offset: float, step: float, bar_beats: float) -> list[float]:
    slots: list[float] = []
    position = offset
    while position < bar_beats - 1e-9:
        slots.append(round(position, 6))
        position += step
    return slots
