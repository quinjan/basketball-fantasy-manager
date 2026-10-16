"""`fbm agent ...`: generate/inspect/remove the AI-agent files (MCP config, roles, skills) for this install."""

from __future__ import annotations

import json
import subprocess
import threading
import time

import typer

from fbm.cli import app, console
from fbm.config import PROJECT_ROOT

agent_app = typer.Typer(help="Wire FBM into your AI agent (Copilot, Claude Code, Codex, Cursor, Gemini, generic).")
app.add_typer(agent_app, name="agent")


def _settings():
    from fbm.settings import load_settings

    return load_settings()


def _resolve_agent(name: str | None) -> str:
    from fbm.agents import AGENT_TARGETS

    agent = name or _settings().agent
    if agent not in AGENT_TARGETS:
        console.print(f"[red]Unknown agent {agent!r}.[/] Choose one of: {', '.join(AGENT_TARGETS)}")
        raise typer.Exit(2)
    return agent


def handshake(command: str, args: list[str], timeout: float = 120.0) -> tuple[bool, str]:
    """Spawn the MCP server, run initialize + tools/list; returns (ok, message)."""
    msgs = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "fbm", "version": "0"},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    ]
    try:
        proc = subprocess.Popen(
            [command, *args],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
        )
    except OSError as e:
        return False, f"cannot start: {e}"
    replies: dict = {}

    def reader() -> None:
        for line in proc.stdout:
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "id" in msg:
                replies[msg["id"]] = msg
            if 2 in replies:
                return

    t = threading.Thread(target=reader, daemon=True)
    try:
        proc.stdin.write("".join(json.dumps(m) + "\n" for m in msgs))
        proc.stdin.flush()
        t.start()
        start = time.time()
        t.join(timeout)
        elapsed = time.time() - start
    finally:
        proc.kill()
        proc.wait()
    if 2 not in replies:
        return False, f"no tools/list reply within {timeout:.0f}s"
    if "error" in replies[2]:
        return False, f"tools/list error: {replies[2]['error']}"
    return True, f"{len(replies[2]['result']['tools'])} tools in {elapsed:.1f}s"


@agent_app.command("install")
def install_cmd(
    agent: str = typer.Option(
        None, "--agent", help="copilot|claude-code|codex|cursor|gemini|generic (default: settings)"
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would be written without writing."),
) -> None:
    """Generate your agent's MCP config, role agents, skills and instructions file for this machine."""
    from fbm.agents import Models, generic_instructions, install, mcp_command, render

    name = _resolve_agent(agent)
    s = _settings()
    command, args = mcp_command(PROJECT_ROOT)
    if name == "generic":
        console.print(generic_instructions(command, args), markup=False, highlight=False)
        return
    plan = render(name, PROJECT_ROOT / "agents", Models(s.gm_model, s.reviewer_model, s.routine_model), command, args)
    console.print(f"MCP command: {command} {' '.join(args)}".rstrip(), markup=False, highlight=False)
    if dry_run:
        for p in plan:
            console.print(f"  would write  {p.path}", markup=False)
        return
    for action, path in install(name, plan, PROJECT_ROOT):
        color = "yellow" if action.startswith("skipped") else "green"
        console.print(f"  [{color}]{action}[/]  {path}", highlight=False)
    if name == "codex":
        console.print("Codex loads .codex/config.toml only for trusted projects: trust this folder when Codex asks.")
    console.print(f"Done. Restart {name} (or reload MCP servers) to pick up changes. Check with `fbm agent status`.")


@agent_app.command("status")
def status_cmd(
    timeout: float = typer.Option(120.0, help="Seconds to wait for the MCP server handshake."),
) -> None:
    """Show installed agent files and check that the fbm MCP server starts and lists its tools."""
    from fbm.agents import load_manifest, mcp_command, status_files

    s = _settings()
    console.print(f"Configured agent: {s.agent}")
    installed = load_manifest(PROJECT_ROOT)["agents"]
    if not installed:
        console.print("No agent files installed (run `fbm agent install`).")
    for name in installed:
        rows = status_files(name, PROJECT_ROOT)
        bad = [r for r in rows if r[0] != "ok"]
        console.print(f"[bold]{name}[/]: {len(rows)} files, {len(bad)} missing/edited")
        for state, path in bad:
            console.print(f"  [yellow]{state}[/]  {path}", highlight=False)
    command, args = mcp_command(PROJECT_ROOT)
    console.print(f"MCP command: {command} {' '.join(args)}".rstrip(), markup=False, highlight=False)
    ok, msg = handshake(command, args, timeout)
    console.print(f"MCP handshake: {'[green]OK[/]' if ok else '[red]FAILED[/]'} — {msg}")
    if not ok:
        raise typer.Exit(1)


@agent_app.command("uninstall")
def uninstall_cmd(agent: str = typer.Option(None, "--agent", help="Agent to remove (default: settings).")) -> None:
    """Remove only the files FBM generated for an agent (per data/agent_install.json)."""
    from fbm.agents import uninstall

    name = _resolve_agent(agent)
    actions = uninstall(name, PROJECT_ROOT)
    if not actions:
        console.print(f"Nothing installed for {name}.")
    for action, path in actions:
        console.print(f"  {action}  {path}", highlight=False)


@agent_app.command("sync-setup-skill")
def sync_setup_skill_cmd() -> None:
    """Copy agents/skills/setup-fbm/SKILL.md into the committed per-agent copies (.github, .claude, .agents)."""
    from fbm.agents import sync_setup_skill

    changed = sync_setup_skill(PROJECT_ROOT)
    console.print("Updated: " + ", ".join(changed) if changed else "Setup skill copies already in sync.")
