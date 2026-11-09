"""Any-team roster view and next-week lookahead (the `team_roster` and `next_week` tools).

The pure helpers (team matching, roster summary, week bounds, game counts) take plain data and are unit-tested; the two
tool functions wire them to the league snapshot, the NBA schedule and the simulator through `fbm.gm.services`.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date, timedelta
from typing import Any

from fbm.analytics.week import date_range, fantasy_week, plan_team_week, teams_playing
from fbm.league.model import IL_SLOTS, OUT_STATUSES, FantasyTeam, LeagueState
from fbm.league.weeks import week_info_from_matchups

TOP_FA = 10
STREAM_GAMES_BASELINE = 3.5  # same lens as services.free_agents (week_value)


def _svc():
    from fbm.gm import services

    return services


# ------------------------------------------------------------------ pure helpers


def match_team(league: LeagueState, query: str) -> FantasyTeam:
    """Team by key, name or manager (case-insensitive): exact match first, then a unique substring."""
    q = query.strip().casefold()
    if not q:
        raise _svc().GMError("Empty team name.")
    for field in (lambda t: t.team_key, lambda t: t.name, lambda t: t.manager):
        exact = [t for t in league.teams if field(t).casefold() == q]
        if len(exact) == 1:
            return exact[0]
    partial = [
        t for t in league.teams if q in t.name.casefold() or q in t.manager.casefold() or q in t.team_key.casefold()
    ]
    if len(partial) == 1:
        return partial[0]
    options = ", ".join(f"{t.name} ({t.team_key}, {t.manager or '?'})" for t in (partial or league.teams))
    raise _svc().GMError(
        f"Team '{query}' matched {len(partial)} teams; use a team key, name or manager. Candidates: {options}"
    )


def is_out(row: Mapping[str, Any]) -> bool:
    injury = row.get("injury") or {}
    return str(row.get("status") or "").upper() in OUT_STATUSES or str(injury.get("status") or "").lower() == "out"


def team_summary(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Team-level roll-up of `my_roster`-style rows: remaining games, injured/out players, category strengths.

    Players in IL slots don't count toward games or category totals (they can't score)."""
    rows = list(rows)
    active = [r for r in rows if r.get("slot") not in IL_SLOTS]
    totals: dict[str, float] = {}
    for r in active:
        for cat, g in (r.get("g_by_cat") or {}).items():
            totals[cat] = round(totals.get(cat, 0.0) + g, 2)
    ranked = sorted(totals, key=lambda k: -totals[k])
    injured = [
        {
            "player": r["player"],
            "slot": r.get("slot"),
            "status": r.get("status") or (r.get("injury") or {}).get("status"),
            "out": is_out(r),
            "detail": (r.get("injury") or {}).get("short_comment"),
            "return_date": (r.get("injury") or {}).get("return_date"),
            "games_left_this_week": r.get("games_left_this_week"),
        }
        for r in rows
        if r.get("status") or r.get("injury") or r.get("slot") in IL_SLOTS
    ]
    return {
        "games_remaining_this_week": sum(int(r.get("games_left_this_week") or 0) for r in active),
        "games_remaining_note": "sum over non-IL players (bench included); the lineup can start only some of them",
        "players_with_game_today": [r["player"] for r in active if r.get("game_today")],
        "injured": injured,
        "out_count": sum(1 for i in injured if i["out"]),
        "category_strengths": {
            "g_by_cat": totals,
            "strongest": ranked[:3],
            "weakest": ranked[::-1][:3] if len(ranked) > 3 else [],
            "note": "sum of per-player G-scores over non-IL players (higher = stronger; punts not applied)",
        },
    }


def next_week_bounds(
    current_end: date, yahoo_start: date | None = None, yahoo_end: date | None = None
) -> tuple[date, date]:
    """Next fantasy week's ET dates: Yahoo's own bounds for that week if its schedule gave them, else the
    Monday-Sunday week after `current_end`. Yahoo's bounds are never replaced by a guess: if they don't start after
    `current_end` the caller's idea of the current week is wrong, and that is an error, not a silent fallback."""
    if yahoo_start and yahoo_end:
        if yahoo_end < yahoo_start or yahoo_start <= current_end:
            raise ValueError(
                f"Yahoo's next-week bounds {yahoo_start}..{yahoo_end} don't follow the current week's end {current_end}"
            )
        return yahoo_start, yahoo_end
    start = current_end + timedelta(days=1)
    return start, fantasy_week(start)[1]


def games_by_team(playing: Mapping[date, set[str]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for teams in playing.values():
        for t in teams:
            counts[t] = counts.get(t, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


def best_streams(
    candidates: Iterable[Mapping[str, Any]], games: Mapping[str, int], top: int = TOP_FA
) -> list[dict[str, Any]]:
    """Free agents ranked by next-week streaming value: value x games / baseline games per week."""
    out = []
    for p in candidates:
        n = games.get(str(p["nba"]), 0)
        out.append(
            {
                "player": p["player"],
                "id": p["id"],
                "nba": p["nba"],
                "pos": p.get("pos"),
                "value": p["value"],
                "games_next_week": n,
                "next_week_value": round(float(p["value"]) * n / STREAM_GAMES_BASELINE, 2),
                "status": p.get("status", ""),
                "pct_owned": p.get("pct_owned"),
            }
        )
    out.sort(key=lambda r: (-r["next_week_value"], -r["games_next_week"]))
    return out[:top]


# ------------------------------------------------------------------ tools


def team_roster(team: str | None = None, day: str | None = None, punts: str | None = None) -> dict[str, Any]:
    svc = _svc()
    league = svc.active_league()
    t = match_team(league, team) if team else svc._default_opponent(league)
    view = svc._roster_view(t, day, punts)
    return {
        "team": t.name,
        "team_key": t.team_key,
        "manager": t.manager,
        "is_mine": t.team_key == league.my_team_key,
        "is_this_week_opponent": t.team_key == league.opponent_key,
        "date_et": view["date_et"],
        "punts_applied": view["punts_applied"],
        "summary": team_summary(view["players"]),
        "players": view["players"],
    }


def _next_opponent(league: LeagueState, opponent: str | None) -> tuple[FantasyTeam | None, dict[str, Any]]:
    """(next opponent, {week, start, end, source/note}) from the owner's choice or the Yahoo schedule. The Yahoo
    week bounds come back even when the caller picks the opponent, so the window is Yahoo's either way."""
    svc = _svc()
    caller = match_team(league, opponent) if opponent else None
    if league.source != "yahoo":
        if caller:
            return caller, {"source": "caller"}
        return None, {"note": "demo league has no matchup schedule; pass opponent=<team> to preview one"}
    week = league.week + 1
    try:
        matchups = svc._yahoo_week_matchups(league, week)
    except Exception as err:
        note = f"Yahoo schedule for week {week} unavailable: {type(err).__name__}: {err}"
        return caller, ({"source": "caller", "note": note} if caller else {"note": note})
    sched = week_info_from_matchups(week, matchups, league.my_team_key)
    info: dict[str, Any] = {"week": week, "start": sched.start, "end": sched.end} if sched else {}
    if caller:
        return caller, {**info, "source": "caller"}
    opp_key = sched.opponent_key if sched else None
    if opp_key is None or not any(x.team_key == opp_key for x in league.teams):
        return None, {**info, "note": f"Yahoo has no week-{week} matchup for this team (bye, or the season ends)"}
    return league.team(opp_key), {**info, "source": "Yahoo schedule"}


def _team_week(
    team: FantasyTeam,
    days: list[date],
    playing: Mapping[date, set[str]],
    league: LeagueState,
    value: Mapping[str, float],
    return_dates: Mapping[str, date] | None = None,
) -> dict[str, Any]:
    _, plans = plan_team_week(team, days, playing, league.starting_slots(), value, return_dates)
    games = {p.player_id: sum(1 for d in days if p.nba_team in playing.get(d, set())) for p in team.players}
    players = [
        {
            "player": p.name,
            "id": p.player_id,
            "nba": p.nba_team,
            "slot": p.selected,
            "status": p.status,
            "games_next_week": games[p.player_id],
        }
        for p in team.players
    ]
    players.sort(key=lambda r: -r["games_next_week"])
    return {
        "team": team.name,
        "team_key": team.team_key,
        "player_games": sum(games[p.player_id] for p in team.players if p.selected not in IL_SLOTS),
        "games_started_optimal": sum(len(p.started) for p in plans),
        "note": "player_games = non-IL players' NBA games; games_started_optimal = games the best daily lineups start",
        "players": players,
    }


def next_week(opponent: str | None = None, n: int = 4000) -> dict[str, Any]:
    svc = _svc()
    league = svc.active_league()
    current_end = svc._week_window()[2]
    opp, info = _next_opponent(league, opponent)
    window_note = None
    try:
        start, end = next_week_bounds(current_end, info.get("start"), info.get("end"))
    except ValueError as err:
        window_note = f"{err}; using the Monday-Sunday calendar week instead."
        start, end = next_week_bounds(current_end)
    days = date_range(start, end)
    playing = teams_playing(svc._conn(), start, end)
    counts = games_by_team(playing)
    out: dict[str, Any] = {
        "week": league.week + 1,
        "start_et": start.isoformat(),
        "end_et": end.isoformat(),
        "days": len(days),
        "this_week": league.week,
        "opponent": opp.name if opp else None,
        "opponent_key": opp.team_key if opp else None,
        "opponent_source": info.get("source"),
        "opponent_note": info.get("note"),
        "nba_games_by_team": counts,
        "nba_game_days": {d.isoformat(): len(teams) // 2 for d, teams in playing.items()},
    }
    notes = [n for n in (league.rollover_note, window_note) if n]
    if notes:
        out["window_note"] = " ".join(notes)
    if not counts:
        out["note"] = "No NBA games are scheduled in that window (schedule not published yet, or the season is over)."
        return out
    c = svc.ctx()
    value = svc._value(c, svc._strategy_punts(None)).to_dict()
    back = c.return_dates(svc._conn())
    out["me"] = _team_week(league.my_team, days, playing, league, value, back)
    if opp:
        out["opponent_roster"] = _team_week(opp, days, playing, league, value, back)
        out["simulation"] = _simulate(league, opp, start, end, n)
    out["free_agents_most_games"] = _fa_lookahead(counts)
    return out


def _simulate(league: LeagueState, opp: FantasyTeam, start: date, end: date, n: int) -> dict[str, Any]:
    svc = _svc()
    try:
        odds, mw, ow, day0, we, *_ = svc._matchup(league, league.my_team, opp, start, n)
    except Exception as err:
        return {"error": f"{type(err).__name__}: {err}"}
    return {
        "window_et": f"{day0}..{we}",
        "window_matches_week": (day0, we) == (start, end),
        "games_started": {"me": len(mw.player_games), "opp": len(ow.player_games)},
        "categories": [
            {
                "cat": o.category.label,
                "me_mean": svc._r(o.mean_a, 3 if o.category.kind == "pct" else 1),
                "opp_mean": svc._r(o.mean_b, 3 if o.category.kind == "pct" else 1),
                "p_win": svc._r(o.p_win),
                "p_tie": svc._r(o.p_tie),
                "p_loss": svc._r(o.p_loss),
            }
            for o in odds.categories
        ],
        "expected_cats_won": svc._r(odds.expected_score),
        "p_win_majority": svc._r(odds.p_majority),
        "swing_categories": [o.category.label for o in odds.swing()],
        "method": f"{n} Monte Carlo weeks vs the current opponent roster as-is (no streaming/lineup changes assumed)",
    }


def _fa_lookahead(counts: Mapping[str, int]) -> dict[str, Any]:
    svc = _svc()
    pool = svc.rankings(top=80, available_only=True)["players"]
    return {
        "players": best_streams(pool, counts),
        "note": "top free agents (by ROS value, pool of 80) ranked by value x next-week games / 3.5",
    }
