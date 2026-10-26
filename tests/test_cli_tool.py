"""`fbm tool`: shell bridge to the fbm MCP tools (fallback when an agent can't reach the MCP server)."""

import json

import pytest
from typer.testing import CliRunner

from fbm import automation as auto
from fbm.cli import app
from fbm.cli_tool import coerce, parse_arguments

SCHEMA = {
    "properties": {
        "query": {"type": "string"},
        "day": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "top": {"type": "integer"},
        "confirm": {"type": "boolean"},
        "payload_json": {"type": "string"},
    }
}


def test_coerce_keeps_strings_raw_and_parses_the_rest():
    props = SCHEMA["properties"]
    assert coerce("123", props["query"]) == "123"
    assert coerce('{"add": 1}', props["payload_json"]) == '{"add": 1}'
    assert coerce("null", props["day"]) is None
    assert coerce("2026-10-21", props["day"]) == "2026-10-21"
    assert coerce("25", props["top"]) == 25
    assert coerce("true", props["confirm"]) is True
    assert coerce("x", props["top"]) == "x"  # left for the tool's validation to reject


def test_parse_arguments_pairs_and_stdin():
    args = parse_arguments(["top=5", "query=Luka Dončić"], SCHEMA, stdin_text='{"confirm": false, "top": 1}')
    assert args == {"confirm": False, "top": 5, "query": "Luka Dončić"}
    with pytest.raises(ValueError, match="key=value"):
        parse_arguments(["top"], SCHEMA)
    with pytest.raises(ValueError, match="unknown argument"):
        parse_arguments(["nope=1"], SCHEMA)
    with pytest.raises(ValueError, match="JSON object"):
        parse_arguments([], SCHEMA, stdin_text="[1]")


def test_tool_list_and_call():
    runner = CliRunner()
    listed = runner.invoke(app, ["tool", "--list"])
    assert listed.exit_code == 0 and "context_pack()" in listed.output
    assert "lineup_steps(day, confirm, trigger)" in listed.output
    out = runner.invoke(app, ["tool", "constants"])
    assert out.exit_code == 0 and "constants" in json.loads(out.output)
    assert runner.invoke(app, ["tool", "no_such_tool"]).exit_code == 1
    assert runner.invoke(app, ["tool", "free_agents", "top=x"]).exit_code == 1


def test_job_prompt_allows_only_the_cli_bridge():
    prompt = auto.job_prompt("lineup-guard")
    assert "uv run fbm tool <tool_name>" in prompt and "only shell command allowed" in prompt
