from typing import Any

from kihachi_mcp.services import AceStepAdapter, AudioService

_audio = AudioService()


def generate_audio(
    project_plan: dict[str, Any],
    target_track: str,
    output_path: str,
    prompt: str = "",
    negative_prompt: str = "",
) -> dict[str, Any]:
    """Generate one audio target through the configured ACE-Step boundary."""
    request = _audio.create_request(project_plan, target_track, prompt, negative_prompt)
    return AceStepAdapter().render(request, output_path).to_dict()
