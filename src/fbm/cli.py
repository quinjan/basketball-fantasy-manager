"""`fbm` command-line interface."""

from __future__ import annotations

import json
import re
import subprocess
import webbrowser
from datetime import date

import typer
from rich.console import Console
from rich.table import Table

from fbm import credentials
from fbm.config import (
    LEAGUE_CONFIG_PATH,
    MEMORY_DIR,
    RAW_DIR,
    LeagueConfig,
    NotConfiguredError,
    load_league_config,
    save_league_config,
)
from fbm.timeutil import nba_today, nba_tomorrow, parse_date
from fbm.yahoo import discovery
from fbm.yahoo import settings as yahoo_settings
from fbm.yahoo.auth import (
    REDIRECT_URI,
    YahooAuthError,
    authorize_url,
    exchange_code,
    extract_code,
    store_token_response,
)
from fbm.yahoo.client import YahooAPIError, YahooClient
from fbm.yahoo.roster import get_roster
from fbm.yahoo.writes import verify_write_scope

app = typer.Typer(
    no_args_is_help=True, help="FBM — AI front office for Yahoo Fantasy Basketball (9-cat H2H)."
)
league_app = typer.Typer(no_args_is_help=True, help="League discovery and selection.")
app.add_typer(league_app, name="league")
console = Console()


def _fail(message: str) -> None:
    console.print(f"[bold red]Error:[/] {message}")
    raise typer.Exit(code=1)


def _run(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except (credentials.MissingCredentialError, NotConfiguredError, YahooAuthError) as err:
        _fail(str(err))
    except YahooAPIError as err:
        hint = " (the Yahoo app may lack Fantasy Sports Read/Write scope)" if err.is_scope_error else ""
        _fail(f"{err}{hint}")


def _printable(value: str) -> str:
    return "".join(ch for ch in value if ch.isprintable()).strip()


def _clipboard(action: str) -> str:
    command = "Get-Clipboard -Raw" if action == "read" else "Set-Clipboard -Value ' '"
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", command], capture_output=True, text=True, timeout=15
    )
    return result.stdout or ""


def _prompt_secret() -> str:
    console.print(
        "[bold]Client Secret:[/] copy it from the Yahoo app page, then just press [bold]Enter[/] here and "
        "FBM will read it from your clipboard (and clear the clipboard). You can also type it."
    )
    typed = _printable(typer.prompt("Client Secret", hide_input=True, default="", show_default=False))
    secret = typed or _printable(_clipboard("read"))
    if not typed and secret:
        _clipboard("clear")
    if not secret:
        _fail("No Client Secret received.")
    if not re.fullmatch(r"[0-9a-fA-F]{40}", secret):
        console.print(
            f"[yellow]Warning:[/] secret is {len(secret)} chars and not 40 hex chars, which Yahoo secrets "
            "usually are. Double-check you copied the Client Secret, not the Client ID."
        )
        if not typer.confirm("Use it anyway?", default=False):
            raise typer.Exit(code=1)
    return secret


@app.command()
def auth(
    reset: bool = typer.Option(False, "--reset", help="Re-enter the Yahoo Client ID and Secret."),
    scope: str = typer.Option(
        None, "--scope", help="Explicit OAuth scope, e.g. 'fspt-w' to request Fantasy Sports read/write."
    ),
    code: str = typer.Option(
        None,
        "--code",
        help="Redirected URL or code from a consent you already completed (skips the browser).",
    ),
    league_key: str = typer.Option(None, "--league-key", help="League to manage if you have several."),
) -> None:
    """Connect to Yahoo (one-time browser consent), then pick your league."""
    client_id = credentials.get(credentials.YAHOO_CLIENT_ID)
    if reset or not client_id or not credentials.get(credentials.YAHOO_CLIENT_SECRET):
        console.print(
            "Enter the credentials from your Yahoo app page. They are stored in Windows Credential "
            "Manager, never in the repo."
        )
        client_id = _printable(typer.prompt("Yahoo Client ID (Consumer Key)"))
        if not client_id.startswith("dj0y"):
            console.print("[yellow]Warning:[/] Yahoo Client IDs usually start with 'dj0y'.")
        client_secret = _prompt_secret()
        credentials.put(credentials.YAHOO_CLIENT_ID, client_id)
        credentials.put(credentials.YAHOO_CLIENT_SECRET, client_secret)

    if code:
        pasted = code
    else:
        url = authorize_url(client_id, scope=scope)
        console.print("\n[bold]1.[/] A browser window will open. Sign in and click [bold]Agree/Allow[/].")
        console.print(
            f"[bold]2.[/] You'll land on a page that fails to load ({REDIRECT_URI}/?code=...). "
            "That's expected."
        )
        console.print("[bold]3.[/] Copy the [bold]full URL[/] from the address bar and paste it below.\n")
        console.print(f"If no browser opens, visit:\n{url}\n")
        webbrowser.open(url)
        pasted = typer.prompt("Paste the redirected URL (or just the code)")

    def _exchange() -> None:
        payload = exchange_code(
            client_id, credentials.require(credentials.YAHOO_CLIENT_SECRET), extract_code(pasted)
        )
        store_token_response(payload)

    _run(_exchange)
    console.print("[green]Yahoo authorized.[/] Refresh token stored in Windows Credential Manager.\n")
    _run(_select_league, league_key)


def _select_league(league_key: str | None) -> LeagueConfig:
    with YahooClient() as client:
        leagues = discovery.list_nba_leagues(client)
        teams = discovery.list_my_nba_teams(client)
    if not leagues:
        _fail("No Yahoo NBA leagues found for this account this season.")

    if league_key:
        chosen = next((lg for lg in leagues if lg["league_key"] == league_key), None)
        if chosen is None:
            _fail(f"League {league_key} not found among your leagues.")
    elif len(leagues) == 1:
        chosen = leagues[0]
    else:
        _print_leagues(leagues)
        idx = typer.prompt("Pick the league number to manage", type=int)
        if not 1 <= idx <= len(leagues):
            _fail("Invalid selection.")
        chosen = leagues[idx - 1]

    team = discovery.team_for_league(teams, chosen["league_key"])
    if team is None:
        _fail(f"Could not find your team in {chosen['name']}.")
    cfg = LeagueConfig(
        game_key=chosen["game_key"],
        season=chosen["season"],
        league_key=chosen["league_key"],
        league_name=chosen["name"],
        team_key=team["team_key"],
        team_name=team["name"],
    )
    save_league_config(cfg)
    console.print(
        f"[green]Managing[/] [bold]{cfg.team_name}[/] in [bold]{cfg.league_name}[/] "
        f"({cfg.league_key}). Saved to {LEAGUE_CONFIG_PATH.name}."
    )
    return cfg


def _print_leagues(leagues: list[dict[str, str]]) -> None:
    table = Table(title="Your Yahoo NBA leagues")
    for col in ("#", "League", "Key", "Teams", "Scoring", "Draft", "Week"):
        table.add_column(col)
    for i, lg in enumerate(leagues, 1):
        label = yahoo_settings.SCORING_TYPES.get(lg["scoring_type"], lg["scoring_type"])
        table.add_row(
            str(i),
            lg["name"],
            lg["league_key"],
            lg["num_teams"],
            label,
            lg["draft_status"],
            lg["current_week"],
        )
    console.print(table)


@league_app.command("list")
def league_list() -> None:
    """List your Yahoo NBA leagues for the current season."""

    def _list() -> None:
        with YahooClient() as client:
            _print_leagues(discovery.list_nba_leagues(client))

    _run(_list)


@league_app.command("select")
def league_select(league_key: str = typer.Option(None, "--league-key", help="e.g. 466.l.12345")) -> None:
    """Choose which league FBM manages."""
    _run(_select_league, league_key)


@app.command()
def status() -> None:
    """Show what is configured (never prints secrets)."""
    for label, key in (
        ("Yahoo client ID", credentials.YAHOO_CLIENT_ID),
        ("Yahoo client secret", credentials.YAHOO_CLIENT_SECRET),
        ("Yahoo refresh token", credentials.YAHOO_REFRESH_TOKEN),
        ("Gmail app password", credentials.GMAIL_APP_PASSWORD),
    ):
        console.print(f"{label}: {'[green]set[/]' if credentials.get(key) else '[yellow]missing[/]'}")
    try:
        cfg = load_league_config()
        console.print(
            f"League: [bold]{cfg.league_name}[/] ({cfg.league_key}) · Team: [bold]{cfg.team_name}[/] "
            f"({cfg.team_key})"
        )
    except NotConfiguredError:
        console.print("League: [yellow]not selected[/]")
    console.print(f"NBA game day (ET): {nba_today()}")


@app.command()
def settings(
    save: bool = typer.Option(True, help="Write data/raw/league_settings.json + memory/league-settings.md"),
) -> None:
    """Fetch and summarize your league's settings from Yahoo."""

    def _settings() -> None:
        cfg = load_league_config()
        with YahooClient() as client:
            raw = yahoo_settings.fetch_settings(client, cfg.league_key)
        summary = yahoo_settings.summarize(raw)
        console.print(yahoo_settings.to_markdown(summary))
        if save:
            RAW_DIR.mkdir(parents=True, exist_ok=True)
            (RAW_DIR / "league_settings.json").write_text(json.dumps(raw, indent=2), encoding="utf-8")
            (RAW_DIR / "league_settings_summary.json").write_text(
                json.dumps(summary, indent=2), encoding="utf-8"
            )
            MEMORY_DIR.mkdir(parents=True, exist_ok=True)
            (MEMORY_DIR / "league-settings.md").write_text(
                yahoo_settings.to_markdown(summary), encoding="utf-8"
            )
            console.print("[green]Saved[/] data/raw/league_settings*.json and memory/league-settings.md")

    _run(_settings)


def _resolve_day(day: str | None, default: date) -> date:
    return parse_date(day) if day else default


@app.command()
def roster(
    day: str = typer.Option(None, "--date", help="ET game day YYYY-MM-DD (default: today ET)"),
) -> None:
    """Show your roster and selected positions for an ET game day."""

    def _roster() -> None:
        cfg = load_league_config()
        target = _resolve_day(day, nba_today())
        with YahooClient() as client:
            r = get_roster(client, cfg.team_key, target)
        table = Table(title=f"{cfg.team_name} — {target} (editable: {r.is_editable})")
        for col in ("Slot", "Player", "Team", "Eligible", "Status"):
            table.add_column(col)
        for p in r.players:
            table.add_row(p.selected_position, p.name, p.nba_team, ",".join(p.eligible_positions), p.status)
        console.print(table)

    _run(_roster)


@app.command("verify-write")
def verify_write(
    day: str = typer.Option(None, "--date", help="ET game day to test (default: tomorrow ET)"),
    yes: bool = typer.Option(False, "--yes", help="Skip the confirmation prompt."),
) -> None:
    """Prove write access with a harmless no-op lineup re-save (one player, same slot)."""
    target = _resolve_day(day, nba_tomorrow())
    if not yes and not typer.confirm(f"Re-save one player's current slot for {target} (no lineup change)?"):
        raise typer.Exit()

    def _verify() -> None:
        cfg = load_league_config()
        with YahooClient() as client:
            result = verify_write_scope(client, cfg.team_key, target)
        RAW_DIR.mkdir(parents=True, exist_ok=True)
        (RAW_DIR / "write_check.json").write_text(
            json.dumps({"date": target.isoformat(), "ok": result.ok, "detail": result.detail}, indent=2),
            encoding="utf-8",
        )
        color = {True: "green", False: "red", None: "yellow"}[result.ok]
        console.print(f"[{color}]{result.detail}[/]")
        if result.ok is False:
            raise typer.Exit(code=2)

    _run(_verify)


if __name__ == "__main__":
    app()
