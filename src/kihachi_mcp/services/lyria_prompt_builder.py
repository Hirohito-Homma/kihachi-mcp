from kihachi_mcp.models import GenerationContext
from kihachi_mcp.models.arrangement import Arrangement
from kihachi_mcp.models.audio_plan import AudioRenderRequest

_BEATS_PER_BAR = 4
_STYLE_HINTS: dict[str, tuple[str, ...]] = {
    "dub techno": (
        "Deep hypnotic dub techno.",
        "Heavy four-on-the-floor kick.",
        "Syncopated dub bass.",
        "Warm chord stabs with long tape-delay tails.",
        "Dark spacious atmosphere.",
        "Minimal melodic movement.",
        "Subtle evolving percussion.",
    ),
    "tech house": (
        "Driving tech house.",
        "Punchy four-on-the-floor kick.",
        "Rolling bassline.",
        "Crisp hats and percussion.",
        "Short rhythmic stabs.",
        "Club-ready groove with restrained melody.",
    ),
    "melodic techno": (
        "Uplifting melodic techno.",
        "Steady driving kick.",
        "Warm evolving pads.",
        "Arpeggiated motifs with gradual development.",
        "Emotional lead lines.",
        "Wide atmospheric space.",
    ),
}


class LyriaPromptBuilder:
    """Turn KIHACHI musical structure into a Lyria 3.5 full-song prompt."""

    def build(
        self,
        request: AudioRenderRequest,
        context: GenerationContext | None = None,
    ) -> str:
        """Build one prompt. Does not include credentials or provider secrets."""
        genre = request.genre.strip() or "electronic"
        mood, tracks = _mood_and_tracks(request, context)
        lines = [
            (
                f"Create a {_duration_phrase(request.length_minutes)} "
                f"{genre.title()} track as a complete stereo reference mix."
            ),
            "",
            f"Genre: {request.genre}" if request.genre.strip() else "",
            f"Tempo: {request.tempo} BPM" if request.tempo > 0 else "",
            f"Key: {_format_key(request.key)}" if request.key.strip() else "",
            f"duration: {request.length_minutes:g} minutes.",
            f"Duration: {_duration_phrase(request.length_minutes)}.",
            "",
            "Style:",
            *self._style_lines(request.genre, mood),
            "",
            "This is a full-song reference mix, not an isolated stem export.",
        ]
        if request.target_track.strip():
            lines.append(
                f"Keep {request.target_track.strip()} clearly present in the mix."
            )
        if tracks:
            lines.extend(
                [
                    "",
                    "Instrumentation:",
                    ", ".join(tracks) + ".",
                    f"tracks: {', '.join(tracks)}",
                ]
            )
        if mood:
            lines.append(f"mood: {mood}")
        structure = self._structure_lines(request)
        if structure:
            lines.extend(["", "Structure:", *structure])
        if request.prompt.strip():
            lines.extend(["", "Additional direction:", request.prompt.strip()])
        if request.negative_prompt.strip():
            lines.extend(["", f"Avoid: {request.negative_prompt.strip()}"])
        return "\n".join(line for line in lines if line is not None).strip() + "\n"

    def _style_lines(self, genre: str, mood: str) -> list[str]:
        hints = list(_STYLE_HINTS.get(genre.strip().lower(), ()))
        if mood.strip():
            mood_line = f"Mood: {mood.strip()}."
            if mood_line not in hints:
                hints.insert(0, mood_line)
        if not hints:
            hints.append(f"{genre.strip().title() or 'Electronic'} instrumental.")
        return hints

    def _structure_lines(self, request: AudioRenderRequest) -> list[str]:
        return [
            (
                f"[{_clock(start)}-{_clock(end)}] {section.name} "
                f"({section.length_bars} bars)."
            )
            for section, start, end in _timed_sections(request)
        ]


def _mood_and_tracks(
    request: AudioRenderRequest, context: GenerationContext | None
) -> tuple[str, list[str]]:
    mood = request.mood.strip()
    tracks = list(request.tracks)
    if context is not None:
        template = context.knowledge.template()
        if not mood:
            mood = context.parameters.mood or (
                template.mood if template is not None else ""
            )
        if template is not None and template.tracks:
            tracks = list(template.tracks)
        elif context.parameters.tracks:
            tracks = list(context.parameters.tracks)
    return mood, tracks


def _timed_sections(
    request: AudioRenderRequest,
) -> list[tuple[Arrangement, float, float]]:
    if not request.arrangement or request.tempo <= 0:
        return []
    seconds_per_bar = (60.0 / request.tempo) * _BEATS_PER_BAR
    timed: list[tuple[Arrangement, float, float]] = []
    for section in request.arrangement:
        start = max(0, section.start_bar - 1) * seconds_per_bar
        end = start + section.length_bars * seconds_per_bar
        timed.append((section, start, end))
    return timed


def _clock(seconds: float) -> str:
    total = max(0, round(seconds))
    return f"{total // 60}:{total % 60:02d}"


def _duration_phrase(length_minutes: float) -> str:
    if length_minutes <= 0:
        return "short"
    unit = "minute" if length_minutes == 1 else "minutes"
    return f"{length_minutes:g} {unit}"


def _format_key(key: str) -> str:
    raw = key.strip()
    if raw.lower().endswith("maj"):
        return f"{raw[:-3].strip()} major"
    if len(raw) > 1 and raw.endswith("m") and not raw.endswith("M"):
        return f"{raw[:-1]} minor"
    if raw.endswith("M"):
        return f"{raw[:-1]} major"
    return raw
