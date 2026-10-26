"""Box-score tools: a player's game log and this fantasy week's per-player breakdown for my team and an opponent."""

from __future__ import annotations

from typing import Any

from fbm.analytics import boxscores as box
from fbm.gm import services as svc
from fbm.league.model import FantasyTeam
from fbm.timeutil import nba_today

WEEK_NOTE = (
    "Built from ESPN box scores for each team's CURRENT roster over the fantasy week (ET dates). Players added or "
    "dropped mid-week, and games a player sat on the bench or IL for, are not reflected, so totals can differ from "
    "Yahoo's banked stats (see `vs_yahoo_banked`)."
)
G_NOTE = (
    "g_total/z_total = this single game scored like a season per-game line (volume-weighted FG%/FT%); "
    "0 = average rostered player."
)


def _pool(c: Any) -> tuple[Any, Any]:
    return getattr(c, "pool", None), getattr(c, "cfg", None)


def game_log(player: str, last_n: int = 10, season: int | None = None) -> dict[str, Any]:
    """Recent games played for one player from stored ESPN box scores (newest first)."""
    c = svc.ctx()
    pid = svc._resolve(player)
    conn = svc._conn()
    lines = box.recent_lines(conn, pid, last_n, season)
    pool, cfg = _pool(c)
    name = str(c.projections.loc[pid, "name"]) if pid in c.projections.index else player
    nba = str(c.projections.loc[pid, "team"]) if pid in c.projections.index else ""
    return {
        "player": name,
        "id": pid,
        "nba": nba,
        "season_filter": season,
        "games_returned": len(lines),
        "seasons_included": sorted({line["season"] for line in lines}),
        "games": box.game_log(lines, pool, cfg),
        "avg_returned": box.averages(lines),
        "note": G_NOTE,
    }


def _team_block(
    team: FantasyTeam, lines: dict[str, list], c: Any, banked: dict[str, dict[str, float]], is_me: bool
) -> dict[str, Any]:
    pool, cfg = _pool(c)
    categories = cfg.categories if cfg is not None else box.NINE_CAT
    players = [{"player": p.name, "id": p.player_id, "slot": p.selected, "nba": p.nba_team} for p in team.players]
    out = box.week_box(players, lines, pool, cfg, categories)
    block = {"team": team.name, "team_key": team.team_key, "is_me": is_me, **out}
    if team.team_key in banked:
        block["yahoo_banked"] = banked[team.team_key]
        block["vs_yahoo_banked"] = box.compare_banked(out["team_total"]["totals"], banked[team.team_key], categories)
    return block


def week_box(team: str | None = None) -> dict[str, Any]:
    """This fantasy week's box-score breakdown per player (games, totals, FG/FT makes/attempts, per-category share /
    FG%-FT% effect, G-score sum) for my team and this week's opponent, or for one `team` (name, key, or 'me')."""
    league, c = svc.active_league(), svc.ctx()
    _, ws, we = svc._week_window()
    through = min(we, nba_today())
    if team is None:
        teams = [(league.my_team, True), (svc._default_opponent(league), False)]
    elif team.strip().lower() in ("me", "my", "mine", "my team"):
        teams = [(league.my_team, True)]
    else:
        picked = svc._team(league, team)
        teams = [(picked, picked.team_key == league.my_team_key)]
    conn = svc._conn()
    ids = [p.player_id for t, _ in teams for p in t.players]
    lines = box.week_lines(conn, ids, ws, we)
    banked = dict(getattr(league, "banked", None) or {})
    last = conn.execute(
        "SELECT MAX(et_date) FROM box_lines WHERE et_date BETWEEN ? AND ?", (ws.isoformat(), we.isoformat())
    ).fetchone()[0]
    return {
        "week": league.week,
        "window": [ws.isoformat(), we.isoformat()],
        "through_et": through.isoformat(),
        "last_box_score_date": last,
        "note": WEEK_NOTE,
        "g_note": G_NOTE,
        "teams": [_team_block(t, lines, c, banked, me) for t, me in teams],
    }
