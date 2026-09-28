import uuid

from kihachi_mcp.services.sample_catalog import SampleCatalog
from kihachi_mcp.services.studio_dialogue import StudioDialogue


def test_conversation_search_and_numbered_followup_preserve_query(tmp_path):
    source = tmp_path / "samples"
    source.mkdir()
    (source / "909 kick.wav").write_bytes(b"audio")
    (source / "808 kick.wav").write_bytes(b"different audio")
    catalog = SampleCatalog(tmp_path / "index")
    catalog.index(str(source))
    dialogue = StudioDialogue(catalog)
    session = uuid.uuid4().hex
    prompt = dialogue.turn(session, "909を探して", "Mutashon Funk")
    assert "どれ" in prompt["reply"]
    found = dialogue.turn(session, "キック", "Mutashon Funk")
    assert found["search_query"] == "909"
    assert found["search_count"] == 1
    assert found["search_results"][0]["name"] == "909 kick.wav"
    assert "proposal" not in found
    detail = dialogue.turn(session, "１番を詳しく", "Mutashon Funk")
    assert detail["sample_detail"]["name"] == "909 kick.wav"
    assert detail["live_changed"] is False
    other = dialogue.turn(uuid.uuid4().hex, "1番を詳しく", "")
    assert "sample_detail" not in other
    missing = dialogue.turn(session, "808のキックを探して", "")
    assert missing["search_results"][0]["name"] == "808 kick.wav"


def test_timbre_search_is_not_fabricated_as_audio_analysis(tmp_path):
    dialogue = StudioDialogue(SampleCatalog(tmp_path))
    result = dialogue.turn(uuid.uuid4().hex, "太いキックを探して", "")
    assert "まだ選べません" in result["reply"]
    assert "search_results" not in result


def test_dialogue_keeps_words_verbatim_until_matching_acceptance():
    dialogue = StudioDialogue()
    session = uuid.uuid4().hex
    original = "125 BPM、Dマイナー"
    result = dialogue.turn(session, "Mutashon Funkで作って", original)
    assert result["action_executed"] is False
    assert result["proposal"]["brief"] == original + "\nMutashon Funkで作って"
    assert dialogue.accept(session, result["proposal"]["id"], "edited")["ok"] is False
    accepted = dialogue.accept(session, result["proposal"]["id"], original)
    assert accepted["brief"] == result["proposal"]["brief"]
    assert accepted["generation_started"] is False
    assert dialogue.accept(session, result["proposal"]["id"], original)["ok"] is False


def test_ambiguous_or_live_turn_never_executes():
    dialogue = StudioDialogue()
    session = uuid.uuid4().hex
    ambiguous = dialogue.turn(session, "それをもう少し太く", "")
    assert "どの音" in ambiguous["reply"]
    assert "proposal" not in ambiguous
    target = dialogue.turn(session, "キックを太く", "")
    assert target["target"] == "キック"
    assert "どの点" in target["reply"]
    live = dialogue.turn(session, "Liveに入れて", "")
    assert live["live_changed"] is False
    assert "プレビュー" in live["reply"]
    assert "proposal" not in live


def test_new_turn_invalidates_old_proposal_and_sessions_are_isolated():
    dialogue = StudioDialogue()
    first, second = uuid.uuid4().hex, uuid.uuid4().hex
    proposal = dialogue.turn(first, "キックを短く", "")["proposal"]
    assert dialogue.accept(second, proposal["id"], "")["ok"] is False
    dialogue.turn(first, "スネアを短く", "")
    assert dialogue.accept(first, proposal["id"], "")["ok"] is False
    assert dialogue.turn("bad", "test", "")["ok"] is False
