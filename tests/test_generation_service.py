import json
from pathlib import Path

import pytest

from kihachi_mcp.knowledge import UnknownGenreError
from kihachi_mcp.models import GenerationRequest, ProjectPlan, SongSpec, TrackSpec
from kihachi_mcp.services import AudioService, GenerationService, GoogleLyriaAdapter


def test_build_context_uses_retrieved_knowledge() -> None:
    context = GenerationService().build_context(
        GenerationRequest(genre="dub techno", length_minutes=5)
    )

    assert context.knowledge.primary() is not None
    assert context.knowledge.primary().id == "dub_techno"
    assert context.parameters.tempo == 110
    assert context.parameters.key == "D#m"
    assert context.parameters.tracks == ("Kick", "Bass", "Dub Chords", "Pad", "FX")
    assert context.parameters.mood == "Deep"
    assert context.parameters.bars == 160


def test_build_context_without_knowledge_keeps_request_values() -> None:
    context = GenerationService().build_context(
        GenerationRequest(genre="custom", tempo=120, key="Am", length_minutes=1)
    )

    assert context.knowledge.entries == ()
    assert context.parameters.tempo == 120
    assert context.parameters.key == "Am"
    assert context.parameters.tracks == ()


def test_generate_passes_selected_knowledge_and_returns_structured_result() -> None:
    result = GenerationService().generate(
        GenerationRequest(genre="tech house", length_minutes=5)
    )

    assert result.songspec == SongSpec(
        genre="tech house",
        tempo=124,
        key="Am",
        length_minutes=5,
        bars=160,
        tracks=["Kick", "Bass", "Hats", "Stab", "FX"],
    )
    assert result.knowledge.primary() is not None
    assert result.knowledge.primary().id == "tech_house"
    assert result.to_dict()["knowledge"] == [
        {
            "id": "tech_house",
            "version": "1",
            "source": "genre_yaml",
            "kind": "genre",
        }
    ]
    assert result.to_dict()["songspec"]["tracks"] == result.songspec.tracks


def test_generate_without_knowledge_raises_like_existing_song_service() -> None:
    with pytest.raises(UnknownGenreError, match="Unknown genre: jungle"):
        GenerationService().generate(GenerationRequest(genre="jungle"))


def test_explicit_request_values_override_knowledge_defaults() -> None:
    result = GenerationService().generate(
        GenerationRequest(genre="dub techno", tempo=118, key="Fm", length_minutes=5)
    )

    assert result.songspec.tempo == 118
    assert result.songspec.key == "Fm"
    assert result.context.parameters.tracks[0] == "Kick"


def test_audio_context_uses_knowledge_when_genre_is_known() -> None:
    plan = ProjectPlan(
        "Demo",
        "dub techno",
        110,
        "D#m",
        5,
        160,
        [TrackSpec("Bass", "Audio", "Blue")],
    )
    context = AudioService().create_generation_context(plan)

    assert context.knowledge.template() is not None
    assert context.parameters.mood == "Deep"


def test_audio_context_without_knowledge_does_not_block_request() -> None:
    plan = ProjectPlan(
        "Demo",
        "custom",
        120,
        "Am",
        1,
        32,
        [TrackSpec("Bass", "Audio", "Blue")],
    )
    service = AudioService()
    request = service.create_request(plan, "Bass")
    context = service.create_generation_context(plan)

    assert request.genre == "custom"
    assert context.knowledge.entries == ()


def test_lyria_uses_structured_knowledge_when_context_is_provided(
    tmp_path: Path,
) -> None:
    calls: list[bytes | None] = []

    def fake_http(method: str, url: str, headers: dict[str, str], body: bytes | None):
        calls.append(body)
        return (
            200,
            b'{"id":"interaction-1","steps":[{"content":[{"type":"audio","data":"d2F2LWJ5dGVz"}]}]}',
            {},
        )

    plan = ProjectPlan(
        "Demo",
        "dub techno",
        110,
        "D#m",
        1,
        32,
        [TrackSpec("Bass", "Audio", "Blue")],
    )
    service = AudioService()
    request = service.create_request(plan, "Bass", "deep bass")
    context = service.create_generation_context(plan)
    result = GoogleLyriaAdapter(api_key="secret", http_call=fake_http).render(
        request, tmp_path / "bass.mp3", context
    )

    payload = json.loads(calls[0] or b"{}")
    assert result.status == "succeeded"
    assert "mood: Deep" in payload["input"]
    assert "tracks: Kick, Bass, Dub Chords, Pad, FX" in payload["input"]
    assert "Genre: dub techno" in payload["input"]
