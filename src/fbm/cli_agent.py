"""`fbm agent ...`: generate/inspect/remove the AI-agent files (roles, skills, shell allow-lists) for this install."""

from __future__ import annotations

import subprocess
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


def tools_check(timeout: float = 120.0) -> tuple[bool, str]:
    """Run `uv run fbm tool --list` from the repo the way an agent does; returns (ok, message)."""
    from fbm.agents import TOOL_COMMAND

    cmd = [*TOOL_COMMAND.split(), "--list"]
    start = time.time()
    try:
        proc = subprocess.run(
            cmd, cwd=PROJECT_ROOT, capture_output=True, text=True, encoding="utf-8", timeout=timeout, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, f"`{' '.join(cmd)}` failed: {e}"
    if proc.returncode != 0:
        return False, f"`{' '.join(cmd)}` exited {proc.returncode}: {(proc.stderr or proc.stdout).strip()[-300:]}"
    n = sum(1 for line in proc.stdout.splitlines() if "(" in line)
    return True, f"{n} tools in {time.time() - start:.1f}s"


@agent_app.command("install")
def install_cmd(
    agent: str = typer.Option(
        None, "--agent", help="copilot|claude-code|codex|cursor|gemini|generic (default: settings)"
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would be written without writing."),
) -> None:
    """Generate your agent's role agents, skills, instructions file and shell allow-list for this machine."""
    from fbm.agents import TOOL_COMMAND, Models, generic_instructions, install, render

    name = _resolve_agent(agent)
    s = _settings()
    if name == "generic":
        console.print(generic_instructions(), markup=False, highlight=False)
        return
    plan = render(name, PROJECT_ROOT / "agents", Models(s.gm_model, s.reviewer_model, s.routine_model))
    console.print(f"Agents call FBM with: {TOOL_COMMAND} <name> key=value ...", markup=False, highlight=False)
    if dry_run:
        for p in plan:
            console.print(f"  would write  {p.path}", markup=False)
        return
    for action, path in install(name, plan, PROJECT_ROOT):
        color = "yellow" if action.startswith("skipped") else "green"
        console.print(f"  [{color}]{action}[/]  {path}", highlight=False)
    if name == "codex":
        console.print("Codex loads .codex/config.toml only for trusted projects: trust this folder when Codex asks.")
    console.print(f"Done. Restart {name} to pick up changes. Check with `fbm agent status`.")


@agent_app.command("status")
def status_cmd(
    timeout: float = typer.Option(120.0, help="Seconds to wait for `fbm tool --list`."),
) -> None:
    """Show installed agent files and check that agents can run `uv run fbm tool`."""
    from fbm.agents import load_manifest, status_files

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
    ok, msg = tools_check(timeout)
    console.print(f"fbm tool: {'[green]OK[/]' if ok else '[red]FAILED[/]'} — {msg}")
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
