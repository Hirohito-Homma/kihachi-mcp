from typing import Any

from kihachi_mcp.services import AudioService, GoogleLyriaAdapter

_audio = AudioService()


def generate_audio(
    project_plan: dict[str, Any],
    target_track: str,
    output_path: str,
    prompt: str = "",
    negative_prompt: str = "",
) -> dict[str, Any]:
    """Generate one audio target through the configured Google Lyria boundary."""
    request = _audio.create_request(project_plan, target_track, prompt, negative_prompt)
    context = _audio.create_generation_context(project_plan)
    return GoogleLyriaAdapter().render(request, output_path, context).to_dict()
