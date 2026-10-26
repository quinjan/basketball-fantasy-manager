"""`fbm setup` (guided install), `fbm backup`, and the legacy `fbm sync-memory`. Registered from fbm.cli."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import typer
from rich.table import Table

from fbm import setup_wizard as wiz
from fbm.cli import app, console
from fbm.settings import AGENTS, AUTOMATION_MODES, JOBS

_COLORS = {wiz.OK: "green", wiz.SKIP: "dim", wiz.FAIL: "red", wiz.PENDING: "yellow"}

_AGENT_HELP = {
    "copilot": "GitHub Copilot (app, CLI, or VS Code)",
    "claude-code": "Claude Code",
    "codex": "OpenAI Codex CLI",
    "cursor": "Cursor",
    "gemini": "Gemini CLI",
    "generic": "any other MCP-capable agent",
}
_AUTOMATION_HELP = {
    "none": "no schedule: you open your agent and ask the GM when you want",
    "agent-app": "your agent app's own scheduled automations (if it has them)",
    "scheduler": "the OS scheduler runs your agent headless (needs its CLI)",
    "engine-only": "the OS scheduler runs deterministic fbm jobs only, no AI",
}


def _choice(question: str, options: dict[str, str], default: str) -> str:
    console.print(f"\n[bold]{question}[/]")
    for key, text in options.items():
        console.print(f"  [cyan]{key}[/] — {text}")
    while True:
        answer = typer.prompt("Choice", default=default).strip()
        if answer in options:
            return answer
        console.print(f"[red]Pick one of: {', '.join(options)}[/]")


def _interactive(opts: wiz.SetupOptions, current: dict) -> None:
    """Ask, one question at a time, for every option not given as a flag."""
    agent_s, owner_s = current.get("agent", {}), current.get("owner", {})
    console.print("[bold]FBM setup[/] — press Enter to keep the value in brackets.")
    if opts.agent is None:
        opts.agent = _choice("Which AI agent will run your GM?", _AGENT_HELP, agent_s.get("name", "generic"))
    if opts.gm_model is None:
        console.print("\nModels use your agent's own names; leave empty for the agent's default.")
        opts.gm_model = typer.prompt("GM model (decisions; a frontier model)", default=agent_s.get("gm_model", ""))
    if opts.reviewer_model is None:
        opts.reviewer_model = typer.prompt(
            "Reviewer model (ideally a different model family than the GM)",
            default=agent_s.get("reviewer_model", ""),
        )
    if opts.routine_model is None:
        opts.routine_model = typer.prompt(
            "Routine model (cheap: lineup guard, health)", default=agent_s.get("routine_model", "")
        )
    if opts.timezone is None:
        tz_default = owner_s.get("timezone") if owner_s.get("timezone", "UTC") != "UTC" else wiz.detect_timezone()
        while True:
            tz = typer.prompt("\nYour time zone (IANA, e.g. America/New_York)", default=tz_default).strip()
            if wiz.valid_timezone(tz):
                opts.timezone = tz
                break
            console.print("[red]Not a valid IANA time zone.[/]")
    if opts.automation is None:
        opts.automation = _choice(
            "How should the GM run on a schedule?",
            _AUTOMATION_HELP,
            current.get("automation", {}).get("mode", "none"),
        )
    if opts.jobs is None and opts.automation != "none":
        existing = ",".join(current.get("automation", {}).get("jobs", [])) or "data-sync,lineup-guard,daily-gm"
        while True:
            jobs = wiz.parse_jobs(typer.prompt(f"Jobs ({', '.join(JOBS)})", default=existing)) or []
            if all(j in JOBS for j in jobs):
                opts.jobs = jobs
                break
            console.print("[red]Unknown job name.[/]")
    if opts.league_source is None:
        console.print(
            "\nYahoo's Fantasy API needs an approved application (https://sports.yahoo.com/developer/access/, "
            "can take weeks). Until then FBM runs a demo league over real NBA players, or it can read your league "
            "now through your own Yahoo sign-in (`fbm yahoo login`, read-only)."
        )
        opts.yahoo_approved = typer.confirm("Has Yahoo approved your API access?", default=False)
        if not opts.yahoo_approved:
            opts.yahoo_web_session = typer.confirm(
                "Connect now with your Yahoo sign-in instead (opens a browser window once)?", default=True
            )
        opts.league_source = current.get("league", {}).get("source", "demo")
    notify = current.get("notify", {})
    if opts.email is None:
        opts.email = typer.confirm("\nEmail notifications via Gmail?", default=bool(notify.get("email_enabled")))
    if opts.quiet_hours is None and opts.email:
        opts.quiet_hours = typer.prompt(
            "Quiet hours, local time (e.g. 23:00-07:00; empty = none)", default=notify.get("quiet_hours", "")
        ).strip()
    if opts.expiry_hours is None:
        opts.expiry_hours = typer.prompt(
            "Hours before an unapproved proposal expires",
            default=float(current.get("proposals", {}).get("expiry_hours", 18)),
            type=float,
        )


def _yahoo_interactive() -> None:
    """Walk an approved user through connecting Yahoo; credentials are typed into `fbm auth`'s own prompts."""
    for line in wiz.YAHOO_GUIDE[1:3]:
        console.print(f"  • {line}")
    if not typer.confirm("Done both? Run `fbm auth` now", default=True):
        return
    for args in (["auth"], ["league", "select"], ["settings"]):
        if subprocess.run(wiz.fbm_command(*args), check=False).returncode != 0:
            console.print(f"[red]`fbm {' '.join(args)}` failed; fix it and re-run `fbm setup`.[/]")
            return
    wiz.write_settings(wiz.SetupOptions(league_source="yahoo"))
    for args in (["snapshot"], ["ids"]):
        subprocess.run(wiz.fbm_command(*args), check=False)


def _hashtag_interactive(opts: wiz.SetupOptions, current: dict) -> None:
    """Real (Yahoo) league only: Hashtag Basketball Premium is required. Ask, write [hashtag], then offer sign-in."""
    from fbm.config import LEAGUE_CONFIG_PATH

    ht = current.get("hashtag", {})
    if opts.hashtag is None:
        console.print(
            "\n[bold]Hashtag Basketball Premium[/] is required for your real league: it supplies FBM's projections "
            "and weekly totals (https://hashtagbasketball.com/premium/, Patreon). Your Yahoo league must be imported "
            "in Hashtag's Premium area. FBM makes no suggestions until Hashtag is connected and healthy."
        )
        opts.hashtag = typer.confirm(
            "Do you have Hashtag Premium with this league imported?", default=bool(ht.get("enabled", True))
        )
    if not opts.hashtag:
        console.print("[yellow]Hashtag not enabled: FBM can't make suggestions for your real league yet.[/]")
        return
    if opts.hashtag_league_id is None:
        default = str(ht.get("league_id") or wiz.default_hashtag_league_id(LEAGUE_CONFIG_PATH))
        while True:
            league_id = typer.prompt("Hashtag league id (your Yahoo league id)", default=default).strip()
            if wiz.LEAGUE_ID_RE.match(league_id):
                opts.hashtag_league_id = league_id
                break
            console.print("[red]Numbers only, e.g. 30273.[/]")
    wiz.write_settings(wiz.SetupOptions(hashtag=True, hashtag_league_id=opts.hashtag_league_id))
    status = wiz.hashtag_status(wiz.current_values(), LEAGUE_CONFIG_PATH)
    if status.status == wiz.OK:
        console.print(f"[green]Hashtag: {status.detail}[/]")
        return
    opts.hashtag_login = typer.confirm("Sign in to Hashtag now (opens a browser window once)?", default=True)
    if not opts.hashtag_login:
        return
    for args in (["hashtag", "login"], ["hashtag", "check"], ["hashtag", "import"]):
        if subprocess.run(wiz.fbm_command(*args), check=False).returncode != 0:
            console.print(f"[red]`fbm {' '.join(args)}` failed; fix it, then run it again.[/]")
            return


def _print_report(report: wiz.SetupReport) -> None:
    table = Table(title="FBM setup report")
    for col in ("Step", "Status", "Detail"):
        table.add_column(col)
    for r in report.results:
        table.add_row(r.step, f"[{_COLORS[r.status]}]{r.status}[/]", r.detail)
    console.print(table)
    s = report.settings
    agent, auto = s.get("agent", {}), s.get("automation", {})
    console.print(
        f"Agent [bold]{agent.get('name', 'generic')}[/] · models GM={agent.get('gm_model') or 'default'} "
        f"reviewer={agent.get('reviewer_model') or 'default'} routine={agent.get('routine_model') or 'default'} · "
        f"time zone {s.get('owner', {}).get('timezone', 'UTC')} · league {s.get('league', {}).get('source', 'demo')}"
        f" · hashtag {'on' if s.get('hashtag', {}).get('enabled') else 'off'}"
        f" · automation {auto.get('mode', 'none')} {', '.join(auto.get('jobs', []))}"
    )
    console.print("\n[bold]Next steps[/]")
    for i, step in enumerate(report.next_steps(), 1):
        console.print(f"  {i}. {step}")


@app.command("setup")
def setup_cmd(
    agent: str = typer.Option(None, help=f"Your AI agent: {' | '.join(AGENTS)}."),
    gm_model: str = typer.Option(None, help="Model for GM decisions (as your agent names it)."),
    reviewer_model: str = typer.Option(None, help="Model for independent review (ideally another family)."),
    routine_model: str = typer.Option(None, help="Cheap model for routine runs."),
    timezone: str = typer.Option(None, help="Your IANA time zone (default: detected)."),
    automation: str = typer.Option(None, help=f"Automation mode: {' | '.join(AUTOMATION_MODES)}."),
    jobs: str = typer.Option(None, help=f"Comma-separated jobs: {','.join(JOBS)}."),
    league_source: str = typer.Option(None, help="demo | yahoo."),
    email: bool = typer.Option(None, "--email/--no-email", help="Gmail notifications."),
    quiet_hours: str = typer.Option(None, help='Local quiet hours, e.g. "23:00-07:00" ("" = none).'),
    expiry_hours: float = typer.Option(None, help="Hours before an unapproved proposal expires."),
    yahoo_approved: bool = typer.Option(False, help="Yahoo API access is approved (show/run the connect steps)."),
    hashtag: bool = typer.Option(
        None,
        "--hashtag/--no-hashtag",
        help="Hashtag Basketball Premium data layer (required for a real Yahoo league; not for the demo).",
    ),
    hashtag_league_id: str = typer.Option(None, help="Hashtag league id (default: your Yahoo league id)."),
    skip_data: bool = typer.Option(False, help="Skip the NBA data backfill/sync."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Non-interactive: use flags + existing/default values."),
    check: bool = typer.Option(False, "--check", help="Only re-run verification (writes nothing)."),
) -> None:
    """Set up (or re-check) FBM: settings, memory, NBA data, agent files, automation, health. Safe to re-run."""
    if check:
        report = wiz.check()
        _print_report(report)
        raise typer.Exit(1 if report.failed else 0)
    opts = wiz.SetupOptions(
        agent=agent,
        gm_model=gm_model,
        reviewer_model=reviewer_model,
        routine_model=routine_model,
        timezone=timezone,
        automation=automation,
        jobs=wiz.parse_jobs(jobs),
        league_source=league_source,
        email=email,
        quiet_hours=quiet_hours,
        expiry_hours=expiry_hours,
        skip_data=skip_data,
        yahoo_approved=yahoo_approved,
        hashtag=hashtag,
        hashtag_league_id=hashtag_league_id,
    )
    interactive = not yes and sys.stdin.isatty()
    current = wiz.current_values()
    if interactive:
        _interactive(opts, current)
    elif opts.timezone is None and current.get("owner", {}).get("timezone", "UTC") == "UTC":
        opts.timezone = wiz.detect_timezone()
    errors = wiz.validate(opts)
    if errors:
        for e in errors:
            console.print(f"[red]✗ {e}[/]")
        raise typer.Exit(2)
    if interactive:
        mode = opts.automation or "none"
        if mode != "none":
            console.print("\n[bold]Automation preview[/] (dry run):")
            preview = wiz.run_automation_install(wiz.default_runner, mode, opts.jobs or [], dry_run=True)
            console.print(f"  {preview.status}: {preview.detail}")
            if not typer.confirm("Install this automation?", default=True):
                opts.automation = "none"

    def show(r: wiz.StepResult) -> None:
        console.print(f"[{_COLORS[r.status]}]{r.status:>7}[/] {r.step}: {r.detail}")

    report = wiz.run_setup(opts, on_step=show)
    if interactive and report.settings.get("league", {}).get("source") != "yahoo":
        if opts.yahoo_approved:
            _yahoo_interactive()
            report = wiz.check()
        elif opts.yahoo_web_session:
            subprocess.run(wiz.fbm_command("yahoo", "login"), check=False)
            report = wiz.check()
    if interactive and report.settings.get("league", {}).get("source") == "yahoo" and opts.hashtag is not False:
        _hashtag_interactive(opts, wiz.current_values())
        report = wiz.check()
    console.print()
    _print_report(report)
    raise typer.Exit(1 if report.failed else 0)


@app.command("backup")
def backup_cmd(
    dest: str = typer.Option(None, help="Folder for the zip (default data/backups/, gitignored)."),
) -> None:
    """Zip memory/, reports/, settings, league config and the NBA database to a local folder (no secrets)."""
    from fbm.backup import backup

    path = backup(Path(dest) if dest else None)
    console.print(f"[green]Backup written[/] {path}")


@app.command("sync-memory", hidden=True)
def sync_memory_cmd(
    message: str = typer.Option("", "-m", "--message", help="Ignored (kept for compatibility)."),
    push: bool = typer.Option(True, help="Ignored (kept for compatibility)."),
) -> None:
    """Deprecated: memory is local-only. Use `fbm backup`."""
    from fbm.backup import LOCAL_ONLY_MESSAGE

    console.print(f"[yellow]{LOCAL_ONLY_MESSAGE}[/]")
