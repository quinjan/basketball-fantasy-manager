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
        hint = (
            " (the Yahoo app may lack Fantasy Sports Read permission or API access approval)"
            if err.is_scope_error
            else ""
        )
        _fail(f"{err}{hint}")


def _printable(value: str) -> str:
    return "".join(ch for ch in value if ch.isprintable()).strip()


def _clipboard(action: str) -> str:
    if sys.platform == "win32":
        command = "Get-Clipboard -Raw" if action == "read" else "Set-Clipboard -Value ' '"
        argv = ["powershell", "-NoProfile", "-Command", command]
    elif sys.platform == "darwin":
        argv = ["pbpaste"] if action == "read" else ["pbcopy"]
    else:
        argv = ["xclip", "-selection", "clipboard"] + (["-o"] if action == "read" else [])
    try:
        result = subprocess.run(
            argv, input="" if action != "read" else None, capture_output=True, text=True, timeout=15
        )
    except (OSError, subprocess.SubprocessError):
        return ""
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
    console.print("[green]Yahoo authorized.[/] Refresh token stored in your OS credential store.\n")
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
    """Yahoo writes are OUT OF SCOPE (owner decision). Optional: prove write access with a harmless no-op lineup
    re-save; it only takes effect if config/settings.toml [yahoo] api_writes = true."""
    console.print(
        "[yellow]Yahoo writes are out of scope (owner decision): roster changes are exact manual steps. "
        "This check only matters if [yahoo] api_writes = true in config/settings.toml.[/]"
    )
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


# ---------------------------------------------------------------- League tools (demo until Yahoo approval)

demo_app = typer.Typer(no_args_is_help=True, help="Demo league (simulated draft) for use before Yahoo access.")
app.add_typer(demo_app, name="demo")


def _context():
    from fbm.analytics.context import build_context
    from fbm.analytics.valuation import config_from_settings

    try:
        return build_context(store.connect(), date.today(), config_from_settings(_league_summary()))
    except RuntimeError as err:
        _fail(str(err))


def _load_league(demo: bool):
    from fbm.gm import services as svc

    if not demo:
        _fail("Live Yahoo league data is pending Yahoo API approval. Use --demo for now.")
    try:
        return svc.active_league()
    except svc.GMError as err:
        _fail(str(err))


def _first_game_day(conn, day: date) -> date:
    row = conn.execute(
        "SELECT MIN(et_date) FROM games WHERE season_type = 2 AND et_date >= ?", (day.isoformat(),)
    ).fetchone()
    return date.fromisoformat(row[0]) if row and row[0] else day


def _fmt(cat, value: float) -> str:
    return f"{value:.3f}" if cat.kind == "pct" else f"{value:.1f}"


@demo_app.command("build")
def demo_build(
    seed: int = typer.Option(1, help="Draft randomness seed."),
    my_pick: int = typer.Option(5, min=1, max=12, help="Your draft slot."),
) -> None:
    """Simulate a 12-team snake draft from current projections and save it as the demo league."""
    from fbm.league.demo import build_demo_league, save_league

    ctx = _context()
    league = build_demo_league(ctx.values, ctx.season, ctx.statuses(), my_slot=my_pick, seed=seed)
    save_league(league)
    table = Table(title=f"{league.my_team.name} — demo roster (pick {my_pick})")
    for col in ("Slot", "Player", "NBA", "Pos", "G-score", "Status"):
        table.add_column(col)
    for p in league.my_team.players:
        table.add_row(
            p.selected,
            p.name,
            p.nba_team,
            "/".join(e for e in p.eligible if e != "UTIL"),
            f"{ctx.values.loc[p.player_id, 'g_total']:+.2f}",
            p.status,
        )
    console.print(table)
    console.print("[green]Saved[/] data/demo_league.json")


@app.command()
def lineup(
    day: str = typer.Option(None, "--date", help="ET game day (default: today ET)"),
    demo: bool = typer.Option(False, "--demo", help="Use the demo league."),
) -> None:
    """Optimal lineup for one ET game day (max games started, then value): tip-off locks and IL moves applied."""
    from fbm.gm import services as svc

    league = _load_league(demo)
    target = _resolve_day(day, nba_today())
    plan = _svc(svc._lineup_plan, league, target, True)
    res, team = plan["res"], league.my_team
    value = svc.ctx().value_map()
    nba = svc.teams_playing(store.connect(), target, target)[target]
    order = {s: i for i, s in enumerate([*dict.fromkeys(league.starting_slots()), "BN", "IL", "IL+"])}
    table = Table(title=f"{team.name} — {target} (ET) · {res.starters_with_games} starters with games")
    for col in ("Slot", "Player", "NBA", "Game", "Status", "G-score", "Change"):
        table.add_column(col)
    for p in sorted(team.players, key=lambda x: order.get(res.assignments[x.player_id], 99)):
        new = res.assignments[p.player_id]
        lock = " 🔒" if p.player_id in plan["locked"] else ""
        table.add_row(
            new,
            p.name + lock,
            p.nba_team,
            "yes" if p.nba_team in nba else "",
            p.status,
            f"{value.get(p.player_id, 0):+.2f}",
            f"{p.selected} → {new}" if p.selected != new else "",
        )
    console.print(table)
    if res.benched_with_games:
        names = {p.player_id: p.name for p in team.players}
        console.print(f"[yellow]Benched with a game:[/] {', '.join(names[pid] for pid in res.benched_with_games)}")
    if plan.get("swing_categories"):
        label = {c.key: c.label for c in league.categories}.get
        w = plan["swing_weights"]
        cats = ", ".join(f"{label(k, k)} {w[k]:.2f}" for k in plan["swing_categories"])
        console.print(f"[magenta]Swing categories this week (bench tie-break weights):[/] {cats}")
        pnames = {p.player_id: p.name for p in team.players}
        for t in plan.get("tiebreak_changes", []):
            console.print(
                f"[magenta]  tie-break:[/] {pnames[t['player_id']]}: {t['ros_lineup']} by ROS value → "
                f"{t['swing_lineup']} for swing cats"
            )
    for m in plan["moves"]:
        style = "red" if m.blocked else "cyan"
        console.print(
            f"[{style}]IL move{' (blocked)' if m.blocked else ''}:[/] {m.name}: {m.from_slot} → "
            f"{m.to_slot} ({m.reason})"
        )
    if plan["locked"]:
        console.print("[dim]🔒 = game started; slot locked[/]")


@app.command()
def playoffs(
    scope: str = typer.Option("my_roster", help="my_roster | free_agents | league"),
    top: int = typer.Option(20),
) -> None:
    """Fantasy-playoff schedule: best/worst NBA schedules and players' playoff games."""
    from fbm.gm import services as svc

    r = _svc(svc.playoff_outlook, scope, top)
    console.print(
        f"Playoff weeks (ET): {', '.join(r['playoff_weeks_et'])} · average team games "
        f"{r['average_team_games']:.1f} · source: {r['source']}"
    )
    console.print(f"[green]Best:[/] {', '.join(f'{t} {g}' for t, g in r['best_schedules'].items())}")
    console.print(f"[red]Worst:[/] {', '.join(f'{t} {g}' for t, g in r['worst_schedules'].items())}")
    table = Table(title=f"Players ({scope})")
    for col in ("Player", "NBA", "Owner", "Playoff games", "vs avg", "Playoff value"):
        table.add_column(col)
    for p in r["players"]:
        table.add_row(
            p["player"], p["nba"], p["owner"], str(p["games"]), f"{p['vs_avg']:+.1f}", f"{p['playoff_value']:+.2f}"
        )
    console.print(table)
    console.print(f"[dim]Trade playoff weight right now: {r['trade_playoff_weight_now']}[/]")


@app.command()
def punts(
    scenarios: str = typer.Option(None, help="Specific builds to test, e.g. 'FT%;3PM,FT%' (default: auto)."),
    moves: int = typer.Option(None, help="Free-agent swaps each build may make (default: punt.fa_moves)."),
    top: int = typer.Option(8),
) -> None:
    """Punt-scenario evaluator: which categories should the build give up?"""
    from fbm.gm import services as svc

    r = _svc(svc.punt_scenarios, scenarios, moves, top)
    now = r["now_without_moves"]
    console.print(
        f"Approved strategy: [bold]{r['approved_strategy']}[/] · roster now {now['expected_cats']:.2f} expected cats, "
        f"P(win week) {now['p_win_week']:.0%} · ranked by {r['metric']} · {r['scenarios_tested']} builds tested"
    )
    table = Table(title="Punt builds (after simulation-confirmed free-agent swaps)")
    for col in ("Build", "Exp cats", "vs strategy", "P(win wk)", "Reliable", "Swaps kept", "Lens conflicts"):
        table.add_column(col)
    for s in r["scenarios"]:
        table.add_row(
            ("★ " if s["approved_strategy"] else "") + s["build"],
            f"{s['expected_cats']:.2f}",
            f"{s['delta_vs_strategy']:+.2f}",
            f"{s['p_win_week']:.0%}",
            str(s["reliable_cats"]),
            "; ".join(f"+{x['add']} / -{x['drop']}" for x in s["fa_swaps"]) or "-",
            "; ".join(f"{x['lens_wanted']} ({x['sim_change']:+.2f})" for x in s["lens_conflicts"]) or "-",
        )
    console.print(table)
    rec = r["recommendation"]
    color = "yellow" if rec["action"] == "propose_switch" else "green"
    console.print(f"[bold {color}]{rec['action'].upper()}[/]: {rec['reason']}")
    best = next((s for s in r["scenarios"] if s["build"] == rec["build"]), None)
    if best and best["buy_targets"]:
        console.print(
            "[dim]Buy targets under "
            + best["build"]
            + ": "
            + ", ".join(
                f"{b['player']} ({b['team']}, #{b['market_rank']}→#{b['lens_rank']})" for b in best["buy_targets"]
            )
            + "[/]"
        )
    if best and best["sell_candidates"]:
        console.print(
            "[dim]Sell candidates: "
            + ", ".join(f"{b['player']} (#{b['market_rank']}→#{b['lens_rank']})" for b in best["sell_candidates"])
            + "[/]"
        )


@app.command()
def waiver(
    player: str = typer.Argument(..., help="Player to claim."),
    drop: str = typer.Option(None, help="Player to drop (default: my lowest-value active player)."),
    free_agent: bool = typer.Option(False, "--free-agent", help="He is a free agent (no waiver claim needed)."),
) -> None:
    """Rolling waivers: claim now, wait for him to clear, or pass?"""
    from fbm.gm import services as svc

    r = _svc(svc.waiver_decision, player, drop, not free_agent)
    color = {"claim": "green", "add": "green", "wait": "yellow", "pass": "red"}[r["action"]]
    console.print(f"[bold {color}]{r['action'].upper()}[/] {r['player']} (drop {r['drop']}): {r['reason']}")
    console.print(
        f"ROS gain {r['ros_gain']:+.2f} · my priority {r['my_priority']} · bar "
        f"{r['threshold_for_my_priority']} · P(claimed ahead of me) {r['p_claimed_before_me']:.0%} · "
        f"P(claimed by anyone if I wait) {r['p_claimed_by_another_team']:.0%}"
    )
    if r["teams_likely_to_claim"]:
        console.print(f"[dim]Teams he'd improve: {', '.join(r['teams_likely_to_claim'])}[/]")


@app.command("health")
def health_cmd() -> None:
    """System health checks (OK/WARN/FAIL). Exit code 1 if any check FAILs."""
    from rich.markup import escape

    from fbm.gm import services as svc

    r = svc.health_check()
    colors = {"OK": "green", "WARN": "yellow", "FAIL": "red"}
    table = Table(title=f"FBM health · {r['phase']} · overall {r['overall']}")
    for col in ("Check", "Status", "Detail", "Fix"):
        table.add_column(col)
    for c in r["checks"]:
        table.add_row(c["check"], f"[{colors[c['status']]}]{c['status']}[/]", escape(c["detail"]), escape(c["fix"]))
    console.print(table)
    if r["overall"] == "FAIL":
        raise typer.Exit(1)


@app.command("constants")
def constants_cmd() -> None:
    """All tunable engine constants (value, kind, source). Override in config/constants.toml."""
    from fbm.gm import services as svc

    r = svc.constants_table()
    table = Table(title="Engine constants")
    for col in ("Key", "Value", "Unit", "Kind", "Source"):
        table.add_column(col)
    for c in r["constants"]:
        value = f"[bold]{c['value']}[/] (default {c['default']})" if c["overridden"] else str(c["value"])
        table.add_row(c["key"], value, c["unit"], c["kind"], c["source"])
    console.print(table)
    if r["unknown_overrides"]:
        console.print(f"[red]Unknown keys in config/constants.toml:[/] {', '.join(r['unknown_overrides'])}")


@app.command()
def sim(
    start: str = typer.Option(None, help="First ET day to simulate (default: today or first game day)."),
    opponent: str = typer.Option(None, help="Opponent team key (default: next team in the demo rotation)."),
    n: int = typer.Option(10000, help="Simulations."),
    demo: bool = typer.Option(False, "--demo", help="Use the demo league."),
) -> None:
    """Monte Carlo the rest of this fantasy week against an opponent: per-category win odds."""
    from fbm.analytics.simulation import simulate_matchup
    from fbm.analytics.week import date_range, fantasy_week, plan_team_week, teams_playing

    league = _load_league(demo)
    ctx = _context()
    day0 = _first_game_day(ctx.conn, _resolve_day(start, nba_today()))
    ws, we = fantasy_week(day0)
    days = date_range(day0, we)
    playing = teams_playing(ctx.conn, ws, we)
    me = league.my_team
    if opponent:
        opp = league.team(opponent)
    else:
        idx = [t.team_key for t in league.teams].index(me.team_key)
        opp = league.teams[(idx + 1) % len(league.teams)]
    value = ctx.value_map()
    models = ctx.models([*me.player_ids(), *opp.player_ids()])
    my_week, _ = plan_team_week(me, days, playing, league.starting_slots(), value)
    opp_week, _ = plan_team_week(opp, days, playing, league.starting_slots(), value)
    odds = simulate_matchup(my_week, opp_week, models, league.categories, n_sims=n, seed=42)

    table = Table(
        title=f"{me.name} vs {opp.name} — {day0} → {we} · games started {len(my_week.player_games)} vs "
        f"{len(opp_week.player_games)}"
    )
    for col in ("Cat", "Me (mean)", "Opp (mean)", "Win", "Tie", "Loss"):
        table.add_column(col, justify="right")
    for o in odds.categories:
        style = "green" if o.p_win >= 0.65 else "red" if o.p_loss >= 0.65 else "yellow"
        table.add_row(
            o.category.label,
            _fmt(o.category, o.mean_a),
            _fmt(o.category, o.mean_b),
            f"[{style}]{o.p_win:.0%}[/]",
            f"{o.p_tie:.0%}",
            f"{o.p_loss:.0%}",
        )
    console.print(table)
    swing = ", ".join(c.category.label for c in odds.swing()) or "none"
    console.print(
        f"Expected categories won: [bold]{odds.expected_score:.2f}[/] / {len(league.categories)} · "
        f"P(win majority) {odds.p_majority:.0%} · Swing categories: {swing}"
    )
    synthetic = [p.name for p in [*me.players, *opp.players] if models[p.player_id].synthetic]
    if synthetic:
        console.print(f"[dim]Synthetic (thin history) models: {', '.join(synthetic)}[/]")


@app.command()
def profile(
    demo: bool = typer.Option(False, "--demo", help="Use the demo league."),
    n: int = typer.Option(4000, help="Simulations per team."),
) -> None:
    """League-wide category strength: each team's typical-week win rate per category."""
    from fbm.analytics.team_profile import league_profile

    league = _load_league(demo)
    ctx = _context()
    models = ctx.models([pid for t in league.teams for pid in t.player_ids()])
    profiles = sorted(league_profile(league, models, n_sims=n), key=lambda p: p.expected_score, reverse=True)
    table = Table(title=f"{league.name} — category win rate vs the league (typical week)")
    for col in ("Team", *[c.label for c in league.categories], "Exp cats", "P(win wk)"):
        table.add_column(col, justify="left" if col == "Team" else "right")
    for p in profiles:
        cells = []
        for c in league.categories:
            v = p.cat_score[c.key]
            style = "green" if v >= 0.6 else "red" if v <= 0.4 else ""
            cells.append(f"[{style}]{v:.0%}[/]" if style else f"{v:.0%}")
        name = f"[bold]{p.name}[/]" if p.team_key == league.my_team_key else p.name
        table.add_row(name, *cells, f"{p.expected_score:.2f}", f"{p.p_majority:.0%}")
    console.print(table)
    mine = next(p for p in profiles if p.team_key == league.my_team_key)
    ordered = sorted(league.categories, key=lambda c: mine.cat_score[c.key])
    console.print(
        "My weakest → strongest: "
        + ", ".join(f"{c.label} ({mine.cat_score[c.key]:.0%}, #{mine.cat_rank[c.key]})" for c in ordered)
    )


# ---------------------------------------------------------------- Notifications

notify_app = typer.Typer(no_args_is_help=True, help="Email notifications (Gmail).")
app.add_typer(notify_app, name="notify")


@notify_app.command("setup")
def notify_setup() -> None:
    """Store your Gmail address + App Password in your OS credential store."""
    console.print(
        "Create an App Password at https://myaccount.google.com/apppasswords (requires 2-Step "
        "Verification). It's 16 letters; spaces are ignored."
    )
    address = _printable(typer.prompt("Gmail address"))
    console.print("Copy the App Password, then press Enter to read it from the clipboard (or type it).")
    typed = _printable(typer.prompt("App Password", hide_input=True, default="", show_default=False))
    password = (typed or _printable(_clipboard("read"))).replace(" ", "")
    if not typed and password:
        _clipboard("clear")
    if not re.fullmatch(r"[a-zA-Z]{16}", password):
        console.print(f"[yellow]Warning:[/] got {len(password)} characters; Gmail App Passwords are 16 letters.")
        if not typer.confirm("Save anyway?", default=False):
            raise typer.Exit(code=1)
    credentials.put(credentials.GMAIL_ADDRESS, address)
    credentials.put(credentials.GMAIL_APP_PASSWORD, password)
    console.print("[green]Saved.[/] Run `fbm notify test`.")


@notify_app.command("test")
def notify_test() -> None:
    """Send a test email."""
    from fbm.notify.email import NotifyError, send_email

    try:
        result = send_email(
            "Test — FBM notifications are working",
            "## It works\n\nThis is a test from **FBM**.\n\n| Check | Status |\n|---|---|\n| Gmail SMTP | ✅ |",
            kind="alert",
        )
    except NotifyError as err:
        _fail(str(err))
    console.print(f"[green]{result['status']}[/] → {result.get('to', '')}")


# ---------------------------------------------------------------- Proposals (owner-side)

proposals_app = typer.Typer(no_args_is_help=True, help="Review and decide GM proposals.")
app.add_typer(proposals_app, name="proposals")


@proposals_app.command("list")
def proposals_list(status: str = typer.Option("open", help="open | pending_approval | executed | all")) -> None:
    """List proposals."""
    from fbm.gm import proposals as props

    rows = props.list_proposals(store.connect(), None if status == "all" else status, 50)
    table = Table(title=f"Proposals ({status})")
    for col in ("Ref", "Status", "Kind", "Title", "Review", "Expires (UTC)"):
        table.add_column(col)
    for p in rows:
        review = f"{p.review_verdict or ''} {p.review_model or ''}".strip()
        table.add_row(p.ref, p.status, p.kind, p.title, review, p.expires_at[:16])
    console.print(table)


@proposals_app.command("show")
def proposals_show(ref: str) -> None:
    """Show one proposal in full."""
    from fbm.gm import services as svc

    console.print_json(json.dumps(svc.get_proposal(ref), default=str))


def _decide(ref: str, approved: bool, note: str) -> None:
    from fbm.gm import services as svc
    from fbm.gm.proposals import ProposalError

    try:
        p = svc.record_owner_decision(ref, approved, note or ("approve" if approved else "reject") + " (CLI)")
        console.print(f"{p['ref']} → [bold]{p['status']}[/]")
        if approved:
            p = svc.execute_proposal(ref)
            console.print(f"Execution: [bold]{p['status']}[/]")
            steps = (p.get("execution") or {}).get("manual_steps")
            if steps:
                console.print("\n".join(steps))
    except ProposalError as err:
        _fail(str(err))


@proposals_app.command("approve")
def proposals_approve(ref: str, note: str = typer.Option("", help="Your words, recorded in the audit trail.")) -> None:
    """Approve (then immediately re-validate and execute) a reviewed proposal."""
    _decide(ref, True, note)


@proposals_app.command("reject")
def proposals_reject(ref: str, note: str = typer.Option("", help="Why (recorded).")) -> None:
    """Reject a reviewed proposal."""
    _decide(ref, False, note)


@notify_app.command("outbox")
def notify_outbox(limit: int = typer.Option(15, help="Rows to show.")) -> None:
    """Recent notifications and their delivery status."""
    rows = (
        store.connect()
        .execute("SELECT created_at, status, subject, detail FROM outbox ORDER BY id DESC LIMIT ?", (limit,))
        .fetchall()
    )
    table = Table(title="Outbox")
    for col in ("When (UTC)", "Status", "Subject", "Detail"):
        table.add_column(col)
    for r in rows:
        table.add_row(r["created_at"][:16], r["status"], r["subject"], (r["detail"] or "")[:60])
    console.print(table)


# ---------------------------------------------------------------- Streaming, trades, learning, draft (via services)


def _svc(fn, *args, **kwargs):
    from fbm.gm import services as svc
    from fbm.gm.proposals import ProposalError

    try:
        result = fn(*args, **kwargs)
    except (svc.GMError, ProposalError, FileNotFoundError, ValueError) as err:
        _fail(str(err))
    if isinstance(result, dict) and "error" in result:
        _fail(result["error"])
    return result


def _pct(x: float) -> str:
    return f"{x:.0%}"


@app.command()
def stream(
    opponent: str = typer.Option(None, help="Opponent team key or name (default: current opponent)."),
    adds: int = typer.Option(None, help="Adds available (default: weekly limit minus adds used)."),
    protect: str = typer.Option("", help="Comma-separated players that must not be dropped."),
) -> None:
    """Streaming plan for the rest of this week (best add/drop sequence within the add limit)."""
    from fbm.gm import services as svc

    r = _svc(svc.streaming_plan, opponent, adds, 4, protect)
    b, a = r["baseline"], r["after_plan"]
    console.print(
        f"[bold]{r['window_et']}[/] vs {r['opponent']} · adds {r['adds_available']} available "
        f"({r['adds_used_this_week']}/{r['weekly_add_limit']} used) · punts {', '.join(r['punts_applied']) or 'none'}"
    )
    table = Table(title="Streaming plan")
    for col in ("Effective (ET)", "Add", "Drop", "+Exp cats", "Add games", "Drop games lost", "ROS cost"):
        table.add_column(col)
    for m in r["moves"]:
        table.add_row(
            m["effective_et"],
            m["add"],
            m["drop"] or "-",
            f"{m['gain_expected_cats']:+.2f}",
            str(m["add_games"]),
            str(m["drop_games_lost"]),
            f"{m['ros_value_cost']:.2f}",
        )
    console.print(table if r["moves"] else "[yellow]No move clears the minimum gain — hold.[/]")
    console.print(
        f"Expected cats {b['expected_cats']:.2f} → [bold]{a['expected_cats']:.2f}[/] · P(win week) "
        f"{_pct(b['p_win_majority'])} → [bold]{_pct(a['p_win_majority'])}[/] · games started "
        f"{r['games_started']['before']} → {r['games_started']['after']}"
    )


trade_app = typer.Typer(no_args_is_help=True, help="Trade evaluation and search.")
app.add_typer(trade_app, name="trade")


def _print_trade(t: dict) -> None:
    me, them = t["me"], t["them"]
    console.print(
        f"[bold]{', '.join(me['gives'])}[/] → {them['team']} for [bold]{', '.join(me['gets'])}[/]  ·  "
        f"verdict: [cyan]{t['verdict']}[/]"
    )
    table = Table()
    for col in ("Side", "Exp cats before", "after", "Δ", "Ranking value Δ", "Drops", "FA fills"):
        table.add_column(col)
    for s in (me, them):
        table.add_row(
            s["team"],
            f"{s['expected_cats_before']:.2f}",
            f"{s['expected_cats_after']:.2f}",
            f"{s['expected_cats_delta']:+.3f}",
            f"{s['ranking_value_delta']:+.2f}",
            ", ".join(s["forced_drops"]) or "-",
            ", ".join(s["free_agent_fills"]) or "-",
        )
    console.print(table)
    deltas = ", ".join(f"{k} {v:+.0%}" for k, v in me["cat_win_rate_delta"].items() if abs(v) >= 0.02)
    console.print(f"[dim]My category win-rate changes: {deltas or 'small'}[/]")
    for note in t["notes"]:
        console.print(f"[dim]- {note}[/]")


@trade_app.command("eval")
def trade_eval(
    partner: str = typer.Option(..., help="Partner team key or name."),
    give: str = typer.Option(..., help="Comma-separated players I give."),
    get: str = typer.Option(..., help="Comma-separated players I get."),
) -> None:
    """Evaluate a specific trade for both sides."""
    from fbm.gm import services as svc

    _print_trade(_svc(svc.evaluate_trade_tool, partner, give, get))


@trade_app.command("find")
def trade_find(
    partner: str = typer.Option(None, help="Limit to one partner team."),
    limit: int = typer.Option(6, help="Max trades to show."),
) -> None:
    """Search the league for trades that help us and are defensible for them."""
    from fbm.gm import services as svc

    r = _svc(svc.find_trades_tool, partner, 2, 2, limit)
    if not r["trades"]:
        console.print("[yellow]No trades found that help us and are defensible for the other side.[/]")
    for t in r["trades"]:
        _print_trade(t)
        console.print()


learn_app = typer.Typer(no_args_is_help=True, help="Learning loop: decision grades and simulator calibration.")
app.add_typer(learn_app, name="learn")


@learn_app.command("grade")
def learn_grade(days: int = typer.Option(28, help="Look back this many days.")) -> None:
    """Grade executed moves by realized production."""
    from fbm.gm import services as svc

    r = _svc(svc.grade_decisions_tool, days)
    table = Table(title="Decision grades")
    for col in ("Ref", "Move", "Window (ET)", "Games", "Δ (team-week SD)", "Grade"):
        table.add_column(col)
    for d in r["decisions"]:
        table.add_row(d["ref"], d["title"], d["window_et"], str(d["games_counted"]), f"{d['delta']:+.2f}", d["grade"])
    console.print(table if r["decisions"] else "No executed moves to grade yet.")


@learn_app.command("calibration")
def learn_calibration() -> None:
    """Simulator calibration (Brier score vs coin flip, reliability)."""
    from fbm.gm import services as svc

    console.print_json(json.dumps(_svc(svc.prediction_calibration), default=str))


@learn_app.command("retro")
def learn_retro(week_start: str = typer.Option(None, help="Monday (ET) of the week; default last week.")) -> None:
    """Retro data for a fantasy week (JSON)."""
    from fbm.gm import services as svc

    console.print_json(json.dumps(_svc(svc.weekly_retro_data_tool, week_start), default=str))


draft_app = typer.Typer(no_args_is_help=True, help="Draft assistant (board, live draft, mock drafts).")
app.add_typer(draft_app, name="draft")


@draft_app.command("board")
def draft_board_cmd(
    top: int = typer.Option(40),
    punt: str = typer.Option(None, help="e.g. 'FT%,TO'"),
    position: str = typer.Option(None, help="PG/SG/G/SF/PF/F/C"),
) -> None:
    """Draft board with tiers and ADP."""
    from fbm.gm import services as svc

    r = _svc(svc.draft_board, top, punt, position)
    table = Table(title=f"Draft board · punts {', '.join(r['punts']) or 'none'}")
    for col in ("#", "Tier", "Player", "NBA", "Pos", "Value", "ADP", "Value vs ADP"):
        table.add_column(col)
    for p in r["players"]:
        vva = p["value_vs_adp"]
        vva_s = "" if vva is None else (f"[green]+{vva}[/]" if vva > 5 else f"[red]{vva}[/]" if vva < -5 else str(vva))
        table.add_row(
            str(p["rank"]),
            str(p["tier"]),
            p["player"],
            p["nba"],
            "/".join(p["positions"]),
            f"{p['value']:+.2f}",
            "" if p["adp"] is None else f"{p['adp']:.1f}",
            vva_s,
        )
    console.print(table)


def _print_recommend(r: dict) -> None:
    if r.get("done"):
        console.print(f"[green]Draft complete.[/] My team: {', '.join(r['my_team'])}")
        return
    who = "[bold green]YOUR PICK[/]" if r["is_my_pick"] else f"team {r['on_clock']} on the clock"
    console.print(f"Pick {r['overall']} (round {r['round']}) — {who} · my next picks {r['my_next_picks']}")
    table = Table()
    for col in ("Player", "Pos", "Value", "Build score", "Tier", "ADP", "P(avail next)"):
        table.add_column(col)
    for x in r["recommendations"]:
        p = x["p_available_next_pick"]
        table.add_row(
            x["player"],
            "/".join(x["positions"]),
            f"{x['value']:+.2f}",
            f"{x['build_score']:+.2f}",
            str(x["tier"]),
            "" if x["adp"] is None else f"{x['adp']:.1f}",
            "" if p is None else _pct(p),
        )
    console.print(table)
    if r["position_needs"]:
        console.print(f"[yellow]Position needs: {', '.join(r['position_needs'])}[/]")
    if r["projected_best_at_my_following_pick"]:
        console.print(
            f"[dim]Likely still there at my following pick: {', '.join(r['projected_best_at_my_following_pick'])}[/]"
        )


@draft_app.command("start")
def draft_start_cmd(
    teams: int = typer.Option(12),
    slot: int = typer.Option(..., help="My draft slot (1-based)."),
    rounds: int = typer.Option(13),
    punt: str = typer.Option("", help="e.g. 'FT%'"),
) -> None:
    """Start a live draft."""
    from fbm.gm import services as svc

    _print_recommend(_svc(svc.draft_start, teams, slot, rounds, punt))


@draft_app.command("pick")
def draft_pick_cmd(player: str = typer.Argument(..., help="Player drafted by the team on the clock.")) -> None:
    """Record the next pick (in draft order)."""
    from fbm.gm import services as svc

    r = _svc(svc.draft_pick, player)
    rec = r["recorded"]
    console.print(
        f"Recorded #{rec['overall']}: {rec['player']} (team {rec['team_slot']}{', mine' if rec['mine'] else ''})"
    )
    _print_recommend(r)


@draft_app.command("undo")
def draft_undo_cmd() -> None:
    """Undo the last pick."""
    from fbm.gm import services as svc

    r = _svc(svc.draft_undo)
    console.print(f"Undid: {r['undone']}")
    _print_recommend(r)


@draft_app.command("recommend")
def draft_recommend_cmd() -> None:
    """Advice for the current pick."""
    from fbm.gm import services as svc

    _print_recommend(_svc(svc.draft_recommend))


@draft_app.command("adp")
def draft_adp_cmd(refresh: bool = typer.Option(False, "--refresh", help="Fetch Yahoo ADP (draft_analysis).")) -> None:
    """Show which ADP source the board uses; --refresh fetches Yahoo ADP (falls back to ESPN)."""
    from fbm.gm import services as svc

    if refresh:
        r = _svc(svc.draft_adp_refresh)
        if r["refreshed"]:
            console.print(f"Yahoo ADP cached: {r['players']} players ({r['mapped']} mapped, {r['unmapped']} unmapped)")
        else:
            console.print(f"[yellow]{r['message']}[/]")
    s = _svc(svc.draft_adp_status)
    console.print(
        f"ADP source: [bold]{s['adp_source']}[/] · {s['players_with_adp']} players · Yahoo cached "
        f"{s['yahoo_cached']} (needs ≥{s['yahoo_min_players']})"
    )


def _print_sync(r: dict) -> None:
    for p in r["new_picks"]:
        tag = " [green](mine)[/]" if p["mine"] else ""
        unmapped = " [dim](unmapped)[/]" if not p["mapped"] else ""
        console.print(f"#{p['overall']} (R{p['round']}): {p['player']}{tag}{unmapped}")
    if r["my_turn"] and r.get("recommendations"):
        _print_recommend(r["recommendations"])


@draft_app.command("live")
def draft_live_cmd(
    interval: float = typer.Option(None, help="Seconds between polls (default: draft.live_interval_sec)."),
) -> None:
    """Poll Yahoo draft results until the draft completes (Ctrl+C to stop); advice when I'm on the clock."""
    import time

    from fbm.constants import C
    from fbm.gm import services as svc

    wait = float(interval if interval is not None else C("draft.live_interval_sec"))
    last_turn_pick = None
    try:
        while True:
            r = _svc(svc.draft_sync_yahoo)
            if r["my_turn"] and last_turn_pick == r["next_overall"]:
                r = {**r, "recommendations": None}  # already shown for this pick
            _print_sync(r)
            if r["my_turn"]:
                last_turn_pick = r["next_overall"]
            if r["done"]:
                console.print("[green]Draft complete.[/]")
                break
            time.sleep(wait)
    except KeyboardInterrupt:
        console.print("Stopped polling.")


@draft_app.command("mock")
def draft_mock_cmd(
    slot: int = typer.Option(1),
    teams: int = typer.Option(12),
    rounds: int = typer.Option(13),
    punt: str = typer.Option(""),
    runs: int = typer.Option(3),
) -> None:
    """Mock drafts scored by season-level simulation."""
    from fbm.gm import services as svc

    r = _svc(svc.draft_mock, slot, teams, rounds, punt, runs)
    for run in r["runs"]:
        console.print(
            f"[bold]Run {run['run']}[/]: expected cats {run['my_expected_cats']:.2f} · rank {run['rank_of_teams']}"
        )
        console.print("  " + " · ".join(run["my_picks"]))
    console.print(
        f"Average expected categories: [bold]{r['avg_expected_cats']:.2f}[/] "
        f"(slot {slot}, punts {', '.join(r['punts']) or 'none'})"
    )


@app.command()
def dashboard(port: int = typer.Option(8501, help="Local port."), open_browser: bool = typer.Option(True)) -> None:
    """Launch the local Streamlit dashboard."""
    from importlib.resources import files

    app_path = str(files("fbm.dashboard").joinpath("app.py"))
    cmd = [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        app_path,
        "--server.port",
        str(port),
        "--server.headless",
        "true" if not open_browser else "false",
        "--browser.gatherUsageStats",
        "false",
    ]
    console.print(f"Dashboard → http://localhost:{port}  (Ctrl+C to stop)")
    subprocess.run(cmd, check=False)


from fbm import (  # noqa: E402,F401  (each module registers its commands on `app`)
    cli_agent,  # `fbm agent`
    cli_automation,  # `fbm automation`
    cli_setup,  # setup, backup, sync-memory
    cli_yahoo,  # Yahoo league commands
)

if __name__ == "__main__":
    app()
