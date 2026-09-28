import pytest

from kihachi_mcp.services.reference_analysis import ReferenceError
from kihachi_mcp.services.reference_guidance import propose_tempo
from kihachi_mcp.services.reference_library import ReferenceLibrary


def library_with_bpms(tmp_path, bpms):
    library = ReferenceLibrary(
        tmp_path / "db",
        lambda _: {"metrics": {}, "warnings": [], "tempo_candidates": [{"bpm": 160}]},
    )
    for i, bpm in enumerate(bpms):
        path = tmp_path / f"{i}.wav"
        path.write_bytes(f"different audio {i}".encode())
        library.import_file(
            path,
            {
                "title": str(i),
                "source": "local",
                "license": "自作",
                "rights_confirmed": True,
                "kind": "track",
                "genres": "dub_techno",
                "confirmed_bpm": bpm,
            },
        )
    return library


def proposal(library, brief="Dマイナー、96小節の暗いテクノ。"):
    return propose_tempo(
        library, {"genre": "dub_techno", "kind": "track", "brief": brief}
    )


def test_guidance_uses_confirmed_median_preserves_original_and_does_not_mutate_db(
    tmp_path,
):
    library = library_with_bpms(tmp_path, [120, 125, 129, None])
    before = library.entries()
    result = proposal(library)
    assert result["suggested_bpm"] == 125
    assert result["confirmed_bpm"]["n"] == 3
    assert result["proposed_brief"] == result["original_brief"] + "\n125 BPM。"
    assert result["generation_applied"] is False
    assert library.entries() == before


@pytest.mark.parametrize(
    "brief",
    [
        "125 BPMで作って",
        "９０ ＢＰＭで作って",
        "テンポは未定",
        "ゆっくり作る",
        "200 bpm",
        "127.5 BPM",
        "BPMなしで相談",
    ],
)
def test_existing_tempo_requests_are_never_overwritten(tmp_path, brief):
    result = proposal(library_with_bpms(tmp_path, [120]), brief)
    assert result["proposed_brief"] == brief
    assert not result["changed"]


@pytest.mark.parametrize("bpms", [[None], [], [220]])
def test_no_invented_or_clamped_tempo(tmp_path, bpms):
    result = proposal(library_with_bpms(tmp_path, bpms))
    assert not result["changed"]
    assert result["suggested_bpm"] is None


def test_proposal_cannot_exceed_input_limit(tmp_path):
    result = proposal(library_with_bpms(tmp_path, [120]), "あ" * 4000)
    assert not result["changed"]
    with pytest.raises(ReferenceError):
        proposal(library_with_bpms(tmp_path / "other", []), "")
