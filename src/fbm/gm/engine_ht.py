"""HT-mode engine plumbing shared by the GM services: the suggestion gate (owner rule: no fallback), the decision
logger (contract C: options -> `choosers.choose` -> `decisions.log`), the situation features, and the official
`ht_spread` weekly-odds forecast record (contract B).

Logging never changes a recommendation: the recommendation is whatever `choosers.choose` picks from the options, and
a failure to write the log is swallowed (reported in the tool output as `decision_id: None`).
"""

from __future__ import annotations

import functools
import json
import sqlite3
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, date, datetime
from typing import Any

from fbm.gm import choosers, decisions, policy


def enabled() -> bool:
    """HT mode ([hashtag] enabled = true in config/settings.toml)."""
    try:
        from fbm.settings import load_settings

        return bool(load_settings().hashtag_enabled)
    except Exception:  # noqa: BLE001 - no settings file (fresh clone/tests): not HT mode
        return False


def gate(conn: sqlite3.Connection | None = None) -> dict[str, Any] | None:
    """The HT_UNHEALTHY payload a suggestion tool returns instead of a suggestion, or None when it may proceed
    (HT healthy, or HT mode off)."""
    if not enabled():
        return None
    from fbm.hashtag import health
    from fbm.store import db

    try:
        health.require_healthy(conn or db.connect(), enabled=True)
    except health.HTUnhealthyError as err:
        return err.status.gate_payload()
    return None


def gated(fn: Callable[..., dict[str, Any]]) -> Callable[..., dict[str, Any]]:
    """Decorator for suggestion tools: in HT mode, return the gate payload while HT is unhealthy."""

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> dict[str, Any]:
        blocked = gate()
        return blocked if blocked is not None else fn(*args, **kwargs)

    return wrapper


def odds_map(odds: Any) -> dict[str, float]:
    """{cat: P(win) + 0.5 P(tie)} from a simulation.MatchupOdds, or a {cat: p} mapping as is."""
    if odds is None:
        return {}
    if isinstance(odds, Mapping):
        return {str(k): float(v) for k, v in odds.items()}
    return {o.category.key: float(o.p_win + 0.5 * o.p_tie) for o in odds.categories}


def trigger_name(trigger: str | None, default: str = "owner-ask") -> str:
    t = (trigger or default).strip()
    return t if t in decisions.TRIGGERS else "other"


def situation(
    *,
    odds: Mapping[str, float],
    punts: Iterable[str],
    day: date,
    adds_left: int | None,
    week: int | None,
    days_left: int,
    games_left: Mapping[str, int],
    banked: Mapping[str, Mapping[str, float]] | None,
    opponent_key: str | None,
    view_used: str | None,
    ht_as_of: str | None,
    **extra: Any,
) -> dict[str, Any]:
    """The situation features every decision logs (contract C), plus any kind-specific extras."""
    return {
        "odds": {k: round(float(v), 4) for k, v in odds.items()},
        "punts": list(punts),
        "weekday": day.weekday(),
        "adds_left": adds_left,
        "week": week,
        "days_left": days_left,
        "games_left": dict(games_left),
        "banked": {k: dict(v) for k, v in (banked or {}).items()},
        "opponent_key": opponent_key,
        "view_used": view_used,
        "ht_as_of": ht_as_of,
        **extra,
    }


def banked_by_cat(me: Mapping[str, float], opp: Mapping[str, float], categories) -> dict[str, dict[str, Any]]:
    """{cat: {me, opp}} banked this week; FG%/FT% as [made, attempted]."""
    out: dict[str, dict[str, Any]] = {}
    for c in categories:
        if c.kind == "pct":
            out[c.key] = {
                "me": [float(me.get(c.made, 0.0)), float(me.get(c.att, 0.0))],
                "opp": [float(opp.get(c.made, 0.0)), float(opp.get(c.att, 0.0))],
            }
        else:
            out[c.key] = {"me": float(me.get(c.stat, 0.0)), "opp": float(opp.get(c.stat, 0.0))}
    return out


def decide(
    conn: sqlite3.Connection,
    *,
    kind: str,
    trigger: str | None,
    et_date: date | str,
    situation: dict[str, Any],
    options: list[dict[str, Any]],
    week: int | None = None,
    proposal_ref: str | None = None,
    log: bool = True,
) -> tuple[str, int | None]:
    """Pick the recommendation with the shared decision rule, then log it. Returns (option id, decision id)."""
    params = policy.all_values(conn)
    decision = {"kind": kind, "situation": situation, "options": options}
    recommended = choosers.choose(decision, params)
    if not log:
        return recommended, None
    try:
        did = decisions.log(
            conn,
            kind=kind,
            trigger=trigger_name(trigger),
            et_date=et_date,
            situation=situation,
            options=options,
            recommended=recommended,
            week=week,
            proposal_ref=proposal_ref,
            policy_snapshot=(policy.version(conn), params),
        )
    except Exception:  # noqa: BLE001 - logging must never change or block a recommendation
        did = None
    return recommended, did


def logged_today(conn: sqlite3.Connection, kind: str, et_date: date | str, trigger: str | None = None) -> bool:
    args: list[Any] = [kind, str(et_date)]
    clause = ""
    if trigger:
        clause = " AND trigger = ?"
        args.append(trigger_name(trigger))
    row = conn.execute(f"SELECT 1 FROM decisions WHERE kind = ? AND et_date = ?{clause} LIMIT 1", args).fetchone()
    return row is not None


def record_ht_prediction(
    conn: sqlite3.Connection,
    payload: dict[str, Any],
    window: tuple[date, date],
    team_key: str,
    opponent_key: str,
) -> int:
    """Store an official weekly-odds forecast (source `ht_spread`, contract B payload) in `predictions`."""
    cur = conn.execute(
        "INSERT INTO predictions(created_at, kind, window_start, window_end, team_key, opponent_key, source, payload) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (
            datetime.now(UTC).isoformat(timespec="seconds"),
            "matchup",
            window[0].isoformat(),
            window[1].isoformat(),
            team_key,
            opponent_key,
            "ht_spread",
            json.dumps(payload, default=str),
        ),
    )
    conn.commit()
    return int(cur.lastrowid)


def lineup_cat_scores(g, starters: Iterable[str], categories) -> dict[str, float]:
    """Summed per-category value (g_<cat> columns) of the starters."""
    ids = [pid for pid in starters if pid in g.index]
    return {c.key: round(float(g.loc[ids, f"g_{c.key}"].sum()) if ids else 0.0, 4) for c in categories}


def odds_delta(after: Any, before: Any) -> dict[str, float]:
    """Change in P(win) (+0.5 tie) per category between two MatchupOdds (or odds maps)."""
    a, b = odds_map(after), odds_map(before)
    return {k: round(a.get(k, 0.0) - v, 4) for k, v in b.items()}
