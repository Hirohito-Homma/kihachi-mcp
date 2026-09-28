"""The `kihachi` CLI and the MCP tools share the Studio's services."""

import json
from pathlib import Path

import pytest

from kihachi_mcp import cli
from kihachi_mcp.tools import studio as tools


@pytest.fixture(autouse=True)
def _state(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("KIHACHI_LIVE_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("KIHACHI_PROJECT_DIR", str(tmp_path / "state" / "candidates"))
    monkeypatch.setattr(tools, "_runtime", None)
    monkeypatch.setattr("kihachi_mcp.services.studio_client.studio_running", lambda: False)
    monkeypatch.setattr("kihachi_mcp.cli.studio_running", lambda: False)


def test_cli_create_projects_inspect_review_revise_approve(capsys) -> None:
    assert cli.main(["create", "KIHACHI STUDIO SMOKE TEST。120 BPM、Cマイナー、32小節", "--offline", "--seed", "2"]) == 0
    out = capsys.readouterr().out
    assert "120 BPM" in out and "Arrangement" in out
    candidate_id = out.split("候補ID: ")[1].split()[0]
    short = candidate_id[:8]
    assert cli.main(["projects"]) == 0
    assert short in capsys.readouterr().out
    assert cli.main(["inspect", short]) == 0
    assert "Tracks" in capsys.readouterr().out
    assert cli.main(["review", short]) == 0
    assert "structure" in capsys.readouterr().out
    assert cli.main(["revise", short, "--scope", "bass", "--bars", "9-16"]) == 0
    assert "--accept" in capsys.readouterr().out
    assert cli.main(["revise", short, "--scope", "bass", "--bars", "9-16", "--accept"]) == 0
    assert "新しい候補" in capsys.readouterr().out
    assert cli.main(["approve", short]) == 0
    assert cli.main(["ableton", "plan", short]) == 0
    assert "ABLETON PLAN" in capsys.readouterr().out


def test_cli_live_steps_need_the_running_studio(capsys) -> None:
    cli.main(["create", "120 BPM、32小節", "--offline"])
    candidate_id = capsys.readouterr().out.split("候補ID: ")[1].split()[0]
    assert cli.main(["ableton", "execute", candidate_id[:8], "--yes"]) == 1
    assert "kihachi start" in capsys.readouterr().err


def test_cli_doctor_json(capsys) -> None:
    cli.main(["doctor", "--json"])
    report = json.loads(capsys.readouterr().out)
    assert {check["name"] for check in report["checks"]} >= {
        "Python", "Dependencies", "Config", "Storage", "Ollama", "Model", "MCP",
        "AbletonGPT", "Remote Script", "Ableton Live", "Studio backend", "Studio frontend",
    }


def test_unknown_project_is_a_clear_message() -> None:
    with pytest.raises(SystemExit, match="kihachi projects"):
        cli.main(["inspect", "zzzzzzzz"])


def test_mcp_tools_share_the_saved_projects() -> None:
    project = tools.create_song("110 BPM、D# minor。Mutation Funk。Swing 54%。約5分。", seed=1, use_ai=False)
    candidate_id = project["candidate_id"]
    assert project["songspec"]["swing"] == pytest.approx(0.54)
    assert tools.list_projects()["projects"][0]["candidate_id"] == candidate_id
    assert tools.get_project(candidate_id)["ok"] is True
    assert tools.review_song(candidate_id)["ok"] is True
    proposal = tools.revise_song(candidate_id, scopes=["bass"], start_bar=9, end_bar=16)
    assert proposal["ok"] is True and "decision" not in proposal
    dry = tools.dry_run_ableton_plan(candidate_id)
    assert dry["live_checked"] is False and dry["tempo"] == 110
    refused = tools.execute_ableton_plan(candidate_id)
    assert refused["ok"] is False
    assert tools.verify_ableton_project(candidate_id)["ok"] is False
    assert "installed_models" in tools.ollama_status()
    assert tools.doctor()["checks"]


def test_server_registers_the_studio_tools() -> None:
    import asyncio

    from kihachi_mcp.server import mcp

    names = {tool.name for tool in asyncio.run(mcp.list_tools())}
    assert {
        "create_song", "list_projects", "get_project", "review_song", "revise_song",
        "approve_song", "dry_run_ableton_plan", "execute_ableton_plan",
        "verify_ableton_project", "ollama_status", "doctor", "create_ableton_plan",
    } <= names
