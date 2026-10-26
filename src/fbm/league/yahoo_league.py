"""The real Yahoo league as a LeagueState (read-only), cached as a snapshot.

`refresh()` reads settings, teams, every roster (ET game day), the scoreboard (this week's opponent and the stats each
team has banked so far), standings and the waiver list; maps Yahoo players to ESPN ids; and saves
data/yahoo_league.json. `load_or_refresh()` serves the snapshot while it is fresh. Writes are out of scope: roster
changes come back as manual steps for the owner, and the next snapshot shows what actually happened.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, date, datetime, timedelta
from typing import Any

from fbm.analytics.categories import from_league_settings
from fbm.config import DATA_DIR, RAW_DIR, LeagueConfig
from fbm.league.demo import load_league, save_league
from fbm.league.model import DEFAULT_SLOTS, FantasyTeam, LeagueState, RosterPlayer, SlotSpec
from fbm.yahoo import roster as yahoo_roster
from fbm.yahoo.ids import IdMapper, Match, espn_team
from fbm.yahoo.league_reads import (
    Matchup,
    StandingRow,
    YahooPlayer,
    YahooTeam,
    banked_stats,
    fetch_players,
    fetch_scoreboard,
    fetch_standings,
    fetch_teams,
    parse_players,
)

SNAPSHOT_PATH = DATA_DIR / "yahoo_league.json"
SETTINGS_SUMMARY_PATH = RAW_DIR / "league_settings_summary.json"
RETRY_AFTER = timedelta(minutes=10)
_last_failure: dict[str, Any] = {"at": None, "error": ""}


def norm_position(pos: str) -> str:
    return (pos or "").strip().upper()


def slots_from_summary(summary: dict[str, Any]) -> tuple[SlotSpec, ...]:
    slots = tuple(
        SlotSpec(norm_position(r["position"]), int(r["count"]), bool(r["is_starting"]))
        for r in summary.get("roster_slots", [])
        if r.get("position") and int(r.get("count") or 0) > 0
    )
    return slots or DEFAULT_SLOTS


def yahoo_player_id(player_key: str) -> str:
    return (player_key or "").rsplit(".p.", 1)[-1]


def to_roster_player(rp: yahoo_roster.RosterPlayer, match: Match) -> RosterPlayer:
    eligible = tuple(dict.fromkeys([*(norm_position(e) for e in rp.eligible_positions if e), "UTIL"]))
    return RosterPlayer(
        player_id=match.espn_id or f"yahoo:{match.yahoo_id}",
        name=rp.name,
        nba_team=espn_team(rp.nba_team),
        eligible=eligible,
        selected=norm_position(rp.selected_position) or "BN",
        status=rp.status,
        yahoo_key=rp.player_key,
    )


def assemble(
    cfg: LeagueConfig,
    summary: dict[str, Any],
    teams: list[YahooTeam],
    rosters: dict[str, yahoo_roster.Roster],
    matchups: list[Matchup],
    standings: list[StandingRow],
    waivers: list[YahooPlayer],
    mapper: IdMapper,
    fetched_at: datetime,
    available: list[YahooPlayer] | None = None,
) -> tuple[LeagueState, list[Match]]:
    """Pure: Yahoo reads -> LeagueState (+ every player match, for the mapping cache)."""
    matches: dict[str, Match] = {}

    def mapped(yahoo_id: str, name: str, team: str) -> Match:
        if yahoo_id not in matches:
            matches[yahoo_id] = mapper.match(yahoo_id, name, team)
        return matches[yahoo_id]

    fantasy_teams = []
    for t in teams:
        roster = rosters.get(t.team_key)
        players = [
            to_roster_player(rp, mapped(yahoo_player_id(rp.player_key), rp.name, rp.nba_team))
            for rp in (roster.players if roster else ())
        ]
        fantasy_teams.append(FantasyTeam(t.team_key, t.name, t.manager, players))

    my_key = cfg.team_key or next((t.team_key for t in teams if t.is_mine), "")
    mine = next((m for m in matchups if m.team(my_key)), None)
    opponent = mine.opponent_of(my_key) if mine else None
    banked = {
        mt.team_key: banked_stats(mt.stats) for m in matchups if m.status != "preevent" for mt in m.teams if mt.stats
    }
    on_waivers = {}
    for w in waivers:
        if w.waiver_date:
            on_waivers[mapped(w.player_id, w.name, w.nba_team).espn_id or f"yahoo:{w.player_id}"] = (
                w.waiver_date.isoformat()
            )
    ownership: dict[str, list[float | None]] = {}
    for p in [*waivers, *(available or [])]:
        if p.percent_owned is not None:
            pid = mapped(p.player_id, p.name, p.nba_team).espn_id or f"yahoo:{p.player_id}"
            ownership[pid] = [p.percent_owned, p.percent_owned_delta]
    settings = summary.get("settings") or {}
    max_adds = settings.get("max_weekly_adds")
    league = LeagueState(
        league_key=cfg.league_key,
        name=str(summary.get("name") or cfg.league_name),
        season=int(summary.get("season") or cfg.season or 0),
        my_team_key=my_key,
        teams=fantasy_teams,
        slots=slots_from_summary(summary),
        categories=from_league_settings(summary),
        week=int(summary.get("current_week") or (mine.week if mine and mine.week else 1)),
        week_start=mine.week_start if mine else None,
        week_end=mine.week_end if mine else None,
        max_weekly_adds=int(max_adds) if max_adds not in (None, "") else None,
        source="yahoo",
        waiver_priority={t.team_key: t.waiver_priority for t in teams if t.waiver_priority},
        opponent_key=opponent.team_key if opponent else None,
        banked=banked,
        standings=[asdict(r) for r in standings],
        on_waivers=on_waivers,
        adds_used={t.team_key: t.week_adds for t in teams if t.week_adds is not None},
        fetched_at=fetched_at.astimezone(UTC).isoformat(timespec="seconds"),
        matchups=[matchup_dict(m) for m in matchups],
        ownership=ownership,
    )
    return league, list(matches.values())


def matchup_dict(m: Matchup) -> dict[str, Any]:
    return {
        "week": m.week,
        "status": m.status,
        "is_playoffs": m.is_playoffs,
        "teams": [
            {"team_key": t.team_key, "name": t.name, "stats": dict(t.stats), "points": t.points} for t in m.teams
        ],
        "stat_winners": dict(m.stat_winners),
    }


def fetch_ownership(client, league_key: str, limit: int, page: int = 25) -> list[YahooPlayer]:
    """Available players by actual rank, with percent owned (and its weekly delta)."""
    out: list[YahooPlayer] = []
    start = 0
    while start < limit:
        path = f"league/{league_key}/players;status=A;sort=AR;start={start};count={page}/percent_owned"
        batch = parse_players(client.get(path))
        out.extend(batch)
        if len(batch) < page:
            break
        start += page
    return out[:limit]


def build_snapshot(client, cfg: LeagueConfig, mapper: IdMapper, day: date, waiver_limit: int = 100):
    """Network reads (read-only) -> (LeagueState, settings summary, player matches)."""
    from fbm.yahoo.settings import fetch_settings, summarize

    summary = summarize(fetch_settings(client, cfg.league_key))
    teams = fetch_teams(client, cfg.league_key)
    rosters = {t.team_key: yahoo_roster.get_roster(client, t.team_key, day) for t in teams}
    matchups = fetch_scoreboard(client, cfg.league_key)
    standings = fetch_standings(client, cfg.league_key)
    waivers = fetch_players(client, cfg.league_key, "W", waiver_limit)
    from fbm.constants import C

    available = fetch_ownership(client, cfg.league_key, int(C("yahoo.ownership_limit")))
    league, matches = assemble(
        cfg, summary, teams, rosters, matchups, standings, waivers, mapper, datetime.now(UTC), available
    )
    return league, summary, matches


def refresh(day: date | None = None) -> tuple[LeagueState, list[Match]]:
    """Read the league from Yahoo and save the snapshot. Returns (league, unmatched players)."""
    from fbm.config import load_league_config
    from fbm.store import db
    from fbm.timeutil import nba_today
    from fbm.yahoo.client import YahooClient
    from fbm.yahoo.ids import espn_players_from_db, load_overrides, save_matches

    cfg = load_league_config()
    conn = db.connect()
    mapper = IdMapper(espn_players_from_db(conn), load_overrides())
    with YahooClient() as client:
        league, summary, matches = build_snapshot(client, cfg, mapper, day or nba_today())
    save_matches(conn, matches)
    save_league(league, SNAPSHOT_PATH)
    try:  # percent-owned history for trends (additive; never fail a snapshot over it)
        from fbm.gm import ownership

        ownership.record(conn, league)
    except Exception:
        pass
    SETTINGS_SUMMARY_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_SUMMARY_PATH.write_text(json.dumps(summary, indent=1), encoding="utf-8")
    _last_failure.update(at=None, error="")
    return league, [m for m in matches if not m.espn_id]


def snapshot_age(league: LeagueState, now: datetime | None = None) -> timedelta | None:
    if not league.fetched_at:
        return None
    return (now or datetime.now(UTC)) - datetime.fromisoformat(league.fetched_at)


def last_refresh_error() -> str:
    return str(_last_failure["error"] or "")


def load_or_refresh(max_age_minutes: float) -> LeagueState:
    """Fresh snapshot if possible; a stale one (with last_refresh_error set) if Yahoo can't be read right now."""
    now = datetime.now(UTC)
    cached = load_league(SNAPSHOT_PATH) if SNAPSHOT_PATH.exists() else None
    if cached is not None:
        age = snapshot_age(cached, now)
        if age is not None and age <= timedelta(minutes=max_age_minutes):
            return cached
    failed_at = _last_failure["at"]
    if cached is not None and failed_at is not None and now - failed_at < RETRY_AFTER:
        return cached
    try:
        league, _ = refresh()
        return league
    except Exception as err:  # network/API failure: fall back to the last snapshot
        _last_failure.update(at=now, error=f"{type(err).__name__}: {err}")
        if cached is not None:
            return cached
        raise
