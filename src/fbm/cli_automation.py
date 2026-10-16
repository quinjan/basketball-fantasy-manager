"""`fbm automation` commands (scheduled jobs). Registered by importing this module from fbm.cli."""

from __future__ import annotations

import typer

from fbm import automation as auto
from fbm.cli import app, console
from fbm.settings import load_settings

automation_app = typer.Typer(no_args_is_help=True, help="Scheduled jobs (OS scheduler / agent app automations).")
app.add_typer(automation_app, name="automation")


def _jobs(jobs: str | None, default: tuple[str, ...]) -> list[str]:
    if jobs is None:
        return list(default)
    return [j.strip() for j in jobs.split(",") if j.strip()]


@automation_app.command("jobs")
def jobs_cmd() -> None:
    """Show the job catalog (ET schedule, skill, model role, engine-only commands)."""
    s = load_settings()
    for j in auto.CATALOG.values():
        et = "; ".join(t.describe() for t in j.et_triggers)
        local = "; ".join(t.describe() for t in auto.owner_triggers(j.name, s.owner_timezone))
        engine = " && ".join("fbm " + " ".join(c) for c in j.engine_commands) or "(AI only)"
        console.print(f"[bold]{j.name}[/] — {j.description}")
        console.print(f"  ET: {et}\n  {s.owner_timezone}: {local}")
        console.print(f"  skill: {j.skill or '-'}  role: {j.role}  engine-only: {engine}")


@automation_app.command("run")
def run_cmd(
    job: str = typer.Argument(..., help="Job name, e.g. lineup-guard."),
    mode: str = typer.Option(None, "--mode", help="Override [automation] mode (scheduler | engine-only)."),
) -> None:
    """Run one job now (the entry point every scheduler calls). Logs to reports/automation/."""
    s = load_settings()
    errors = auto.validate(s.agent, mode or s.automation_mode, [job])
    if errors:
        for e in errors:
            console.print(f"[red]{e}[/]")
        raise typer.Exit(2)
    code, log = auto.run_job(job, s, mode)
    console.print(f"{job}: exit {code}" + (f" — log {log}" if log else ""))
    raise typer.Exit(code)


@automation_app.command("install")
def install_cmd(
    mode: str = typer.Option(None, "--mode", help="none | agent-app | scheduler | engine-only (default: settings)."),
    jobs: str = typer.Option(None, "--jobs", help="Comma-separated jobs (default: settings [automation] jobs)."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print exactly what would be installed; change nothing."),
) -> None:
    """Install scheduled jobs for the chosen mode."""
    s = load_settings()
    mode = mode or s.automation_mode
    selected = _jobs(jobs, s.automation_jobs)
    errors = auto.validate(s.agent, mode, selected)
    if errors:
        for e in errors:
            console.print(f"[red]{e}[/]")
        raise typer.Exit(2)
    if mode == "none" or not selected:
        console.print("Automation mode 'none' or no jobs selected: nothing to install.")
        return
    if mode == "agent-app":
        console.print(auto.agent_app_instructions(s, selected), markup=False, highlight=False)
        return
    entries = auto.plan_entries(s, selected, mode)
    skipped = sorted(set(selected) - {e.job for e in entries})
    if skipped:
        console.print(f"[yellow]Skipped (AI-only, nothing to run in {mode}): {', '.join(skipped)}[/]")
    backend = auto.detect_backend()
    change = auto.next_offset_change(s.owner_timezone)
    note = f"Times are {s.owner_timezone} wall-clock, computed for today's ET offset."
    if change:
        note += f" The ET/local offset changes on {change}: re-run `fbm automation install` then."
    if dry_run:
        console.print(f"[bold]Dry run[/] — backend {backend}, mode {mode}. {note}")
        console.print(auto.render_plan(entries, backend), markup=False, highlight=False)
        return
    for m in auto.install_entries(entries, backend):
        console.print(m, markup=False)
    console.print(note)


@automation_app.command("list")
def list_cmd() -> None:
    """List installed FBM scheduler entries."""
    items = auto.list_installed(auto.detect_backend())
    for i in items:
        console.print(i, markup=False, highlight=False)
    if not items:
        console.print("No FBM scheduler entries installed.")


@automation_app.command("remove")
def remove_cmd(
    jobs: str = typer.Option(None, "--jobs", help="Comma-separated jobs (default: all FBM entries)."),
) -> None:
    """Remove FBM scheduler entries (never touches other entries)."""
    msgs = auto.remove_installed(auto.detect_backend(), _jobs(jobs, auto.JOBS))
    for m in msgs:
        console.print(m, markup=False)
    if not msgs:
        console.print("No FBM scheduler entries to remove.")
