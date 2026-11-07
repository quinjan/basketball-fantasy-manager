"""`fbm tool`: call any FBM tool from the shell. Registered by importing this module from fbm.cli.

This is how AI agents use FBM: `uv run fbm tool <name> key=value ...`. Output is compact JSON on stdout (exit code 1
when the tool returns {"error": ...}). `--list` names every tool; `--doc` shows one tool's arguments and description,
so agents load only the docs they need. Output is capped (see fbm.output_cap); `--full` lifts the cap.
"""

from __future__ import annotations

import json
import sys
from typing import Annotated, Any

import typer

from fbm.cli import _fail, app
from fbm.output_cap import DEFAULT_MAX_CHARS, cap


def parse_arguments(name: str, pairs: list[str], stdin_text: str | None = None) -> dict[str, Any]:
    """`key=value` pairs (+ an optional JSON object from stdin) -> validated, typed tool arguments."""
    from fbm.tools import bind

    args: dict[str, Any] = {}
    if stdin_text and stdin_text.strip():
        data = json.loads(stdin_text)
        if not isinstance(data, dict):
            raise ValueError("stdin must be a JSON object of tool arguments")
        args.update(data)
    for pair in pairs:
        key, sep, value = pair.partition("=")
        if not sep or not key:
            raise ValueError(f"expected key=value, got {pair!r}")
        args[key] = value
    return bind(name, args)


def render(result: Any, pretty: bool = False) -> str:
    if pretty:
        return json.dumps(result, ensure_ascii=False, indent=2, default=str)
    return json.dumps(result, ensure_ascii=False, separators=(",", ":"), default=str)


@app.command("tool", context_settings={"help_option_names": ["-h", "--help"]})
def tool(
    name: str = typer.Argument(None, help="Tool name, e.g. context_pack. Omit (or use --list) to list tools."),
    arguments: Annotated[
        list[str] | None, typer.Argument(help="Tool arguments as key=value (JSON values for non-strings).")
    ] = None,
    stdin: bool = typer.Option(False, "--stdin", help="Read a JSON object of arguments from stdin."),
    list_tools: bool = typer.Option(False, "--list", help="List tools and their arguments."),
    doc: bool = typer.Option(False, "--doc", help="Show the tool's arguments (with defaults) and description."),
    pretty: bool = typer.Option(False, "--pretty", help="Indented JSON (for people; agents should not use it)."),
    max_chars: int = typer.Option(
        DEFAULT_MAX_CHARS,
        "--max-chars",
        help="Output budget: larger results are shrunk and saved in full to data/tool_out/<tool>.json (0 = no cap).",
    ),
    full: bool = typer.Option(False, "--full", help="No output cap (same as --max-chars 0). Costs context."),
) -> None:
    """Call an FBM tool from the shell. Prints JSON; exit code 1 if the tool reports an error."""
    from fbm.tools import TOOLS, call

    if list_tools or not name:
        for t in sorted(TOOLS.values(), key=lambda t: t.name):
            typer.echo(f"{t.name}({', '.join(t.params)}){' [write]' if t.write else ''}")
        return
    if name not in TOOLS:
        _fail(f"Unknown tool {name!r}. Run `fbm tool --list`.")
    if doc:
        t = TOOLS[name]
        typer.echo(f"{t.signature()}{'  [writes FBM records]' if t.write else ''}\n\n{t.doc}")
        return
    try:
        args = parse_arguments(name, arguments or [], sys.stdin.read() if stdin else None)
    except ValueError as err:  # includes JSONDecodeError
        _fail(f"{err}. See `fbm tool {name} --doc`.")
    result = call(name, args)
    result = cap(name, result, 0 if full else max_chars)
    sys.stdout.buffer.write((render(result, pretty) + "\n").encode("utf-8"))
    sys.stdout.flush()
    if isinstance(result, dict) and "error" in result:
        raise typer.Exit(code=1)
