"""Replay grader: the realized outcome of every option FBM considered, on real box scores and Yahoo final totals.

Method (design §5C): start from my REAL week totals (Yahoo final, incl. FGM/FGA and FTM/FTA), subtract the real
box-score lines of the player-days the option changes versus what actually happened (Yahoo daily lineups), add the
option's player-days from ESPN box scores (played games only), recompute every category (percentages from
made/attempted) and compare with the opponent's real totals. Only the opponent is held at what they actually did.

An added player counts only on days he would have started: he takes the dropped player's slot on days the dropped
player actually started, else an open starting slot (fewer starters than slots that day). What actually happened is
first undone (the chosen option's moves are reversed) before the option's moves are applied, so every option is
compared on the same footing.

ros_value (drops, adds, IL, trades) = real production of players gained minus players given up over
`replay.ros_horizon_days` after the move, in team-week SD units (outcomes.contribution scaling).

A result is `complete` only once Yahoo marks the week final, every NBA game of the week is final with box scores
synced, and (for ros_value) the horizon has passed.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, timedelta
from itertools import zip_longest
from typing import Any

from fbm.analytics.categories import NINE_CAT, Category
from fbm.analytics.simulation import STATS, category_values
from fbm.analytics.valuation import PoolStats
from fbm.constants import C
from fbm.gm import decisions as dlog
from fbm.gm import outcomes
from fbm.yahoo import lineups
from fbm.yahoo.lineups import FinalsProvider

ROS_KINDS = ("stream_plan", "waiver_claim", "drop", "il", "trade")
MOVE_KINDS = ("stream_plan", "waiver_claim", "drop", "il", "trade")
WEEK_KINDS = ("chase_concede", "category_weights")
LEGACY_SOURCES = ("yahoo", "demo")


# ------------------------------------------------------------------ helpers


def _d(x: Any) -> date:
    return x if isinstance(x, date) else date.fromisoformat(str(x)[:10])


def _line(conn: sqlite3.Connection, pid: str, day: str) -> dict[str, float] | None:
    row = conn.execute(
        f"SELECT {', '.join(STATS)} FROM box_lines WHERE espn_id = ? AND et_date = ? AND played = 1", (pid, day)
    ).fetchone()
    return {s: float(row[s] or 0) for s in STATS} if row else None


def _value(totals: Mapping[str, float], cat: Category) -> float:
    return float(category_values(outcomes._as_array(totals), cat)[0])


def cat_results(
    me: Mapping[str, float], opp: Mapping[str, float], categories: Sequence[Category]
) -> tuple[dict[str, float], dict[str, float]]:
    results, margins = {}, {}
    for cat in categories:
        a, b = _value(me, cat), _value(opp, cat)
        margins[cat.key] = round((a - b) if cat.higher_is_better else (b - a), 4)
        results[cat.key] = outcomes.category_outcome(cat, me, opp)
    return results, margins


def _summary(results: Mapping[str, float], margins: Mapping[str, float], ros, complete: bool) -> dict[str, Any]:
    won = float(sum(results.values()))
    n = len(results)
    matchup = None if not n or won * 2 == n else won * 2 > n
    return {
        "cats_won": won,
        "matchup_won": matchup,
        "cat_results": dict(results),
        "cat_margins": dict(margins),
        "ros_value": ros,
        "complete": complete,
    }


def _default_team_key() -> str | None:
    try:
        from fbm.config import load_league_config

        return load_league_config().team_key or None
    except Exception:
        return None


class _Week:
    """The real week around a decision: Yahoo final totals + my actual daily starters."""

    def __init__(self, conn, decision, day: date, finals: FinalsProvider, categories, today: date):
        sit = decision.get("situation") or {}
        team = sit.get("team_key")
        final = finals(day, team, None) if team else None
        if final is None and not team and sit.get("opponent_key"):
            final = finals(day, None, sit.get("opponent_key"))
        if final is None and not team:
            team = _default_team_key()
            final = finals(day, team, None) if team else None
        self.final = final
        self.categories = [
            c for c in categories if not final or not final["stat_winners"] or c.key in final["stat_winners"]
        ]
        self.reason = ""
        if not final:
            self.reason = "no Yahoo matchup result cached for that week"
            return
        self.team_key = final["team_key"]
        self.start, self.end = _d(final["week_start"]), _d(final["week_end"])
        self.days = [(self.start + timedelta(days=i)).isoformat() for i in range((self.end - self.start).days + 1)]
        self.started = lineups.started_days(conn, self.team_key, self.start, self.end)
        observed = [len(s) for s in self.started.values()]
        self.slots = int(sit.get("starting_slots") or (max(observed) if observed else 0))
        if not final["final"]:
            self.reason = "week not final in Yahoo yet"
        elif self.end >= today or not outcomes.window_complete(conn, self.start.isoformat(), self.end.isoformat()):
            self.reason = "box scores not all final + synced"

    @property
    def ok(self) -> bool:
        return self.final is not None

    @property
    def complete(self) -> bool:
        return self.ok and not self.reason

    def actual(self) -> dict[str, dict[str, Any]]:
        winners = self.final["stat_winners"] or {}
        res, margins = cat_results(self.final["me"], self.final["opp"], self.categories)
        res.update({k: float(v) for k, v in winners.items() if k in res})
        return {"results": res, "margins": margins}

    def counterfactual(self, conn, lineup: Mapping[str, set[str]]) -> tuple[dict[str, float], dict[str, float]]:
        me = dict(self.final["me"])
        changed = False
        for day in self.days:
            actual, alt = self.started.get(day, set()), lineup.get(day, set())
            for pid, sign in [*((p, -1.0) for p in actual - alt), *((p, 1.0) for p in alt - actual)]:
                line = _line(conn, pid, day)
                if line:
                    changed = True
                    for s in STATS:
                        me[s] = me.get(s, 0.0) + sign * line[s]
        if not changed:
            a = self.actual()
            return a["results"], a["margins"]
        return cat_results(me, self.final["opp"], self.categories)


def _swap(conn, week: _Week, lineup: dict[str, set[str]], out: str | None, inn: str | None, from_day: date) -> None:
    for day in week.days:
        if day < from_day.isoformat():
            continue
        s = lineup.setdefault(day, set())
        plays = bool(inn) and _line(conn, inn, day) is not None
        if out and out in s:
            s.discard(out)
            if plays:
                s.add(inn)
        elif plays and inn not in s and len(s) < week.slots:
            s.add(inn)


def _moves(kind: str, option: Mapping[str, Any], decision: Mapping[str, Any]) -> list[dict[str, Any]]:
    spec = option.get("spec") or {}
    day = spec.get("date") or decision["et_date"]
    if kind == "il":
        action, player, repl = spec.get("action") or option.get("action"), spec.get("player"), spec.get("replacement")
        if action == "drop":
            return [{"add": repl, "drop": player, "date": day}]
        if action == "activate":
            return [{"add": player, "drop": repl, "date": day}]
        return []
    if kind == "trade":
        return [
            {"add": g, "drop": o, "date": day} for g, o in zip_longest(spec.get("get") or [], spec.get("give") or [])
        ]
    return [{"add": m.get("add"), "drop": m.get("drop"), "date": m.get("date") or day} for m in spec.get("moves") or []]


def _chosen_option(decision: Mapping[str, Any]) -> dict[str, Any] | None:
    chosen = decision.get("chosen")
    return next((o for o in decision["options"] if str(o["id"]) == str(chosen)), None) if chosen else None


def _ros(conn, moves, pool, categories, today, punts) -> tuple[float | None, bool]:
    if pool is None or not moves:
        return (0.0 if not moves else None), True
    horizon = int(C("replay.ros_horizon_days"))
    total, complete = 0.0, True
    for m in moves:
        start = _d(m["date"])
        end = start + timedelta(days=horizon - 1)
        if end >= today or not outcomes.window_complete(conn, start.isoformat(), end.isoformat()):
            complete = False
        end = min(end, today - timedelta(days=1))
        for pid, sign in ((m.get("add"), 1.0), (m.get("drop"), -1.0)):
            if pid:
                total += sign * outcomes.contribution(conn, pid, start, end, pool, categories, punts)["total"]
    return round(total, 3), complete


# ------------------------------------------------------------------ public API


def replay_option(
    conn: sqlite3.Connection,
    decision: dict[str, Any],
    option: dict[str, Any],
    *,
    finals: FinalsProvider | None = None,
    pool: PoolStats | None = None,
    categories: Sequence[Category] = NINE_CAT,
    today: date | None = None,
) -> dict[str, Any]:
    """Realized outcome of one option: {cats_won, matchup_won, cat_results, cat_margins, ros_value, complete}."""
    from fbm.timeutil import nba_today

    finals = finals or lineups.db_provider(conn)
    today = today or nba_today()
    kind = decision["kind"]
    spec = option.get("spec") or {}
    moves = _moves(kind, option, decision) if kind in MOVE_KINDS else []
    anchor = _d(spec.get("date") or (moves[0]["date"] if moves else decision["et_date"]))
    week = _Week(conn, decision, anchor, finals, categories, today)
    if not week.ok:
        return {**_summary({}, {}, None, False), "cats_won": None, "note": week.reason}

    if kind in WEEK_KINDS:
        a = week.actual()
        out = _summary(a["results"], a["margins"], None, week.complete)
        status = spec.get("status") or {}
        if status:
            calls = {}
            for cat, st in status.items():
                if cat in a["results"]:
                    won = a["results"][cat]
                    calls[cat] = won >= 0.5 if st in ("chase", "hold", "lock") else won < 0.5
            out["status_hits"] = round(sum(calls.values()) / len(calls), 3) if calls else None
            out["status_calls"] = calls
        return out

    lineup = {d: set(s) for d, s in week.started.items()}
    missing = [d for d in week.days if d not in week.started]
    if kind == "lineup":
        day = str(spec.get("date") or decision["et_date"])
        if day not in week.started:
            return {**_summary({}, {}, None, False), "cats_won": None, "note": f"no Yahoo lineup stored for {day}"}
        lineup[day] = {str(p) for p in spec.get("starters") or []}
        missing = []
    else:
        chosen = _chosen_option(decision)
        for m in _moves(kind, chosen, decision) if chosen else []:  # undo what actually happened
            _swap(conn, week, lineup, m.get("add"), m.get("drop"), _d(m["date"]))
        for m in moves:
            _swap(conn, week, lineup, m.get("drop"), m.get("add"), _d(m["date"]))
    results, margins = week.counterfactual(conn, lineup)
    ros, ros_done = (None, True)
    if kind in ROS_KINDS:
        punts = (decision.get("situation") or {}).get("punts") or ()
        ros, ros_done = _ros(conn, moves, pool, categories, today, punts)
    out = _summary(results, margins, ros, week.complete and ros_done and not missing)
    if week.reason or missing:
        out["note"] = week.reason or f"Yahoo lineups missing for {', '.join(missing)}"
    return out


def grade_decision(conn: sqlite3.Connection, decision: dict[str, Any], **kw: Any) -> dict[str, Any]:
    """Replay every option of a logged decision. Regret = cats(best) - cats(recommended)."""
    results = {str(o["id"]): replay_option(conn, decision, o, **kw) for o in decision["options"]}
    scored = {k: v for k, v in results.items() if v.get("cats_won") is not None}
    best = max(scored, key=lambda k: (scored[k]["cats_won"], scored[k]["ros_value"] or 0.0)) if scored else None
    rec = decision.get("recommended")
    chosen = decision.get("chosen") if str(decision.get("chosen")) in results else None
    regret = scored[best]["cats_won"] - scored[rec]["cats_won"] if best is not None and rec in scored else None
    out = {
        "options": results,
        "recommended": rec,
        "chosen": chosen,
        "best": best,
        "regret_cats": regret,
        "complete": bool(results) and all(v["complete"] for v in results.values()),
    }
    if decision["kind"] in WEEK_KINDS and scored:
        out["cat_results"] = dict(next(iter(scored.values()))["cat_results"])
    if best is not None and rec in scored and scored[best]["ros_value"] is not None:
        out["regret_ros"] = round((scored[best]["ros_value"] or 0) - (scored[rec]["ros_value"] or 0), 3)
    if chosen is not None and best is not None and chosen in scored:
        out["regret_chosen_cats"] = scored[best]["cats_won"] - scored[chosen]["cats_won"]
    return out


def _effective(conn, players: Iterable[str], day: str) -> set[str]:
    return {p for p in players if _line(conn, str(p), day) is not None}


def infer_chosen(
    conn: sqlite3.Connection, decision: dict[str, Any], finals: FinalsProvider | None = None
) -> tuple[Any, str] | None:
    """What actually happened, from Yahoo daily lineups (lineup) or the linked proposal's final status."""
    kind = decision["kind"]
    options = decision["options"]
    if kind == "lineup":
        finals = finals or lineups.db_provider(conn)
        spec_day = None
        for o in options:
            spec_day = spec_day or (o.get("spec") or {}).get("date")
        day = str(spec_day or decision["et_date"])
        week = _Week(conn, decision, _d(day), finals, NINE_CAT, _d(day) + timedelta(days=1))
        team = week.team_key if week.ok else (decision.get("situation") or {}).get("team_key") or _default_team_key()
        actual = lineups.started(conn, team, _d(day)) if team else None
        if actual is None:
            return None
        eff = _effective(conn, actual, day)
        for o in options:
            if _effective(conn, (o.get("spec") or {}).get("starters") or [], day) == eff:
                return str(o["id"]), "yahoo_lineup"
        return {"starters": sorted(actual)}, "yahoo_lineup"
    ref = decision.get("proposal_ref")
    if not ref:
        return None
    from fbm.gm import proposals as props

    try:
        p = props.get(conn, ref)
    except Exception:
        return None
    stand = next((o for o in options if o.get("stand_pat")), None)
    if p.status in ("rejected", "expired"):
        return (str(stand["id"]), "proposal") if stand else None
    if p.status != "done":
        return None
    pay = p.payload or {}
    want = {str(x) for x in [pay.get("add"), pay.get("drop"), pay.get("activate"), *(pay.get("get") or [])] if x}
    for o in options:
        spec = o.get("spec") or {}
        ids = {str(x) for m in spec.get("moves") or [] for x in (m.get("add"), m.get("drop")) if x}
        ids |= {str(x) for x in [*(spec.get("get") or []), spec.get("player"), spec.get("replacement")] if x}
        if want and want <= ids:
            return str(o["id"]), "proposal"
    return (str(decision["recommended"]), "proposal") if decision.get("recommended") else None


def grade_pending(conn: sqlite3.Connection, today: date, lookback_days: int = 35, **kw: Any) -> list[dict[str, Any]]:
    """Fill `chosen`, then replay every decision of the last `lookback_days` (before today) without a complete
    result. Complete results are saved (decisions.save_result); incomplete ones are returned but not saved."""
    since = (today - timedelta(days=lookback_days)).isoformat()
    until = (today - timedelta(days=1)).isoformat()
    out = []
    for d in dlog.query(conn, since=since, until=until, limit=5000):
        if d.get("result") and d["result"].get("complete"):
            continue
        if not d.get("chosen"):
            found = infer_chosen(conn, d, kw.get("finals"))
            if found:
                dlog.set_chosen(conn, d["id"], found[0], found[1])
                d["chosen"] = found[0] if isinstance(found[0], str) else json.dumps(found[0])
        res = grade_decision(conn, d, today=today, **kw)
        if res["complete"]:
            dlog.save_result(conn, d["id"], res)
        out.append(
            {
                "id": d["id"],
                "kind": d["kind"],
                "et_date": d["et_date"],
                "recommended": res["recommended"],
                "chosen": res["chosen"],
                "best": res["best"],
                "regret_cats": res["regret_cats"],
                "complete": res["complete"],
            }
        )
    return out


def regret_summary(graded: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """By kind: count, total/mean regret (cats), how often the recommendation was the best option / was followed."""
    by: dict[str, list[Mapping[str, Any]]] = {}
    for g in graded:
        by.setdefault(g["kind"], []).append(g)
    out = {}
    for kind, rows in by.items():
        reg = [r["regret_cats"] for r in rows if r.get("regret_cats") is not None]
        followed = [r for r in rows if r.get("chosen") is not None]
        out[kind] = {
            "decisions": len(rows),
            "graded": len(reg),
            "regret_cats_total": round(sum(reg), 2),
            "regret_cats_mean": round(sum(reg) / len(reg), 3) if reg else None,
            "recommended_was_best": sum(1 for r in rows if r.get("best") is not None and r["best"] == r["recommended"]),
            "followed": sum(1 for r in followed if str(r["chosen"]) == str(r["recommended"])),
            "chosen_known": len(followed),
        }
    return out


def regrade_legacy_predictions(
    conn: sqlite3.Connection,
    finals: FinalsProvider | None = None,
    categories: Sequence[Category] = NINE_CAT,
) -> dict[str, Any]:
    """Migration: re-grade already-graded "yahoo"/"demo" predictions against Yahoo FINAL matchup results (fix of the
    2026-10-26 bug: mid-week forecasts were graded on the remaining days only). The old result is kept under
    result["legacy"]. Predictions without a cached final result (e.g. the demo league) are left unchanged."""
    finals = finals or lineups.db_provider(conn)
    by_key = {c.key: c for c in categories}
    items, skipped = [], 0
    rows = conn.execute(
        f"SELECT * FROM predictions WHERE graded_at IS NOT NULL AND source IN ({','.join('?' * len(LEGACY_SOURCES))}) "
        "ORDER BY id",
        LEGACY_SOURCES,
    ).fetchall()
    for row in rows:
        old = json.loads(row["result"]) if row["result"] else {}
        if "legacy" in old:
            skipped += 1
            continue
        final = finals(_d(row["window_end"]), row["team_key"], None)
        if not final or not final.get("final"):
            skipped += 1
            continue
        if final.get("opponent_key") and row["opponent_key"] and final["opponent_key"] != row["opponent_key"]:
            skipped += 1
            continue
        new = outcomes._grade_result(
            json.loads(row["payload"]), by_key, final["me"], final["opp"], final.get("stat_winners"), "yahoo_final"
        )
        new["legacy"] = old
        conn.execute("UPDATE predictions SET result = ? WHERE id = ?", (json.dumps(new), row["id"]))
        items.append(
            {
                "id": row["id"],
                "window": f"{row['window_start']}..{row['window_end']}",
                "old_actual_cats": old.get("actual_cats"),
                "new_actual_cats": new["actual_cats"],
                "old_brier": old.get("brier_mean"),
                "new_brier": new["brier_mean"],
            }
        )
    conn.commit()
    return {"regraded": len(items), "skipped": skipped, "items": items}
