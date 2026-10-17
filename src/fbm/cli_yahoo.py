"""CLI commands for the live Yahoo league (read-only views). Registered by importing this module from fbm.cli."""

from __future__ import annotations

import sys
from datetime import UTC, datetime

import typer
from rich.table import Table

from fbm import credentials
from fbm.cli import _clipboard, _fail, _printable, _run, _select_league, _svc, app, console
from fbm.config import LEAGUE_CONFIG_PATH, NotConfiguredError, load_league_config
from fbm.timeutil import nba_today, parse_date
from fbm.yahoo import discovery
from fbm.yahoo.client import YahooAPIError, YahooClient

yahoo_app = typer.Typer(
    no_args_is_help=True,
    help="Yahoo access: sign in with your own Yahoo web session (no API approval needed), check every data feed.",
)
app.add_typer(yahoo_app, name="yahoo")


def _demo(r: dict) -> bool:
    if r.get("available") is False:
        console.print(f"[yellow]{r['message']}[/]")
        return True
    return False


@app.command()
def snapshot() -> None:
    """Re-read the Yahoo league now (rosters, scoreboard, standings, waivers, ownership)."""
    from fbm.gm import services as svc

    r = _svc(svc.snapshot_tool)
    if _demo(r):
        return
    start, end = r["window"]
    console.print(
        f"[bold]{r['league']}[/] week {r['week']} ({start} → {end}) · opponent {r['opponent'] or '-'} · "
        f"{r['teams']} teams · fetched {r['fetched_at']}"
    )
    if r["unmatched_players"]:
        console.print(f"[yellow]{len(r['unmatched_players'])} unmatched players[/] — {r['how_to_fix_unmatched']}")
        for p in r["unmatched_players"]:
            console.print(f"  {p['yahoo_id']}: {p['name']} ({p['team']})")


@app.command()
def ids() -> None:
    """Yahoo players not mapped to ESPN ids, and how to fix them."""
    from fbm.gm import services as svc

    r = _svc(svc.yahoo_ids_tool)
    if not r["unmatched"]:
        console.print("[green]Every Yahoo player is mapped to an ESPN id.[/]")
        return
    table = Table(title=f"Unmatched Yahoo players ({len(r['unmatched'])})")
    for col in ("Yahoo id", "Name", "Team"):
        table.add_column(col)
    for p in r["unmatched"]:
        table.add_row(p["yahoo_id"], p["name"], p["team"] or "")
    console.print(table)
    console.print(r["how_to_fix"])


@app.command()
def standings() -> None:
    """League standings (Yahoo)."""
    from fbm.gm import services as svc

    r = _svc(svc.standings_tool)
    if _demo(r):
        return
    table = Table(title=f"{r['league']} standings · as of {r['as_of']}")
    for col in ("Rank", "Team", "W-L-T", "Pct", "GB", "Seed"):
        table.add_column(col)
    for row in r["standings"]:
        name = f"[bold]{row['name']}[/]" if row["mine"] else row["name"]
        pct = f"{row['pct']:.3f}" if row.get("pct") is not None else "-"
        table.add_row(
            str(row.get("rank") or "-"),
            name,
            f"{row['wins']}-{row['losses']}-{row['ties']}",
            pct,
            row.get("games_back") or "-",
            str(row.get("playoff_seed") or "-"),
        )
    console.print(table)


def _fmt(v) -> str:
    if v is None:
        return "-"
    return f"{v:.3f}" if v < 1 and v != 0 else f"{v:g}"


@app.command()
def scoreboard() -> None:
    """This week's live category totals for every matchup (mine first)."""
    from fbm.gm import services as svc

    r = _svc(svc.scoreboard_tool)
    if _demo(r):
        return
    for m in r["matchups"]:
        a, b = (m["teams"] + ["-"])[:2]
        won = m["categories_won"]
        table = Table(title=f"{a} {won.get(a, 0)} – {won.get(b, 0)} {b} · {m['status']}")
        for col in ("Cat", a, b, "Leader"):
            table.add_column(col)
        for c in m["categories"]:
            table.add_row(c["category"], _fmt(c["values"].get(a)), _fmt(c["values"].get(b)), c["leader"] or "tie")
        console.print(table)
    console.print(f"as of {r['as_of']}")


@app.command()
def transactions(
    count: int = typer.Option(25, "--count", help="How many recent transactions."),
    team: str = typer.Option(None, "--team", help="Only transactions involving this team (name or key)."),
) -> None:
    """Recent league transactions (Yahoo)."""
    from fbm.gm import services as svc

    r = _svc(svc.transactions_tool, count, team)
    if _demo(r):
        return
    table = Table(title=f"Transactions ({r['count']})")
    for col in ("Time (UTC)", "Type", "Status", "Moves"):
        table.add_column(col)
    for tx in r["transactions"]:
        moves = "; ".join(f"{p['action']} {p['player']} ({p['from']} → {p['to']})" for p in tx["players"])
        table.add_row(tx["time_utc"] or "-", tx["type"], tx["status"], moves)
    console.print(table)


@app.command()
def offers() -> None:
    """My pending trades (incoming offers evaluated) and waiver claims. Accept/reject manually in Yahoo."""
    from fbm.gm import services as svc

    r = _svc(svc.pending_offers_tool)
    if _demo(r):
        return
    for o in r["incoming_trades"]:
        gets = [p["player"] for p in o["players"] if p["to"] != o["trader"]]
        gives = [p["player"] for p in o["players"] if p["to"] == o["trader"]]
        console.print(f"[bold]Offer from {o['trader']}[/]: you give {', '.join(gives)} · you get {', '.join(gets)}")
        ev = o.get("evaluation") or {}
        if "error" in ev:
            console.print(f"  [yellow]not evaluated: {ev['error']}[/]")
        else:
            console.print(f"  verdict [bold]{ev['verdict']}[/] · expected cats {ev['expected_cats_delta']:+.3f}")
        for step in o["manual_steps"]["accept"]:
            console.print(f"  accept: {step}")
    for label in ("outgoing_trades", "waiver_claims"):
        for tx in r[label]:
            moves = ", ".join(f"{p['action']} {p['player']}" for p in tx["players"])
            console.print(f"{label.replace('_', ' ')}: {tx['status']} · {moves}")
    if not any(r[k] for k in ("incoming_trades", "outgoing_trades", "waiver_claims")):
        console.print("No pending trades or waiver claims.")
    console.print(f"[dim]{r['note']}[/]")


@app.command()
def intel(
    team: str = typer.Option(None, "--team", "-t", help="Profile one manager (default: league-wide intel)."),
    sync: bool = typer.Option(False, "--sync", help="Sync Yahoo transaction history + manager notes first."),
) -> None:
    """Opponent intel: manager activity, streaming, trades, builds, best trade partners."""
    from fbm.gm import services as svc

    if sync:
        s = _svc(svc.sync_transactions_tool)
        if not _demo(s):
            console.print(f"Synced {s['new']} new transactions ({s['total_stored']} stored, {s['pages']} pages).")
    if team:
        p = _svc(svc.manager_profile, team)
        console.print(f"[bold]{p['name']}[/] · {p['tier']} (confidence {p['confidence']}, {p['moves']} moves)")
        console.print(
            f"adds/week {p['adds_per_week']} (recent {p['recent_adds_per_week']}) · waiver claims {p['waiver_claims']}"
            f" · FA adds {p['fa_adds']} · streaming {p['streaming_propensity']} · trades {p['trades_completed']} done"
            f" / {p['trades_proposed']} proposed · last activity {p['last_activity'] or '-'}"
        )
        console.print(
            f"strong {', '.join(p['strong_cats']) or '-'} · weak {', '.join(p['weak_cats']) or '-'} · likely punts "
            f"{', '.join(p['likely_punts']) or '-'} · fit with me {p['complementary_fit_with_me']}"
        )
        for m in p["recent_moves"]:
            console.print(f"  {m['utc']}: {'; '.join(m['moves'])}")
        if p["note"]:
            console.print(f"[dim]{p['note']}[/]")
        return
    r = _svc(svc.league_intel)
    table = Table("Team", "Tier", "Conf", "Adds/wk", "Recent", "Claims", "Stream%", "Trades")
    for row in r["activity_ranking"]:
        table.add_row(
            row["team"],
            row["tier"],
            row["confidence"],
            str(row["adds_per_week"]),
            str(row["recent_adds_per_week"]),
            str(row["waiver_claims"]),
            "-" if row["streaming_propensity"] is None else f"{row['streaming_propensity']:.0%}",
            f"{row['trades_completed']}/{row['trades_proposed']}",
        )
    console.print(table)
    console.print("[bold]Best trade partners[/] (" + r["partner_order"] + ")")
    for row in r["best_trade_partners"]:
        console.print(
            f"  {row['team']} · fit {row['complementary_fit']} · {row['tier']} · strong "
            f"{', '.join(row['their_strong_cats']) or '-'} · weak {', '.join(row['their_weak_cats']) or '-'}"
        )
    if r["note"]:
        console.print(f"[dim]{r['note']}[/]")


# ---------------------------------------------------------------- Yahoo access (web session)

_PASTE_STEPS = (
    "In the browser where you're signed in to Yahoo, open:\n   {url}\n   (you should see XML listing your leagues)",
    "Press F12 → Network tab → reload the page → click the request → Headers → Request Headers.",
    "Copy the whole value of [bold]cookie[/], then press [bold]Enter[/] here: FBM reads it from your clipboard "
    "(and clears it). You can also paste it into the hidden prompt.",
)


def _paste_session():
    from fbm.yahoo import session as ws

    for i, step in enumerate(_PASTE_STEPS, 1):
        console.print(f"[bold]{i}.[/] {step.format(url=ws.SESSION_BASE_URL + ws.PROBE_PATH + '/leagues')}")
    typed = _printable(typer.prompt("Cookie header", hide_input=True, default="", show_default=False))
    value = typed or _printable(_clipboard("read"))
    if not typed and value:
        _clipboard("clear")
    if not value:
        _fail("No cookie header received.")
    return ws.from_cookie_header(value)


def _switch_to_yahoo() -> None:
    import subprocess

    from fbm import setup_wizard as wiz
    from fbm.settings import load_settings

    if load_settings().league_source == "yahoo":
        console.print("Next: `fbm yahoo check` (verifies every data feed), then `fbm snapshot`.")
        return
    if not sys.stdin.isatty() or not typer.confirm(
        "Switch FBM from the demo league to your Yahoo league now?", default=True
    ):
        console.print(
            "When ready: `fbm setup --league-source yahoo --yes`, then `fbm settings`, `fbm snapshot`, `fbm ids`."
        )
        return
    wiz.write_settings(wiz.SetupOptions(league_source="yahoo"))
    console.print('[green]League source set to "yahoo".[/]')
    for args in (["settings"], ["snapshot"], ["ids"]):
        subprocess.run(wiz.fbm_command(*args), check=False)
    console.print("Run `fbm yahoo check` any time to verify every data feed.")


@yahoo_app.command("login")
def yahoo_login(
    paste: bool = typer.Option(
        False, "--paste", help="Paste the Cookie header from your browser's DevTools instead of opening a browser."
    ),
    browser: str = typer.Option(None, "--browser", help="msedge | chrome | chromium (default: first that starts)."),
    timeout: int = typer.Option(600, "--timeout", help="Seconds to wait for you to finish signing in."),
    league_key: str = typer.Option(None, "--league-key", help="League to manage if you have several."),
) -> None:
    """Sign in to Yahoo once; FBM then reads your league through that web session (read-only, no API approval)."""
    from fbm.yahoo import session as ws

    console.print(
        "FBM will read your league the way Yahoo's own website does, with your Yahoo sign-in. Read-only: FBM "
        "never changes your league. The session is kept in your OS credential store, never in a file or the "
        "AI chat. This route isn't documented by Yahoo and may stop working; the official API (`fbm auth`) "
        "stays available once approved.\n"
    )
    try:
        new = _paste_session() if paste else ws.browser_login(timeout, browser, echo=console.print)
        with YahooClient(session=new) as client:
            leagues = discovery.list_nba_leagues(client)
    except ws.YahooSessionError as err:
        _fail(str(err))
    except YahooAPIError as err:
        tip = "Copy the cookie header again while signed in." if paste else "Try `fbm yahoo login --paste`."
        _fail(f"Yahoo didn't accept the session for reads ({err.status}: {err.description}). Nothing saved. {tip}")
    ws.save(new)
    console.print(
        f"[green]Signed in.[/] Yahoo web session saved to your OS credential store ({len(new.cookies)} cookies)."
    )
    if not leagues:
        _fail("No Yahoo NBA leagues found for this account this season.")
    if league_key or not LEAGUE_CONFIG_PATH.exists():
        _run(_select_league, league_key)
    else:
        cfg = load_league_config()
        console.print(
            f"Managing [bold]{cfg.team_name}[/] in [bold]{cfg.league_name}[/] (`fbm league select` to change)."
        )
    _switch_to_yahoo()


@yahoo_app.command("logout")
def yahoo_logout() -> None:
    """Forget the Yahoo web session (FBM falls back to the official API from `fbm auth`, if set up)."""
    from fbm.yahoo import session as ws

    ws.clear()
    console.print("Yahoo web session removed from your OS credential store.")


@yahoo_app.command("status")
def yahoo_status() -> None:
    """Which Yahoo access route FBM uses (never prints secrets)."""
    from fbm.yahoo import session as ws

    s = ws.load()
    has_api = bool(credentials.get(credentials.YAHOO_REFRESH_TOKEN))
    if s:
        age = s.age_days(datetime.now(UTC))
        console.print(
            f"Active route: [bold]Yahoo web session[/] (saved {age:.1f} days ago via {s.source}; "
            f"{len(s.cookies)} cookies; host {ws.SESSION_HOST}, read-only)."
        )
    elif has_api:
        console.print("Active route: [bold]official Yahoo API[/] (OAuth app from `fbm auth`).")
    else:
        console.print("[yellow]No Yahoo access yet.[/] Run `fbm yahoo login` (web session) or `fbm auth` (API).")
    console.print(f"Official API credentials: {'[green]set[/]' if has_api else 'missing'}")


@yahoo_app.command("check")
def yahoo_check(
    day: str = typer.Option(None, "--date", help="ET game day for the roster read (default: today ET)."),
) -> None:
    """Read every Yahoo feed FBM uses (plus extras) and report which work. Read-only."""
    from fbm.yahoo import coverage
    from fbm.yahoo import session as ws

    if ws.load() is None and not credentials.get(credentials.YAHOO_REFRESH_TOKEN):
        _fail("No Yahoo access configured: run `fbm yahoo login` (web session) or `fbm auth` (official API).")
    try:
        cfg = load_league_config()
        league_key, team_key = cfg.league_key, cfg.team_key
    except NotConfiguredError:
        league_key = team_key = None
    target = parse_date(day) if day else nba_today()
    with YahooClient() as client:
        route = "Yahoo web session" if client.auth_mode == "web_session" else "official API"
        results = coverage.run(client, league_key, team_key, target)
    table = Table(title=f"Yahoo data feeds via {route}")
    for col in ("Feed", "", "Detail", "Used for"):
        table.add_column(col)
    for r in results:
        if r.detail.startswith("skipped"):
            mark = "[dim]skip[/]"
        else:
            mark = "[green]OK[/]" if r.ok else ("[red]FAIL[/]" if r.kind == coverage.CORE else "[yellow]n/a[/]")
        table.add_row(r.name, mark, r.detail, r.used_for)
    console.print(table)
    core = [r for r in results if r.kind == coverage.CORE]
    ok = sum(r.ok for r in core)
    console.print(f"{ok}/{len(core)} feeds FBM uses are readable via the {route}.")
    if route == "official API" and any("403" in r.detail for r in results):
        console.print(
            "[yellow]Your Yahoo API app isn't authorized (Yahoo's access program). Read your league now with "
            "`fbm yahoo login` instead.[/]"
        )
    if ok < len(core):
        raise typer.Exit(code=1)
