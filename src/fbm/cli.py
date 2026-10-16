"""`fbm` command-line interface."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import webbrowser
from datetime import date, datetime

import typer
from rich.console import Console
from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn, TimeRemainingColumn
from rich.table import Table

from fbm import credentials
from fbm.config import (
    DATA_DIR,
    LEAGUE_CONFIG_PATH,
    MEMORY_DIR,
    RAW_DIR,
    LeagueConfig,
    NotConfiguredError,
    load_league_config,
    save_league_config,
)
from fbm.data import queries, sync
from fbm.data.espn import EspnClient
from fbm.data.espn import current_season as espn_current_season
from fbm.store import db as store
from fbm.timeutil import ET, nba_today, nba_tomorrow, parse_date
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

app = typer.Typer(no_args_is_help=True, help="FBM — AI front office for Yahoo Fantasy Basketball (9-cat H2H).")
league_app = typer.Typer(no_args_is_help=True, help="League discovery and selection.")
app.add_typer(league_app, name="league")

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure") and (_stream.encoding or "").lower() not in ("utf-8", "utf8"):
        _stream.reconfigure(encoding="utf-8", errors="replace")
console = Console(width=None if sys.stdout.isatty() else 180)


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
            f"[bold]2.[/] You'll land on a page that fails to load ({REDIRECT_URI}/?code=...). That's expected."
        )
        console.print("[bold]3.[/] Copy the [bold]full URL[/] from the address bar and paste it below.\n")
        console.print(f"If no browser opens, visit:\n{url}\n")
        webbrowser.open(url)
        pasted = typer.prompt("Paste the redirected URL (or just the code)")

    def _exchange() -> None:
        payload = exchange_code(client_id, credentials.require(credentials.YAHOO_CLIENT_SECRET), extract_code(pasted))
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
            f"League: [bold]{cfg.league_name}[/] ({cfg.league_key}) · Team: [bold]{cfg.team_name}[/] ({cfg.team_key})"
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
            (RAW_DIR / "league_settings_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
            MEMORY_DIR.mkdir(parents=True, exist_ok=True)
            (MEMORY_DIR / "league-settings.md").write_text(yahoo_settings.to_markdown(summary), encoding="utf-8")
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


# ---------------------------------------------------------------- NBA data (ESPN)

data_app = typer.Typer(no_args_is_help=True, help="NBA data (ESPN) sync and inspection.")
app.add_typer(data_app, name="data")


def _progress_bar(label: str):
    progress = Progress(
        TextColumn(label),
        BarColumn(),
        MofNCompleteColumn(),
        TimeRemainingColumn(),
        console=console,
        transient=True,
    )
    task = progress.add_task(label, total=None)

    def update(done: int, total: int) -> None:
        progress.update(task, completed=done, total=total)

    return progress, update


@data_app.command("sync")
def data_sync() -> None:
    """Daily sync: schedule status, new box scores, season averages, injuries, news."""
    conn = store.connect()
    progress, update = _progress_bar("box scores")
    with progress:
        results = sync.daily_sync(conn, EspnClient(), date.today(), progress=update)
    for r in results:
        console.print(f"[green]✓[/] {r}")


@data_app.command("backfill")
def data_backfill(
    season: int = typer.Option(None, help="ESPN season end-year (2026 = 2025-26). Default: last season."),
    workers: int = typer.Option(4, min=1, max=8, help="Parallel box-score downloads."),
) -> None:
    """One-time load of a completed season's games + box scores (for priors and variance)."""
    target = season or espn_current_season(date.today()) - 1
    conn = store.connect()
    progress, update = _progress_bar(f"{target} box scores")
    with progress:
        results = sync.backfill_season(conn, EspnClient(), target, date.today(), workers=workers, progress=update)
    for r in results:
        console.print(f"[green]✓[/] {r}")


@data_app.command("status")
def data_status() -> None:
    """What's in the local NBA store."""
    s = queries.store_status(store.connect())
    console.print(
        f"Teams {s['teams']} · Players {s['players']} · Box lines {s['box_lines']} · "
        f"Injuries {s['injuries']} · News {s['news']}"
    )
    table = Table(title="Seasons (regular season)")
    for col in ("Season", "Games", "Completed", "Box synced", "First", "Last"):
        table.add_column(col)
    for row in s["seasons"]:
        table.add_row(
            str(row["season"]),
            str(row["games"]),
            str(row["completed"]),
            str(row["boxed"]),
            row["first"],
            row["last"],
        )
    console.print(table)
    for row in s["season_averages"]:
        console.print(f"Season averages {row['season']}: {row['players']} players")
    for row in s["sync_log"]:
        console.print(f"[dim]{row['job']}: {row['last_run']} — {row['detail']}[/]")


@data_app.command("games")
def data_games(
    day: str = typer.Option(None, "--date", help="ET date YYYY-MM-DD (default: today ET)"),
) -> None:
    """NBA games on an ET date."""
    target = _resolve_day(day, nba_today())
    rows = queries.games_on(store.connect(), target)
    table = Table(title=f"NBA games — {target} (ET)")
    for col in ("Away", "Home", "Tip (ET)", "Tip (local)", "State"):
        table.add_column(col)
    for g in rows:
        start = datetime.fromisoformat(g["start_utc"])
        table.add_row(
            g["away"],
            g["home"],
            start.astimezone(ET).strftime("%H:%M"),
            start.astimezone().strftime("%a %H:%M"),
            g["state"],
        )
    console.print(table if rows else f"No games stored for {target}. Run `fbm data sync`.")


@data_app.command("injuries")
def data_injuries(team: str = typer.Option(None, help="NBA team abbreviation, e.g. BOS")) -> None:
    """Current ESPN injury report."""
    conn = store.connect()
    sql, args = "SELECT * FROM injuries_current", []
    if team:
        sql, args = sql + " WHERE team = ?", [team.upper()]
    rows = conn.execute(sql + " ORDER BY team, name", args).fetchall()
    table = Table(title="Injuries (ESPN)")
    for col in ("Team", "Player", "Status", "Return", "Note"):
        table.add_column(col)
    for r in rows:
        table.add_row(r["team"], r["name"], r["status"], r["return_date"] or "", (r["short_comment"] or "")[:70])
    console.print(table)


# ---------------------------------------------------------------- Analytics


def _league_summary() -> dict | None:
    path = RAW_DIR / "league_settings_summary.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


@app.command()
def rank(
    season: int = typer.Option(None, help="Value one ESPN season (end-year). Default: ROS projection blend."),
    top: int = typer.Option(40, min=1, help="Rows to show."),
    punt: str = typer.Option(None, help="Comma-separated punted categories, e.g. 'FT%,TO'."),
    metric: str = typer.Option("g", help="'g' (G-score, H2H-aware) or 'z'."),
    team: str = typer.Option(None, help="Filter to one NBA team (ESPN abbr)."),
    min_gp: int = typer.Option(15, help="Minimum games played to qualify (season mode)."),
    csv: bool = typer.Option(False, help="Also write data/rankings.csv"),
) -> None:
    """Rank players by 9-cat value (G-score or Z-score)."""
    from fbm.analytics import players as player_tables
    from fbm.analytics.categories import parse_category_list
    from fbm.analytics.valuation import config_from_settings, value_players

    if metric not in ("g", "z"):
        _fail("metric must be 'g' or 'z'")
    try:
        punts = tuple(c.key for c in parse_category_list(punt))
    except ValueError as err:
        _fail(str(err))
    conn = store.connect()
    current = espn_current_season(date.today())
    if season:
        table, variance_season, label = (
            player_tables.season_table(conn, season),
            season,
            f"{season - 1}-{season % 100:02d}",
        )
    else:
        table = player_tables.projection_table(conn, current, current - 1)
        has_current = bool(table["gp_current"].sum())
        variance_season = current if has_current else current - 1
        label = "ROS projection" + (
            "" if has_current else f" (pre-season: {current - 2}-{(current - 1) % 100:02d} stats)"
        )
    if table.empty:
        _fail("No player stats stored. Run `fbm data sync` and `fbm data backfill`.")
    cfg = config_from_settings(_league_summary(), punts=punts, min_gp=min_gp if season else 5)
    weekly = player_tables.weekly_table(conn, variance_season)
    result, stats = value_players(table, cfg, weekly=weekly if not weekly.empty else None)
    if team:
        result = result[result["team"].str.upper() == team.upper()]

    total_col = f"{metric}_total"
    result = result.sort_values(total_col, ascending=False)
    title = f"{metric.upper()}-score rankings — {label} · pool {cfg.pool_size} · κ={cfg.kappa:.3f}" + (
        f" · punt {', '.join(punts)}" if punts else ""
    )
    table_out = Table(title=title)
    for col in ("#", "Player", "Team", "GP", "MIN", *[c.label for c in cfg.categories], "Total"):
        table_out.add_column(col, justify="right" if col not in ("Player", "Team") else "left")
    for i, (_, row) in enumerate(result.head(top).iterrows(), 1):
        cells = []
        for c in cfg.categories:
            v = row[f"{metric}_{c.key}"]
            style = "dim" if c.key in punts else ("green" if v >= 1 else "red" if v <= -1 else "")
            cells.append(f"[{style}]{v:+.2f}[/]" if style else f"{v:+.2f}")
        table_out.add_row(
            str(i),
            str(row["name"]),
            str(row["team"]),
            str(int(row["gp"])),
            f"{row['minutes']:.1f}",
            *cells,
            f"[bold]{row[total_col]:+.2f}[/]",
        )
    console.print(table_out)
    if weekly.empty:
        console.print("[yellow]No box scores for that season yet — G-scores equal Z-scores (τ=0).[/]")
    else:
        taus = ", ".join(f"{c.label} {stats.tau2[c.key] ** 0.5 / stats.sd[c.key]:.2f}" for c in cfg.categories)
        console.print(f"[dim]Week-to-week noise relative to player spread (τ/σ): {taus}[/]")
    if csv:
        out = DATA_DIR / "rankings.csv"
        result.to_csv(out)
        console.print(f"Wrote {out}")


if __name__ == "__main__":
    app()
