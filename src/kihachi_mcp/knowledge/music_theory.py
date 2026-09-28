"""Scales, modes and chords in the terms the MIDI builder plays.

Chords are written as Roman numerals against the major scale, the way a
chart names them: ``bVI`` is the flat sixth whatever the key, ``V7/vi`` the
dominant of the sixth. A minor-key progression therefore reads ``i bVI bIII
bVII``. Each chord keeps a scale degree as well, so a melody can step along
the mode from it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

MODES: dict[str, tuple[int, ...]] = {
    "ionian": (0, 2, 4, 5, 7, 9, 11),
    "dorian": (0, 2, 3, 5, 7, 9, 10),
    "phrygian": (0, 1, 3, 5, 7, 8, 10),
    "lydian": (0, 2, 4, 6, 7, 9, 11),
    "mixolydian": (0, 2, 4, 5, 7, 9, 10),
    "aeolian": (0, 2, 3, 5, 7, 8, 10),
    "locrian": (0, 1, 3, 5, 6, 8, 10),
    "harmonic_minor": (0, 2, 3, 5, 7, 8, 11),
    "melodic_minor": (0, 2, 3, 5, 7, 9, 11),
    # Hijaz, the Arabic and flamenco sound: a minor second, then a major third.
    "phrygian_dominant": (0, 1, 4, 5, 7, 8, 10),
}
MINOR_MODES = frozenset(
    {"dorian", "phrygian", "aeolian", "locrian", "harmonic_minor", "melodic_minor"}
)
#: Five-note scales a melody may be held to; chords still come from the mode.
PENTATONIC = {
    "minor": (0, 3, 5, 7, 10),
    "major": (0, 2, 4, 7, 9),
    # 都節 (miyako-bushi), the Japanese in-scale.
    "in": (0, 1, 5, 7, 8),
    # ヨナ抜き: the major scale without its fourth and seventh.
    "yonanuki": (0, 2, 4, 7, 9),
}

_MODE_WORDS = (
    (r"ハーモニックマイナー|和声的短音階|harmonic minor", "harmonic_minor"),
    (r"メロディックマイナー|旋律的短音階|melodic minor", "melodic_minor"),
    (r"フリジアンドミナント|ヒジャーズ|phrygian dominant|hijaz", "phrygian_dominant"),
    (r"ドリアン|dorian", "dorian"),
    (r"フリジアン|phrygian", "phrygian"),
    # Before lydian, which its name contains.
    (r"ミクソリディアン|mixolydian", "mixolydian"),
    (r"リディアン|lydian", "lydian"),
    (r"ロクリアン|locrian", "locrian"),
    (r"エオリアン|aeolian", "aeolian"),
    (r"アイオニアン|イオニアン|ionian", "ionian"),
)


def mode_from_text(text: str) -> str | None:
    """The mode a brief names outright, or None."""
    lowered = text.lower()
    for pattern, mode in _MODE_WORDS:
        if re.search(pattern, lowered):
            return mode
    return None


@dataclass(frozen=True)
class Chord:
    #: Semitones from the tonic to the chord root. May exceed 11 for a
    #: diatonic chord stacked from a high degree; fold with ``% 12``.
    root: int
    #: Semitones above the root, ascending, starting at 0.
    tones: tuple[int, ...]
    #: The scale degree nearest the root, for stepping a melody along the mode.
    degree: int
    symbol: str = ""

    def pitch_classes(self) -> frozenset[int]:
        return frozenset((self.root + tone) % 12 for tone in self.tones)

    def triad(self) -> tuple[int, ...]:
        return self.tones[:3]

    def four_voices(self) -> tuple[int, ...]:
        """Root, third, fifth, seventh; with a ninth, the fifth makes room for it."""
        if len(self.tones) <= 4:
            return self.tones
        return (self.tones[0], self.tones[1], *self.tones[3:5])


def degree_offset(scale: tuple[int, ...], degree: int) -> int:
    octave, step = divmod(degree, len(scale))
    return scale[step] + 12 * octave


def diatonic(scale: tuple[int, ...], degree: int) -> Chord:
    """Thirds stacked on a scale degree, to the seventh."""
    root = degree_offset(scale, degree)
    tones = tuple(degree_offset(scale, degree + step) - root for step in (0, 2, 4, 6))
    return Chord(root, tones, degree)


_NUMERALS = {"I": 0, "II": 2, "III": 4, "IV": 5, "V": 7, "VI": 9, "VII": 11}
_SYMBOL = re.compile(
    r"^(?P<accidental>[b#]?)(?P<numeral>VII|VI|IV|V|III|II|I|vii|vi|iv|v|iii|ii|i)"
    r"(?P<quality>°|ø|\+)?(?P<extension>maj9|maj7|add9|7sus4|sus4|sus2|m7|7|9|6)?$"
)


def parse_numeral(symbol: str, scale: tuple[int, ...]) -> Chord:
    """One Roman numeral, with an optional ``/target`` for a secondary chord."""
    head, _, target = symbol.partition("/")
    offset = parse_numeral(target, scale).root % 12 if target else 0
    match = _SYMBOL.match(head)
    if match is None:
        raise ValueError(f"not a chord symbol: {symbol!r}")
    numeral = match["numeral"]
    root = _NUMERALS[numeral.upper()] + {"b": -1, "#": 1, "": 0}[match["accidental"]]
    root = (root + offset) % 12
    quality = match["quality"] or ""
    extension = match["extension"] or ""
    if quality == "°":
        tones = [0, 3, 6]
    elif quality == "ø":
        tones = [0, 3, 6, 10]
    elif quality == "+":
        tones = [0, 4, 8]
    elif numeral.isupper():
        tones = [0, 4, 7]
    else:
        tones = [0, 3, 7]
    if extension == "sus4":
        tones[1] = 5
    elif extension == "7sus4":
        tones[1] = 5
        tones.append(10)
    elif extension == "sus2":
        tones[1] = 2
    elif extension in {"7", "m7"}:
        tones.append(9 if quality == "°" else 10)
    elif extension == "maj7":
        tones.append(11)
    elif extension == "9":
        tones.extend((10, 14))
    elif extension == "maj9":
        tones.extend((11, 14))
    elif extension == "add9":
        tones.append(14)
    elif extension == "6":
        tones.append(9)
    return Chord(root, tuple(sorted(set(tones))), nearest_degree(scale, root), symbol)


def nearest_degree(scale: tuple[int, ...], root: int) -> int:
    """The scale degree at or just below a root, so ``bVI`` in major steps from 5."""
    pitch = root % 12
    below = [index for index, offset in enumerate(scale) if offset <= pitch]
    return below[-1] if below else 0


def progression(symbols: tuple[str, ...], scale: tuple[int, ...]) -> tuple[Chord, ...]:
    return tuple(parse_numeral(symbol, scale) for symbol in symbols)


def fit_to_chord(pitch: int, tonic: int, chord: Chord, scale: tuple[int, ...]) -> int:
    """Move a scale tone onto a borrowed chord tone a semitone away.

    Only the chord's tones outside the scale pull: over E7 in C major the G
    becomes G-sharp, while a diatonic chord leaves every scale tone alone.
    """
    keys = {(tonic + offset) % 12 for offset in scale}
    tones = {(tonic + chord.root + tone) % 12 for tone in chord.tones}
    if pitch % 12 in tones:
        return pitch
    for step in (1, -1):
        if (pitch + step) % 12 in tones - keys:
            return pitch + step
    return pitch


#: Progressions by style, minor and major. Four chords, or two for a vamp.
#: Each row is a common record progression, not an invention.
HARMONY_STYLES: dict[str, dict[str, tuple[tuple[str, ...], ...]]] = {
    "deep_house": {
        "minor": (("i7", "iv7", "bVIImaj7", "bIIImaj7"), ("i9", "iv9"), ("i7", "bVImaj7", "iv7", "v7")),
        "major": (("ii7", "V7", "Imaj7", "vi7"), ("Imaj7", "iii7", "IVmaj7", "V7sus4")),
    },
    "pop": {
        "minor": (("i", "bVI", "bIII", "bVII"), ("i", "iv", "bVII", "bIII"), ("i", "bVII", "bVI", "bVII")),
        "major": (("I", "V", "vi", "IV"), ("vi", "IV", "I", "V"), ("I", "vi", "IV", "V"), ("IV", "V", "iii", "vi")),
    },
    # 王道進行, 丸サ進行 (the III7 and I7 are secondary dominants), 小室進行.
    "j_pop": {
        "minor": (("bVI", "bVII", "i", "i"), ("i", "bVI", "bVII", "V7")),
        "major": (("IVmaj7", "V7", "iii7", "vi7"), ("IVmaj7", "III7", "vi7", "I7"), ("vi", "IV", "V", "I")),
    },
    "city_pop": {
        "minor": (("i9", "iv9", "bVIImaj7", "bIIImaj7"),),
        "major": (("IVmaj7", "III7", "vi9", "V7sus4"), ("ii9", "V7", "Imaj9", "VI7")),
    },
    "funk": {
        "minor": (("i7", "IV7"), ("i7", "i7", "IV7", "bVII7")),
        "major": (("I7", "IV7"), ("I7", "bVII7", "IV7", "I7")),
    },
    "jazz": {
        "minor": (("iiø", "V7", "i7", "i7"), ("i7", "iv7", "bVII7", "bIIImaj7")),
        "major": (("ii7", "V7", "Imaj7", "vi7"), ("Imaj7", "VI7", "ii7", "V7"), ("IVmaj7", "iii7", "ii7", "Imaj7")),
    },
    "dnb": {
        "minor": (("i", "bVI", "bIII", "bVII"), ("i7", "bVImaj7", "bVII", "v7")),
        "major": (("vi", "IV", "I", "V"),),
    },
    "trap": {
        "minor": (("i", "bVI", "i", "v"), ("i", "bII", "i", "bVI"), ("i", "i", "bVI", "bVII")),
        "major": (("vi", "IV", "vi", "V"),),
    },
    "techno": {
        "minor": (("i", "i", "bVI", "bVII"), ("i", "bII", "i", "i"), ("i", "iv", "i", "bVI")),
        "major": (("I", "I", "IV", "I"),),
    },
    "edm": {
        "minor": (("i", "bVI", "bIII", "bVII"), ("bVI", "bVII", "i", "i")),
        "major": (("vi", "IV", "I", "V"), ("IV", "V", "vi", "I")),
    },
    # The Andalusian cadence and the tonic-dominant vamp.
    "latin": {
        "minor": (("i", "bVII", "bVI", "V7"), ("i", "iv", "V7", "i")),
        "major": (("I", "IV", "V7", "I"),),
    },
    "reggae": {
        "minor": (("i", "bVII"), ("i", "iv")),
        "major": (("I", "IV"), ("I", "V", "IV", "V")),
    },
    "rock": {
        "minor": (("i", "bVII", "bVI", "bVII"), ("i", "bIII", "bVII", "iv")),
        "major": (("I", "bVII", "IV", "I"), ("I", "V", "vi", "IV")),
    },
    "ambient": {
        "minor": (("i9", "bVImaj7"), ("i7", "bIIImaj7", "bVImaj7", "iv7")),
        "major": (("Imaj7", "IVmaj7"), ("Imaj9", "vi7", "IVmaj7", "IVmaj7")),
    },
    "phrygian_dominant": {
        "minor": (("I", "bII", "I", "bvii"), ("I", "bII", "bIII", "bII")),
        "major": (("I", "bII", "I", "bvii"),),
    },
    "afro": {
        "minor": (("i7", "iv7"), ("i", "bVII", "bVI", "bVII")),
        "major": (("I", "IV", "vi", "V"), ("Imaj7", "IVmaj7")),
    },
}


def progressions_for(style: str, minor: bool) -> tuple[tuple[str, ...], ...]:
    rows = HARMONY_STYLES.get(style)
    if not rows:
        return ()
    return rows["minor" if minor else "major"]
