from kihachi_mcp.models import Arrangement, AudioRenderRequest
from kihachi_mcp.services import LyriaPromptBuilder


def test_prompt_includes_musical_context_and_timestamps() -> None:
    request = AudioRenderRequest(
        "Demo",
        "dub techno",
        "Kick",
        110,
        "D#m",
        3,
        96,
        "keep the dub delay long",
        "vocals",
        arrangement=(
            Arrangement("Intro", 1, 8),
            Arrangement("Groove", 9, 16),
        ),
        mood="Deep",
        tracks=("Kick", "Bass", "Dub Chords"),
    )

    prompt = LyriaPromptBuilder().build(request)

    assert "3 minute" in prompt
    assert "Dub Techno" in prompt
    assert "Tempo: 110 BPM" in prompt
    assert "Key: D# minor" in prompt
    assert "Duration: 3 minutes" in prompt
    assert "Mood: Deep." in prompt
    assert "four-on-the-floor" in prompt
    assert "[0:00-0:17] Intro" in prompt
    assert "Groove" in prompt
    assert "complete stereo reference mix" in prompt
    assert "Kick, Bass, Dub Chords." in prompt
    assert "keep the dub delay long" in prompt
    assert "Avoid: vocals" in prompt
    assert "GEMINI_API_KEY" not in prompt
    assert "x-goog-api-key" not in prompt
