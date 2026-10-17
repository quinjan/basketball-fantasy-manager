"""`fbm yahoo check`: run every Yahoo read FBM uses (plus a few extra resources) and report what works.

Read-only. Works with either access route (web session or official API), so it doubles as the proof that a route
covers everything the engine needs. Details hold counts and league labels only, never credentials.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any

from fbm.yahoo import discovery
from fbm.yahoo.xmlparse import as_list, text

CORE, EXTRA = "core", "extra"


@dataclass(frozen=True)
class CheckResult:
    name: str
    kind: str  # core (FBM uses it) | extra (available, not used yet)
    ok: bool
    detail: str
    used_for: str


@dataclass(frozen=True)
class Context:
    league_key: str | None
    team_key: str | None
    day: date


def _leagues(client: Any, ctx: Context) -> str:
    leagues = discovery.list_nba_leagues(client)
    names = ", ".join(f"{lg['name']} ({lg['league_key']})" for lg in leagues)
    return f"{len(leagues)} NBA league(s) this season: {names}" if leagues else "0 NBA leagues this season"


def _settings(client: Any, ctx: Context) -> str:
    from fbm.yahoo.settings import fetch_settings, summarize

    s = summarize(fetch_settings(client, ctx.league_key))
    cats = ", ".join(c["display_name"] for c in s["scoring_categories"])
    spots = sum(r["count"] for r in s["roster_slots"])
    return (
        f"{s.get('scoring_type_label')}: {len(s['scoring_categories'])} cats ({cats}); {spots} roster spots; "
        f"max weekly adds {s['settings'].get('max_weekly_adds') or '-'}; week {s.get('current_week', '-')}"
    )


def _teams(client: Any, ctx: Context) -> str:
    from fbm.yahoo.league_reads import fetch_teams

    teams = fetch_teams(client, ctx.league_key)
    mine = next((t for t in teams if t.team_key == ctx.team_key or t.is_mine), None)
    tail = (
        f"; yours: waiver priority {mine.waiver_priority or '-'}, adds this week "
        f"{'-' if mine.week_adds is None else mine.week_adds}, {mine.moves} moves"
        if mine
        else "; your team not flagged"
    )
    return f"{len(teams)} teams{tail}"


def _roster(client: Any, ctx: Context) -> str:
    from fbm.yahoo.roster import get_roster

    r = get_roster(client, ctx.team_key, ctx.day)
    hurt = sum(1 for p in r.players if p.status)
    return f"{len(r.players)} players on {ctx.day} ({hurt} with an injury status); lineup editable: {r.is_editable}"


def _scoreboard(client: Any, ctx: Context) -> str:
    from fbm.yahoo.league_reads import fetch_scoreboard

    matchups = fetch_scoreboard(client, ctx.league_key)
    mine = next((m for m in matchups if m.team(ctx.team_key or "")), None)
    week = mine.week if mine else (matchups[0].week if matchups else "-")
    stats = len(mine.team(ctx.team_key or "").stats) if mine else 0
    status = mine.status if mine else "-"
    return f"{len(matchups)} matchups in week {week} ({status}); {stats} stat values for your team"


def _standings(client: Any, ctx: Context) -> str:
    from fbm.yahoo.league_reads import fetch_standings

    rows = fetch_standings(client, ctx.league_key)
    mine = next((r for r in rows if r.team_key == ctx.team_key), None)
    if not mine:
        return f"{len(rows)} teams"
    return f"{len(rows)} teams; you: rank {mine.rank or '-'}, {mine.wins}-{mine.losses}-{mine.ties}"


def _waivers(client: Any, ctx: Context) -> str:
    from fbm.yahoo.league_reads import fetch_players

    players = fetch_players(client, ctx.league_key, "W", 25)
    dated = sum(1 for p in players if p.waiver_date)
    return f"{len(players)} on waivers ({dated} with a clear date)"


def _free_agents(client: Any, ctx: Context) -> str:
    from fbm.league.yahoo_league import fetch_ownership

    players = fetch_ownership(client, ctx.league_key, 25)
    owned = sum(1 for p in players if p.percent_owned is not None)
    return f"{len(players)} top available players ({owned} with % owned and weekly change)"


def _transactions(client: Any, ctx: Context) -> str:
    from fbm.yahoo.league_reads import fetch_transactions

    txs = fetch_transactions(client, ctx.league_key, count=10)
    return f"{len(txs)} most recent adds/drops/trades" + (f" (latest {txs[0].type})" if txs else "")


def _pending(client: Any, ctx: Context) -> str:
    from fbm.yahoo.league_reads import fetch_pending

    txs = fetch_pending(client, ctx.league_key, ctx.team_key)
    trades = sum(1 for t in txs if "trade" in t.type)
    return f"{trades} pending trade(s), {len(txs) - trades} pending waiver claim(s)"


def _draft(client: Any, ctx: Context) -> str:
    from fbm.yahoo.draft import fetch_draftresults

    picks = fetch_draftresults(client, ctx.league_key)
    return f"{len(picks)} draft picks recorded"


def _adp(client: Any, ctx: Context) -> str:
    from fbm.yahoo.draft import fetch_adp

    return f"{len(fetch_adp(client, ctx.league_key, 25))} players with Yahoo ADP (first page)"


def _team_stats(client: Any, ctx: Context) -> str:
    fc = client.get(f"league/{ctx.league_key}/teams/stats;type=season")
    teams = as_list(((fc.get("league") or {}).get("teams") or {}).get("team"))
    with_stats = sum(1 for t in teams if ((t.get("team_stats") or {}).get("stats") or {}).get("stat"))
    return f"season category totals for {with_stats}/{len(teams)} teams"


def _matchup_history(client: Any, ctx: Context) -> str:
    fc = client.get(f"team/{ctx.team_key}/matchups")
    weeks = as_list(((fc.get("team") or {}).get("matchups") or {}).get("matchup"))
    done = sum(1 for m in weeks if text(m.get("status")) == "postevent")
    return f"{len(weeks)} scheduled weeks ({done} completed) with opponents and category results"


def _player_stats(client: Any, ctx: Context) -> str:
    fc = client.get(f"team/{ctx.team_key}/roster/players/stats;type=season")
    players = as_list((((fc.get("team") or {}).get("roster") or {}).get("players") or {}).get("player"))
    with_stats = sum(1 for p in players if ((p.get("player_stats") or {}).get("stats") or {}).get("stat"))
    return f"Yahoo season stats for {with_stats}/{len(players)} of your players"


Check = tuple[str, str, str, bool, Callable[[Any, Context], str]]  # name, kind, used_for, needs_team, fn
CHECKS: tuple[Check, ...] = (
    ("Your leagues", CORE, "league discovery (`fbm league select`)", False, _leagues),
    ("League settings", CORE, "categories, roster slots, add limits", False, _settings),
    ("Teams", CORE, "managers, waiver priority, weekly adds", False, _teams),
    ("Your roster (today)", CORE, "lineup, IL, add/drop", True, _roster),
    ("Scoreboard", CORE, "matchup odds, banked weekly stats", True, _scoreboard),
    ("Standings", CORE, "standings, playoff outlook", False, _standings),
    ("Waiver list", CORE, "waiver claims, clear dates", False, _waivers),
    ("Free agents + % owned", CORE, "pickups, streaming, buy/sell", False, _free_agents),
    ("Transactions", CORE, "league intel, manager profiles", False, _transactions),
    ("Pending trades & claims", CORE, "`fbm offers`", True, _pending),
    ("Draft results", CORE, "draft sync, retro", False, _draft),
    ("Yahoo ADP", CORE, "draft board", False, _adp),
    ("Team season stats", EXTRA, "not used yet: true league category profile", False, _team_stats),
    ("Matchup history", EXTRA, "not used yet: full-season H2H results", True, _matchup_history),
    ("Player season stats (Yahoo)", EXTRA, "not used yet: Yahoo-scored player stats", True, _player_stats),
)


def run(client: Any, league_key: str | None, team_key: str | None, day: date) -> list[CheckResult]:
    ctx = Context(league_key, team_key, day)
    out = []
    for name, kind, used_for, needs_team, fn in CHECKS:
        if fn is not _leagues and not league_key:
            out.append(CheckResult(name, kind, False, "skipped: no league selected (`fbm league select`)", used_for))
            continue
        if needs_team and not team_key:
            out.append(CheckResult(name, kind, False, "skipped: no team in config/league.toml", used_for))
            continue
        try:
            out.append(CheckResult(name, kind, True, fn(client, ctx), used_for))
        except Exception as err:
            out.append(CheckResult(name, kind, False, f"{type(err).__name__}: {str(err)[:200]}", used_for))
    return out
