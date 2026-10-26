"""`fbm tool`: call any fbm MCP tool from the shell. Registered by importing this module from fbm.cli.

Same tools, argument validation and JSON output as the MCP server (it goes through `server.call_tool`). It is the
fallback for agent sessions whose MCP connection to fbm drops (e.g. an agent-app bug), so unattended runs still work.
"""

from __future__ import annotations

import json
import sys
from typing import Annotated, Any

import typer

from fbm.cli import _fail, app


def _allowed_types(schema: dict) -> set[str]:
    if "type" in schema:
        t = schema["type"]
        return set(t) if isinstance(t, list) else {t}
    return {t for option in schema.get("anyOf", []) for t in _allowed_types(option)}


def coerce(value: str, schema: dict) -> Any:
    """`key=value` text -> argument. String parameters keep the raw text; others are parsed as JSON when possible."""
    types = _allowed_types(schema)
    if value == "null" and "null" in types:
        return None
    if types and types <= {"string", "null"}:
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def parse_arguments(pairs: list[str], input_schema: dict, stdin_text: str | None = None) -> dict:
    args: dict[str, Any] = {}
    if stdin_text and stdin_text.strip():
        data = json.loads(stdin_text)
        if not isinstance(data, dict):
            raise ValueError("stdin must be a JSON object of tool arguments")
        args.update(data)
    props = input_schema.get("properties", {})
    for pair in pairs:
        key, sep, value = pair.partition("=")
        if not sep or not key:
            raise ValueError(f"expected key=value, got {pair!r}")
        if key not in props:
            raise ValueError(f"unknown argument {key!r}; expected one of: {', '.join(props) or '(none)'}")
        args[key] = coerce(value, props[key])
    return args


async def _tools() -> dict[str, Any]:
    from fbm.mcp_server import server

    return {t.name: t for t in await server.list_tools()}


async def _call(name: str, args: dict) -> str:
    from fbm.mcp_server import server

    result = await server.call_tool(name, args)
    return "\n".join(getattr(c, "text", "") for c in result.content)


@app.command("tool", context_settings={"help_option_names": ["-h", "--help"]})
def tool(
    name: str = typer.Argument(None, help="Tool name, e.g. context_pack. Omit (or use --list) to list tools."),
    arguments: Annotated[
        list[str] | None, typer.Argument(help="Tool arguments as key=value (JSON values for non-strings).")
    ] = None,
    stdin: bool = typer.Option(False, "--stdin", help="Read a JSON object of arguments from stdin."),
    list_tools: bool = typer.Option(False, "--list", help="List tools and their arguments."),
) -> None:
    """Call an fbm MCP tool from the shell (fallback when the agent can't reach the fbm MCP server)."""
    import anyio

    tools = anyio.run(_tools)
    if list_tools or not name:
        for t in sorted(tools.values(), key=lambda t: t.name):
            params = ", ".join(t.input_schema.get("properties", {}))
            typer.echo(f"{t.name}({params})")
        return
    if name not in tools:
        _fail(f"Unknown tool {name!r}. Run `fbm tool --list`.")
    try:
        args = parse_arguments(arguments or [], tools[name].input_schema, sys.stdin.read() if stdin else None)
    except ValueError as err:
        _fail(str(err))
    try:
        text = anyio.run(_call, name, args)
    except Exception as err:  # validation and tool errors, reported like the MCP server does
        _fail(str(err))
    sys.stdout.buffer.write((text + "\n").encode("utf-8"))
    sys.stdout.flush()
    try:
        failed = isinstance(parsed := json.loads(text), dict) and "error" in parsed
    except json.JSONDecodeError:
        failed = False
    if failed:
        raise typer.Exit(code=1)
