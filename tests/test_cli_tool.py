"""`fbm tool`: the shell interface agents use to call FBM tools."""

import json

import pytest
from typer.testing import CliRunner

from fbm import automation as auto
from fbm.cli import app
from fbm.cli_tool import parse_arguments, render


def test_parse_arguments_pairs_and_stdin():
    args = parse_arguments("free_agents", ["top=5", "position=PG"], stdin_text='{"sort": "week", "top": 1}')
    assert args == {"sort": "week", "top": 5, "position": "PG"}
    assert parse_arguments("player_card", ["query=Luka Dončić"]) == {"query": "Luka Dončić"}
    with pytest.raises(ValueError, match="key=value"):
        parse_arguments("free_agents", ["top"])
    with pytest.raises(ValueError, match="unknown argument"):
        parse_arguments("free_agents", ["nope=1"])
    with pytest.raises(ValueError, match="JSON object"):
        parse_arguments("free_agents", [], stdin_text="[1]")


def test_render_is_compact_unless_pretty():
    assert render({"a": [1, 2], "b": "é"}) == '{"a":[1,2],"b":"é"}'
    assert "\n  " in render({"a": 1}, pretty=True)


def test_tool_list_doc_and_call():
    runner = CliRunner()
    listed = runner.invoke(app, ["tool", "--list"])
    assert listed.exit_code == 0 and "context_pack(notes_chars, signals_top, memory_chars, journal)" in listed.output
    assert "lineup_steps(day, confirm, trigger) [write]" in listed.output
    doc = runner.invoke(app, ["tool", "free_agents", "--doc"])
    assert doc.exit_code == 0 and "free_agents(top=25" in doc.output and "unrostered" in doc.output
    out = runner.invoke(app, ["tool", "constants"])
    assert out.exit_code == 0 and "groups" in json.loads(out.output) and len(out.output) < 6000
    assert "\n  " not in out.output.strip()
    assert runner.invoke(app, ["tool", "no_such_tool"]).exit_code == 1
    bad = runner.invoke(app, ["tool", "free_agents", "top=x"])
    assert bad.exit_code == 1 and "--doc" in bad.output


def test_job_prompt_uses_the_cli():
    prompt = auto.job_prompt("lineup-guard")
    assert "uv run fbm tool <tool_name>" in prompt and "MCP" not in prompt
