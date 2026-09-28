"""Convert bars, meter, and tempo without the legacy 32-bars-per-minute grid."""


def beats_per_bar(numerator: int = 4, denominator: int = 4) -> float:
    """Return one bar in Live beat units, where one beat is a quarter note."""
    if numerator < 1 or denominator < 1:
        raise ValueError("meter must be positive")
    return numerator * 4.0 / denominator


def duration_minutes(
    bars: int,
    tempo: int,
    numerator: int = 4,
    denominator: int = 4,
) -> float:
    """Return minutes from bar count, time signature, and BPM.

    ``minutes = bars * beats_per_bar / tempo``. This is the only duration
    formula the studio path is allowed to use.
    """
    if type(tempo) is not int or tempo <= 0:
        raise ValueError("tempo must be a positive integer")
    if type(bars) is not int or bars < 1:
        raise ValueError("bars must be a positive integer")
    return bars * beats_per_bar(numerator, denominator) / tempo


def bar_start_beats(
    start_bar: int,
    numerator: int = 4,
    denominator: int = 4,
) -> float:
    """Return the beat position of the first beat of a 1-based bar."""
    if start_bar < 1:
        raise ValueError("start_bar must be at least 1")
    return (start_bar - 1) * beats_per_bar(numerator, denominator)
