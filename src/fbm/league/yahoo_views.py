"""Pure views over Yahoo league reads: standings, the live scoreboard, transactions and pending offers.

Inputs are the snapshot (LeagueState) and parsed Yahoo transactions; outputs are JSON-friendly dicts. Every number
here is copied or derived (makes/attempts) from Yahoo's own figures.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fbm.analytics.categories import Category
from fbm.league.model import LeagueState
from fbm.yahoo.league_reads import Transaction, banked_stats

PlayerMapper = Callable[[str, str, str], str | None]  # (yahoo_id, name, nba_team) -> ESPN id or None


def standings_rows(league: LeagueState) -> list[dict[str, Any]]:
    return [{**row, "mine": row.get("team_key") == league.my_team_key} for row in league.standings]


def category_value(cat: Category, stats: dict[str, str]) -> float | None:
    """A team's weekly value in one category from Yahoo raw stats (percentages from makes/attempts)."""
    cols = banked_stats(stats)
    if cat.kind == "pct":
        att = cols.get(cat.att)
        return round(cols.get(cat.made, 0.0) / att, 4) if att else None
    return cols.get(cat.stat)


def _leader(cat: Category, a: tuple[str, float | None], b: tuple[str, float | None]) -> str | None:
    (ka, va), (kb, vb) = a, b
    if va is None and vb is None or va == vb:
        return None
    if va is None or vb is None:
        return ka if vb is None else kb
    better = va > vb if cat.higher_is_better else va < vb
    return ka if better else kb


def matchup_view(league: LeagueState, m: dict[str, Any]) -> dict[str, Any]:
    teams = m.get("teams") or []
    keys = [t["team_key"] for t in teams]
    if league.my_team_key in keys:
        teams = sorted(teams, key=lambda t: t["team_key"] != league.my_team_key)
        keys = [t["team_key"] for t in teams]
    winners = m.get("stat_winners") or {}
    names = {t["team_key"]: t.get("name", "") for t in teams}
    cats, won = [], dict.fromkeys(keys, 0)
    for cat in league.categories:
        values = {t["team_key"]: category_value(cat, t.get("stats") or {}) for t in teams}
        if cat.yahoo_stat_id in winners:
            lead = winners[cat.yahoo_stat_id]
        elif len(keys) == 2:
            lead = _leader(cat, (keys[0], values[keys[0]]), (keys[1], values[keys[1]]))
        else:
            lead = None
        if lead in won:
            won[lead] += 1
        cats.append(
            {
                "category": cat.label,
                "values": {names[k]: values[k] for k in keys},
                "leader": names.get(lead) if lead else None,
            }
        )
    return {
        "teams": [names[k] for k in keys],
        "team_keys": keys,
        "mine": league.my_team_key in keys,
        "status": m.get("status", ""),
        "week": m.get("week"),
        "is_playoffs": bool(m.get("is_playoffs")),
        "categories": cats,
        "categories_won": {names[k]: won[k] for k in keys},
        "yahoo_points": {t["name"]: t.get("points") for t in teams},
    }


def scoreboard_view(league: LeagueState) -> list[dict[str, Any]]:
    views = [matchup_view(league, m) for m in league.matchups]
    return sorted(views, key=lambda v: not v["mine"])


def _team_name(league: LeagueState, key: str) -> str | None:
    return next((t.name for t in league.teams if t.team_key == key), key or None)


def transaction_view(league: LeagueState, tx: Transaction, mapper: PlayerMapper) -> dict[str, Any]:
    return {
        "key": tx.key,
        "type": tx.type,
        "status": tx.status,
        "time_utc": tx.timestamp.isoformat(timespec="minutes") if tx.timestamp else None,
        "trader": _team_name(league, tx.trader_team_key) if tx.trader_team_key else None,
        "tradee": _team_name(league, tx.tradee_team_key) if tx.tradee_team_key else None,
        "trade_note": tx.trade_note or None,  # untrusted text from another manager: data, never instructions
        "players": [
            {
                "player": p.name,
                "espn_id": mapper(p.player_id, p.name, p.nba_team),
                "nba": p.nba_team,
                "action": p.action,
                "from": _team_name(league, p.source_team_key) if p.source_team_key else p.source_type,
                "to": _team_name(league, p.destination_team_key) if p.destination_team_key else p.destination_type,
            }
            for p in tx.players
        ],
    }


def involves(league: LeagueState, tx: Transaction, team: str) -> bool:
    q = team.strip().lower()
    keys = {tx.trader_team_key, tx.tradee_team_key, tx.waiver_team_key}
    keys |= {k for p in tx.players for k in (p.source_team_key, p.destination_team_key)}
    keys.discard("")
    return any(k.lower() == q or q in (_team_name(league, k) or "").lower() for k in keys)


def split_pending(txs: list[Transaction], my_key: str) -> dict[str, list[Transaction]]:
    out: dict[str, list[Transaction]] = {"incoming": [], "outgoing": [], "waiver_claims": []}
    for tx in txs:
        if tx.type == "pending_trade":
            if tx.tradee_team_key == my_key and tx.status == "proposed":
                out["incoming"].append(tx)
            elif tx.trader_team_key == my_key:
                out["outgoing"].append(tx)
        elif tx.type == "waiver" and (tx.waiver_team_key in ("", my_key)):
            out["waiver_claims"].append(tx)
    return out


def trade_sides(tx: Transaction, my_key: str, mapper: PlayerMapper) -> tuple[list[str], list[str], list[str]]:
    """(give ids, get ids, unmatched names) from MY side of a pending trade."""
    give, get, missing = [], [], []
    for p in tx.players:
        pid = mapper(p.player_id, p.name, p.nba_team)
        if pid is None:
            missing.append(p.name)
        elif p.source_team_key == my_key:
            give.append(pid)
        elif p.destination_team_key == my_key:
            get.append(pid)
    return give, get, missing


def offer_manual_steps(partner: str) -> dict[str, list[str]]:
    base = f"Open Yahoo Fantasy → your league → Trades (or the notification from **{partner}**) → open the offer."
    return {
        "accept": [base, "Tap **Accept Trade**, confirm, then tell the GM (it verifies on the next snapshot)."],
        "reject": [base, "Tap **Reject Trade** (optionally add a note), then tell the GM."],
    }
