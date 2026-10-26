"""Tools that watch the market: ownership_trends (who is rising/falling, who in the league is adding/dropping) and
fa_radar (which unowned player just turned valuable, ranked, with the drop candidate and how to confirm).

Orchestration over `fbm.gm.services` (league, projections, signals); scoring lives in `fbm.analytics.radar` and the
history maths in `fbm.gm.ownership`. Read-only: nothing here changes the league.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fbm.analytics import radar
from fbm.analytics.week import date_range, teams_playing
from fbm.constants import C
from fbm.gm import ownership
from fbm.league.model import IL_SLOTS, LeagueState

_FA_SCOPES = {"free_agents": "fa", "fa": "fa", "all": "all"}


def _svc():
    from fbm.gm import services

    return services


def _owner_status(pid: str, league: LeagueState, owners: dict[str, Any]) -> str:
    if pid in owners:
        return owners[pid].name
    if pid in league.on_waivers:
        return f"on waivers (clears {league.on_waivers[pid]} ET)"
    return "FA"


def league_moves(days: int | None = None) -> dict[str, Any]:
    """Adds/drops by league teams from the stored transaction log (Yahoo league only)."""
    svc = _svc()
    days = int(days if days is not None else C("ownership.moves_days"))
    league, conn = svc.active_league(), svc._conn()
    out: dict[str, Any] = {"days": days}
    if league.source != "yahoo":
        return {**out, "available": False, "note": "The demo league has no transaction log."}
    from fbm.league import tx_history

    txs = tx_history.load(conn)
    moves = ownership.recent_moves(
        txs,
        {t.team_key: t.name for t in league.teams},
        datetime.now(UTC),
        days,
        ownership.yahoo_to_espn(conn),
    )
    return {
        **out,
        "available": True,
        "transactions_stored": len(txs),
        **ownership.summarize_moves(moves),
        "note": None if txs else "No league transactions stored yet: run `snapshot` (it syncs the log).",
    }


def ownership_trends(window_days: int = 3, top: int = 20, scope: str = "free_agents") -> dict[str, Any]:
    """Biggest percent-owned risers and fallers over `window_days` (FBM's own history) plus in-league adds/drops."""
    svc = _svc()
    if scope not in _FA_SCOPES:
        raise svc.GMError("scope must be free_agents or all")
    if window_days < 1:
        raise svc.GMError("window_days must be at least 1")
    league, c, conn = svc.active_league(), svc.ctx(), svc._conn()
    owners = svc._owners(league)
    history = ownership.load_history(conn)
    first, last = ownership.snapshot_bounds(history)
    snapshots = len({o.at for obs in history.values() for o in obs})
    pun = svc._strategy_punts(None)
    ros = svc._ros_value(c, pun)
    min_move = float(C("ownership.min_move"))
    rows = []
    for pid, obs in history.items():
        t = ownership.trend(obs, window_days, last)
        if t is None or t.delta is None:
            continue
        rostered = pid in owners
        if _FA_SCOPES[scope] == "fa" and rostered:
            continue
        own = league.ownership.get(pid)
        name = str(c.projections.loc[pid, "name"]) if pid in c.projections.index else pid
        rows.append(
            {
                "player": name,
                "id": pid,
                "nba": str(c.projections.loc[pid, "team"]) if pid in c.projections.index else "",
                "pct_owned": t.current,
                "pct_owned_delta": t.delta,
                "history_span_days": t.span_days,
                "partial_window": t.partial,
                "status": _owner_status(pid, league, owners),
                "ros_value": svc._r(ros.get(pid, 0.0)),
                "yahoo_week_delta": own[1] if own and len(own) > 1 else None,
            }
        )
    risers = sorted((r for r in rows if r["pct_owned_delta"] >= min_move), key=lambda r: -r["pct_owned_delta"])
    fallers = sorted((r for r in rows if r["pct_owned_delta"] <= -min_move), key=lambda r: r["pct_owned_delta"])
    moves = league_moves()
    added = {m["id"]: m["team"] for m in moves.get("adds", []) if m.get("id")}
    for r in [*risers, *fallers]:
        if r["id"] in added:
            r["added_in_league_by"] = added[r["id"]]
    notes = [
        "pct_owned_delta = change in Yahoo percent owned over window_days from FBM's recorded history "
        "(history_span_days says how much history backs it; partial_window = shorter than requested).",
        "Percent owned is only read for available players (top ownership_limit by rank): players who are rostered "
        "keep their last known value, so scope=all shows who was taken after a rise.",
    ]
    if snapshots < 2 or not rows:
        notes.append(
            f"History is thin: {snapshots} snapshot(s)"
            + (f" since {first.isoformat(timespec='minutes')}" if first else "")
            + f". Deltas appear after {C('ownership.min_span_hours')}h+ of history (each snapshot / daily sync "
            "adds a point). Until then use yahoo_week_delta (Yahoo's own weekly change, often 0.0 early in a week)."
        )
    yahoo_movers = sorted(
        (
            {
                "player": str(c.projections.loc[pid, "name"]) if pid in c.projections.index else pid,
                "id": pid,
                "pct_owned": o[0],
                "yahoo_week_delta": o[1],
            }
            for pid, o in league.ownership.items()
            if len(o) > 1 and o[1] and pid in c.projections.index and (_FA_SCOPES[scope] == "all" or pid not in owners)
        ),
        key=lambda r: -abs(r["yahoo_week_delta"]),
    )[:top]
    return {
        "window_days": window_days,
        "scope": scope,
        "as_of": last.isoformat(timespec="minutes") if last else None,
        "history": {
            "snapshots": snapshots,
            "since": first.isoformat(timespec="minutes") if first else None,
            "players_tracked": len(history),
        },
        "risers": risers[:top],
        "fallers": fallers[:top],
        "yahoo_week_movers": yahoo_movers,
        "league_moves": moves,
        "notes": notes,
    }


def _games_left(start, end) -> dict[str, int]:
    playing = teams_playing(_svc()._conn(), start, end)
    counts: dict[str, int] = {}
    for d in date_range(start, end):
        for team in playing.get(d, set()):
            counts[team] = counts.get(team, 0) + 1
    return counts


def fa_radar(top: int = 10, min_confidence: str = "medium", compact: bool = False) -> dict[str, Any]:
    """Scan every free agent (and player on waivers) for a pickup worth confirming: up signals, minutes jump,
    ownership surge, and ROS/week value vs my worst active rostered player. Ranked, with reasons and a drop
    candidate."""
    svc = _svc()
    if min_confidence not in radar.CONF_ORDER:
        raise svc.GMError("min_confidence must be high, medium or low")
    from fbm.gm import signals as signal_feed

    league, c, conn = svc.active_league(), svc.ctx(), svc._conn()
    owners = svc._owners(league)
    pun = svc._strategy_punts(None)
    ros = svc._ros_value(c, pun)
    start, _ws, we = svc._week_window()
    games = _games_left(start, we)

    active = [p for p in league.my_team.players if p.selected not in IL_SLOTS]
    worst = min(active, key=lambda p: float(ros.get(p.player_id, 0.0))) if active else None
    worst_ros = float(ros.get(worst.player_id, 0.0)) if worst else 0.0
    worst_week = worst_ros * games.get(worst.nba_team, 0) / 3.5 if worst else 0.0
    worst_name = (
        str(c.values.loc[worst.player_id, "name"])
        if worst and worst.player_id in c.values.index
        else (worst.name if worst else "")
    )

    short, long_ = int(C("ownership.delta_days")), int(C("ownership.trend_days"))
    trends = ownership.trend_map(conn, (short, long_))
    feed: dict[str, list[dict[str, Any]]] = {}
    for s in signal_feed.active(conn, min_confidence="low"):
        feed.setdefault(s["id"], []).append(s)
    teamless = svc._teamless(c)

    items = []
    scanned = 0
    for pid, row in c.values.iterrows():
        if pid in owners or pid in teamless:
            continue
        scanned += 1
        proj = c.projections.loc[pid] if pid in c.projections.index else None
        trend_val = None if proj is None else proj.get("minutes_trend")
        recent = None if proj is None else proj.get("recent_mpg")
        own = league.ownership.get(pid)
        t_long = trends.get(pid, {}).get(long_)
        surge = t_long.delta if t_long is not None and t_long.delta is not None else (own[1] if own else None)
        gain = float(ros.get(pid, 0.0)) - worst_ros
        result = radar.score(
            radar.RadarInput(
                signals=feed.get(pid, []),
                recent_mpg=None if recent is None or recent != recent else float(recent),
                minutes_trend=None if trend_val is None or trend_val != trend_val else float(trend_val),
                owned_delta=surge,
                owned_pct=own[0] if own else None,
                ros_gain=gain,
                worst_name=worst_name,
            )
        )
        if result.score <= 0 or not radar.meets(result.confidence, min_confidence):
            continue
        items.append((result, pid, row, gain, surge, own, t_long))
    items.sort(key=lambda x: (-x[0].score, -x[3]))

    out_items = []
    for result, pid, row, gain, surge, own, t_long in items[:top]:
        status = _owner_status(pid, league, owners)
        team = str(row["team"])
        week = float(ros.get(pid, 0.0)) * games.get(team, 0) / 3.5
        name = str(row["name"])
        drop = worst_name or None
        item: dict[str, Any] = {
            "rank": len(out_items) + 1,
            "player": name,
            "id": pid,
            "nba": team,
            "pos": str(row["position"]),
            "confidence": result.confidence,
            "score": result.score,
            "reasons": result.reasons,
            "cautions": result.cautions,
            "owner_status": status,
            "on_waivers": pid in league.on_waivers,
            "pct_owned": own[0] if own else None,
            "pct_owned_trend_delta": t_long.delta if t_long is not None else None,
            "ownership_surge_basis": surge,
            "ros_value": svc._r(ros.get(pid, 0.0)),
            "ros_gain_vs_worst": svc._r(gain),
            "week_value": svc._r(week),
            "week_gain_vs_worst": svc._r(week - worst_week),
            "beats_worst_rostered": gain >= float(C("radar.min_ros_gain")),
            "minutes": svc._role(c, pid),
            "suggested_drop": {"player": drop, "id": worst.player_id, "ros_value": svc._r(worst_ros)}
            if worst
            else None,
            "confirm_with": (
                f"waiver_decision(player='{name}', drop='{drop}')"
                if pid in league.on_waivers
                else f"evaluate_add_drop(add='{name}', drop='{drop}')"
            ),
        }
        if not compact:
            item["injury"] = svc._injury(pid)
        out_items.append(item)

    notes = ["Radar = candidates to confirm, not recommendations: run the confirm_with tool before any proposal."]
    history = ownership.load_history(conn)
    first, last = ownership.snapshot_bounds(history)
    if first is None or first == last:
        notes.append(
            "Ownership history is thin (first snapshot): the ownership reason uses Yahoo's weekly delta until FBM "
            "has recorded enough snapshots."
        )
    if worst is None:
        notes.append("No active rostered player to compare against: value reasons skipped.")
    return {
        "min_confidence": min_confidence,
        "scanned_free_agents": scanned,
        "count": len(items),
        "my_worst_rostered": {"player": worst_name, "id": worst.player_id, "ros_value": svc._r(worst_ros)}
        if worst
        else None,
        "players": out_items,
        "notes": notes,
    }


def radar_summary(top: int = 5) -> list[dict[str, Any]]:
    """Cheap digest for the daily sync's early_signals."""
    res = fa_radar(top=top, min_confidence="medium", compact=True)
    return [
        {k: p[k] for k in ("player", "nba", "confidence", "owner_status", "ros_gain_vs_worst", "reasons")}
        for p in res["players"]
    ]
