"""Signal feed: persist detected signals once per episode, list what's new/active, and grade them afterwards.

Dedup: a signal with the same key (kind, player, direction[, injured teammates]) seen again within
`signals.dedup_days` updates the existing row (last_seen, strength, evidence for display) instead of creating a new
one, so `first_seen` is when FBM first caught it. A re-detection at a higher confidence starts a new episode (the
owner could first act on it then). Grading uses `basis` — metric, reference, predicted and confidence frozen when
the episode was first seen (no look-ahead) — over the `signals.grade_horizon_days` after first sight:
- buy/sell (basis.predicted present): hit if production over the window was closer to our projection than to the
  season-to-date line the market saw;
- otherwise a hit if the metric (per-game value or minutes) moved in the predicted direction from basis.reference
  (minutes by at least `signals.grade_min_minutes`).
`pct_owned_change` (Yahoo league) shows whether the market followed after we flagged him — the lead we had.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pandas as pd

from fbm.analytics.signals import Signal
from fbm.analytics.valuation import PoolStats, ValuationConfig, score_table
from fbm.constants import C
from fbm.timeutil import nba_today

CONF_ORDER = {"high": 0, "medium": 1, "low": 2}


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat(timespec="seconds")


def record(
    conn: sqlite3.Connection,
    season: int,
    signals: Iterable[Signal],
    now: datetime | None = None,
    ownership: Mapping[str, Sequence[float | None]] | None = None,
) -> list[int]:
    """Insert new signals / refresh re-detected ones. Returns ids of signals seen for the first time."""
    now = now or datetime.now(UTC)
    ownership = ownership or {}
    cutoff = _iso(now - timedelta(days=int(C("signals.dedup_days"))))
    stamp, today_et = _iso(now), nba_today(now).isoformat()
    new_ids = []
    for s in signals:
        row = conn.execute(
            "SELECT id, basis FROM signals WHERE key = ? AND last_seen >= ? ORDER BY id DESC LIMIT 1", (s.key, cutoff)
        ).fetchone()
        fields = (s.name, s.team, round(s.strength, 3), s.confidence, json.dumps(s.reasons), s.note)
        upgraded = False
        if row:
            first_conf = json.loads(row["basis"] or "{}").get("confidence", s.confidence)
            upgraded = CONF_ORDER.get(s.confidence, 3) < CONF_ORDER.get(first_conf, 3)
        if row and not upgraded:
            conn.execute(
                "UPDATE signals SET name=?, team=?, strength=?, confidence=?, reasons=?, note=?, evidence=?, "
                "last_seen=? WHERE id=?",
                (*fields, json.dumps(s.evidence, default=str), stamp, row["id"]),
            )
            continue
        if row:  # confidence upgrade: close the old episode, start a new one from now
            conn.execute("UPDATE signals SET key = key || ':superseded' WHERE id = ?", (row["id"],))
        owned = (ownership.get(s.espn_id) or [None])[0]
        basis = {k: s.evidence.get(k) for k in ("metric", "reference", "predicted")} | {"confidence": s.confidence}
        cur = conn.execute(
            "INSERT INTO signals(season, key, kind, direction, espn_id, name, team, strength, confidence, reasons, "
            "note, evidence, first_seen, first_seen_et, last_seen, pct_owned_at, basis) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                season,
                s.key,
                s.kind,
                s.direction,
                s.espn_id,
                *fields,
                json.dumps(s.evidence, default=str),
                stamp,
                today_et,
                stamp,
                owned,
                json.dumps(basis, default=str),
            ),
        )
        new_ids.append(cur.lastrowid)
    conn.commit()
    return new_ids


def _row(r: sqlite3.Row, now: datetime) -> dict[str, Any]:
    new_cut = _iso(now - timedelta(hours=float(C("signals.new_hours"))))
    return {
        "signal_id": r["id"],
        "kind": r["kind"],
        "direction": r["direction"],
        "id": r["espn_id"],
        "player": r["name"],
        "nba": r["team"],
        "strength": r["strength"],
        "confidence": r["confidence"],
        "reasons": json.loads(r["reasons"] or "[]"),
        "note": r["note"],
        "evidence": json.loads(r["evidence"] or "{}"),
        "first_seen_et": r["first_seen_et"],
        "is_new": r["first_seen"] >= new_cut,
        "pct_owned_at_first_seen": r["pct_owned_at"],
    }


def active(
    conn: sqlite3.Connection,
    now: datetime | None = None,
    kinds: Sequence[str] | None = None,
    new_only: bool = False,
    player: str | None = None,
    min_confidence: str | None = None,
) -> list[dict[str, Any]]:
    """Signals re-detected within `signals.active_hours` (newest catches first, then confidence, then strength).
    min_confidence defaults to `signals.feed_min_confidence` ("low" shows everything)."""
    now = now or datetime.now(UTC)
    floor = CONF_ORDER.get((min_confidence or str(C("signals.feed_min_confidence"))).lower(), 1)
    sql = "SELECT * FROM signals WHERE last_seen >= ? AND key NOT LIKE '%:superseded'"
    params: list[Any] = [_iso(now - timedelta(hours=float(C("signals.active_hours"))))]
    if new_only:
        sql += " AND first_seen >= ?"
        params.append(_iso(now - timedelta(hours=float(C("signals.new_hours")))))
    if kinds:
        sql += f" AND kind IN ({','.join('?' * len(kinds))})"
        params += list(kinds)
    if player:
        sql += " AND espn_id = ?"
        params.append(player)
    rows = [_row(r, now) for r in conn.execute(sql, params)]
    rows = [r for r in rows if CONF_ORDER.get(r["confidence"], 3) <= floor]
    return sorted(rows, key=lambda r: (not r["is_new"], CONF_ORDER.get(r["confidence"], 3), -(r["strength"] or 0)))


def grade_pending(
    conn: sqlite3.Connection,
    pool: PoolStats,
    cfg: ValuationConfig,
    today: date,
    ownership: Mapping[str, Sequence[float | None]] | None = None,
) -> list[dict[str, Any]]:
    """Grade signals whose window has finished (legacy, non-HT mode: projection accuracy). Returns the newly graded
    ones."""
    from fbm.analytics.boxscores import played_lines, window_actuals

    ownership = ownership or {}
    horizon = int(C("signals.grade_horizon_days"))
    min_games = int(C("signals.grade_min_games"))
    min_minutes = float(C("signals.grade_min_minutes"))
    last_first_seen = (today - timedelta(days=horizon + 1)).isoformat()
    rows = conn.execute(
        "SELECT * FROM signals WHERE graded_at IS NULL AND first_seen_et <= ? ORDER BY first_seen_et",
        (last_first_seen,),
    ).fetchall()
    graded: list[dict[str, Any]] = []
    boxes: dict[int, pd.DataFrame] = {}
    windows: dict[tuple[int, str], tuple[pd.DataFrame, pd.Series]] = {}
    for r in rows:
        season, seen = int(r["season"]), r["first_seen_et"]
        if (season, seen) not in windows:
            if season not in boxes:
                boxes[season] = played_lines(conn, season)
            box = boxes[season]
            start = date.fromisoformat(seen) + timedelta(days=1)
            actual = window_actuals(box, start, start + timedelta(days=horizon - 1), min_games)
            values = score_table(actual, pool, cfg)["g_total"] if not actual.empty else pd.Series(dtype=float)
            windows[(season, seen)] = (actual, values)
        actual, values = windows[(season, seen)]
        ev = json.loads(r["basis"] or r["evidence"] or "{}")
        pid, metric, ref, pred = r["espn_id"], ev.get("metric", "value"), ev.get("reference"), ev.get("predicted")
        result: dict[str, Any] = {"metric": metric, "reference": ref, "predicted": pred, "window_days": horizon}
        if pid not in actual.index or ref is None:
            result["outcome"] = "no_games"
        else:
            post = float(values[pid]) if metric == "value" else float(actual.loc[pid, "minutes"])
            result["post"] = round(post, 2)
            result["games"] = int(actual.loc[pid, "games"])
            if pred is not None:
                hit = abs(post - float(pred)) < abs(post - float(ref))
            else:
                sign = 1.0 if r["direction"] == "up" else -1.0
                hit = sign * (post - float(ref)) > (min_minutes if metric == "minutes" else 0.0)
            result["outcome"] = "hit" if hit else "miss"
        own_now = (ownership.get(pid) or [None])[0]
        if own_now is not None and r["pct_owned_at"] is not None:
            result["pct_owned_change"] = round(float(own_now) - float(r["pct_owned_at"]), 1)
        conn.execute(
            "UPDATE signals SET graded_at = ?, result = ? WHERE id = ?",
            (_iso(datetime.now(UTC)), json.dumps(result), r["id"]),
        )
        graded.append({"signal_id": r["id"], "kind": r["kind"], "player": r["name"], **result})
    conn.commit()
    return graded


def summary(conn: sqlite3.Connection, season: int | None = None) -> dict[str, Any]:
    """Hit rate by kind and confidence, plus the average ownership change after the signal (market lead)."""
    sql = "SELECT kind, confidence, direction, result, basis FROM signals WHERE graded_at IS NOT NULL"
    params: tuple = ()
    if season is not None:
        sql += " AND season = ?"
        params = (season,)
    rows = [
        dict(r)
        | {
            "result": json.loads(r["result"] or "{}"),
            "confidence": json.loads(r["basis"] or "{}").get("confidence", r["confidence"]),
        }
        for r in conn.execute(sql, params)
    ]
    by: dict[str, dict[str, Any]] = {}
    for r in rows:
        if r["result"].get("mode") == "ht":
            continue
        for key in (r["kind"], f"{r['kind']}/{r['confidence']}"):
            b = by.setdefault(key, {"graded": 0, "hit": 0, "miss": 0, "no_games": 0, "_own": []})
            b["graded"] += 1
            b[r["result"].get("outcome", "no_games")] += 1
            if "pct_owned_change" in r["result"]:
                b["_own"].append(r["result"]["pct_owned_change"] * (1 if r["direction"] == "up" else -1))
    out = {}
    for key, b in sorted(by.items()):
        decided = b["hit"] + b["miss"]
        own = b.pop("_own")
        out[key] = {
            **b,
            "hit_rate": round(b["hit"] / decided, 3) if decided else None,
            "avg_pct_owned_move_in_signal_direction": round(sum(own) / len(own), 1) if own else None,
        }
    return {
        "by_kind": out,
        "how_to_read": "hit_rate > 0.5 means the signal beat the naive view (season-to-date line / old level). "
        "A positive ownership move means the market followed after FBM flagged him (we were early).",
    }


# ------------------------------------------------------------------ HT mode grading (signals as decision triggers)


def _ids_in(obj: Any) -> set[str]:
    """Every scalar inside a decision option / proposal payload, as strings (player ids are found among them)."""
    out: set[str] = set()
    if isinstance(obj, Mapping):
        for v in obj.values():
            out |= _ids_in(v)
    elif isinstance(obj, list | tuple):
        for v in obj:
            out |= _ids_in(v)
    elif obj is not None and not isinstance(obj, bool | float):
        out.add(str(obj))
    return out


def _ht_lead(conn: sqlite3.Connection, pid: str, direction: str, seen: str, end: str) -> dict[str, Any]:
    """Days from first sight until HT's ROS projection moved the same way (total by `signals.ht_revision_min` or
    minutes by `signals.grade_min_minutes` vs the last import on/before first sight)."""
    rows = conn.execute(
        "SELECT as_of, total, minutes FROM hashtag_projections WHERE view = 'ROS' AND espn_id = ? AND as_of <= ? "
        "ORDER BY as_of",
        (pid, end),
    ).fetchall()
    base = [r for r in rows if r["as_of"] <= seen]
    if not base:
        return {"ht_lead_days": None, "ht_moved": None}
    b = base[-1]
    sign = 1.0 if direction == "up" else -1.0
    min_total, min_minutes = float(C("signals.ht_revision_min")), float(C("signals.grade_min_minutes"))
    for r in rows:
        if r["as_of"] <= seen:
            continue
        moved_total = (
            r["total"] is not None and b["total"] is not None and sign * (r["total"] - b["total"]) >= min_total
        )
        moved_min = (
            r["minutes"] is not None
            and b["minutes"] is not None
            and sign * (r["minutes"] - b["minutes"]) >= min_minutes
        )
        if moved_total or moved_min:
            days = (date.fromisoformat(r["as_of"]) - date.fromisoformat(seen)).days
            return {"ht_lead_days": days, "ht_moved": True, "ht_moved_on": r["as_of"]}
    return {"ht_lead_days": None, "ht_moved": False}


def _market_lead(
    conn: sqlite3.Connection, pid: str, direction: str, first_seen: str, end: str, owned_at: float | None
) -> dict[str, Any]:
    """Days from first sight until league % owned moved `signals.lead_owned_move` in the signal's direction (being
    rostered counts as up)."""
    from fbm.gm import ownership

    ownership.ensure(conn)
    rows = conn.execute(
        "SELECT observed_at, pct_owned, owner_team_key FROM ownership_history WHERE player_id = ? AND observed_at <= ? "
        "ORDER BY observed_at",
        (pid, end),
    ).fetchall()
    base = owned_at
    if base is None:
        before = [r["pct_owned"] for r in rows if r["observed_at"] <= first_seen and r["pct_owned"] is not None]
        base = before[-1] if before else None
    move, sign = float(C("signals.lead_owned_move")), 1.0 if direction == "up" else -1.0
    seen_day = datetime.fromisoformat(first_seen).date()
    for r in rows:
        if r["observed_at"] <= first_seen:
            continue
        rostered = bool(r["owner_team_key"])
        moved = (direction == "up" and rostered) or (
            base is not None and r["pct_owned"] is not None and sign * (float(r["pct_owned"]) - float(base)) >= move
        )
        if moved:
            days = (datetime.fromisoformat(r["observed_at"]).date() - seen_day).days
            return {"market_lead_days": days, "market_moved": True, "market_moved_on": r["observed_at"][:10]}
    return {"market_lead_days": None, "market_moved": False if base is not None or rows else None}


def _acted(conn: sqlite3.Connection, pid: str, start: str, end: str) -> dict[str, Any]:
    """Decisions (recommended/chosen option involves him) and proposals naming him within the window."""
    acted, considered = [], []
    for d in conn.execute(
        "SELECT id, kind, et_date, options, recommended, chosen, result, proposal_ref FROM decisions "
        "WHERE et_date >= ? AND et_date <= ? ORDER BY et_date, id",
        (start, end),
    ):
        options = json.loads(d["options"] or "[]")
        by_id = {str(o.get("id")): o for o in options if isinstance(o, Mapping)}
        if pid not in _ids_in(options):
            continue
        picked = [by_id.get(str(d["recommended"])), by_id.get(str(d["chosen"]))]
        entry = {"decision_id": d["id"], "kind": d["kind"], "et_date": d["et_date"], "proposal_ref": d["proposal_ref"]}
        if any(o is not None and pid in _ids_in(o) for o in picked):
            result = json.loads(d["result"]) if d["result"] else None
            if result:
                entry["replay"] = {
                    k: result.get(k) for k in ("regret_cats", "recommended", "chosen", "best", "complete")
                }
                rec = (result.get("options") or {}).get(str(result.get("recommended")))
                if isinstance(rec, Mapping):
                    entry["replay"]["recommended_cats_won"] = rec.get("cats_won")
            acted.append(entry)
        else:
            considered.append(entry)
    proposals = []
    for p in conn.execute(
        "SELECT id, kind, status, payload FROM proposals WHERE substr(created_at, 1, 10) >= ? "
        "AND substr(created_at, 1, 10) <= ?",
        (start, end),
    ):
        try:
            payload = json.loads(p["payload"] or "{}")
        except ValueError:
            continue
        if pid in _ids_in(payload):
            proposals.append({"proposal_id": p["id"], "kind": p["kind"], "status": p["status"]})
    return {"acted": bool(acted or proposals), "decisions": acted, "considered_in": considered, "proposals": proposals}


def grade_pending_ht(conn: sqlite3.Connection, today: date) -> list[dict[str, Any]]:
    """HT mode: grade each signal as an early trigger, not as a projection. Over `signals.grade_horizon_days` after
    first sight: (1) lead time until HT's ROS projection moved the same way (ht_revision signals are HT moving, so
    only their market lead counts) and until league % owned moved; (2) whether a decision/proposal involving the
    player followed within `signals.acted_days`; (3) the replay grade (decisions.result) of those decisions."""
    horizon, act_days = int(C("signals.grade_horizon_days")), int(C("signals.acted_days"))
    rows = conn.execute(
        "SELECT * FROM signals WHERE graded_at IS NULL AND first_seen_et <= ? ORDER BY first_seen_et",
        ((today - timedelta(days=horizon + 1)).isoformat(),),
    ).fetchall()
    graded = []
    for r in rows:
        seen = r["first_seen_et"]
        end = (date.fromisoformat(seen) + timedelta(days=horizon)).isoformat()
        pid, direction = r["espn_id"], r["direction"]
        result: dict[str, Any] = {"mode": "ht", "window_days": horizon}
        if r["kind"] == "ht_revision":
            result |= {"ht_lead_days": None, "ht_moved": None}
        else:
            result |= _ht_lead(conn, pid, direction, seen, end)
        result |= _market_lead(conn, pid, direction, r["first_seen"], end + "T23:59:59", r["pct_owned_at"])
        act_end = (date.fromisoformat(seen) + timedelta(days=act_days)).isoformat()
        result |= _acted(conn, pid, seen, act_end)
        conn.execute(
            "UPDATE signals SET graded_at = ?, result = ? WHERE id = ?",
            (_iso(datetime.now(UTC)), json.dumps(result, default=str), r["id"]),
        )
        graded.append({"signal_id": r["id"], "kind": r["kind"], "player": r["name"], **result})
    conn.commit()
    return graded


def _mean(xs: list[float]) -> float | None:
    return round(sum(xs) / len(xs), 2) if xs else None


def summary_ht(conn: sqlite3.Connection, season: int | None = None) -> dict[str, Any]:
    """Per signal kind: lead over HT and the market, how often FBM acted, and how acted-on signals graded in replay."""
    sql = "SELECT kind, result FROM signals WHERE graded_at IS NOT NULL"
    params: tuple = ()
    if season is not None:
        sql += " AND season = ?"
        params = (season,)
    by: dict[str, dict[str, list]] = {}
    for r in conn.execute(sql, params):
        res = json.loads(r["result"] or "{}")
        if res.get("mode") != "ht":
            continue
        b = by.setdefault(
            r["kind"], {k: [] for k in ("n", "ht", "ht_moved", "mkt", "mkt_moved", "acted", "regret", "cats")}
        )
        b["n"].append(1)
        if res.get("ht_moved") is not None:
            b["ht_moved"].append(1.0 if res["ht_moved"] else 0.0)
        if res.get("ht_lead_days") is not None:
            b["ht"].append(float(res["ht_lead_days"]))
        if res.get("market_moved") is not None:
            b["mkt_moved"].append(1.0 if res["market_moved"] else 0.0)
        if res.get("market_lead_days") is not None:
            b["mkt"].append(float(res["market_lead_days"]))
        b["acted"].append(1.0 if res.get("acted") else 0.0)
        for d in res.get("decisions") or []:
            rep = d.get("replay") or {}
            if rep.get("regret_cats") is not None:
                b["regret"].append(float(rep["regret_cats"]))
            if rep.get("recommended_cats_won") is not None:
                b["cats"].append(float(rep["recommended_cats_won"]))
    out = {
        kind: {
            "graded": len(b["n"]),
            "ht_caught_up_rate": _mean(b["ht_moved"]),
            "avg_lead_over_ht_days": _mean(b["ht"]),
            "market_moved_rate": _mean(b["mkt_moved"]),
            "avg_lead_over_market_days": _mean(b["mkt"]),
            "acted_rate": _mean(b["acted"]),
            "acted_replays_graded": len(b["regret"]),
            "avg_replay_regret_cats": _mean(b["regret"]),
            "avg_replay_cats_won": _mean(b["cats"]),
        }
        for kind, b in sorted(by.items())
    }
    return {
        "mode": "ht",
        "by_kind": out,
        "how_to_read": "Signals are early triggers for decisions, not projections (HT owns projection accuracy). "
        "avg_lead_over_ht_days / avg_lead_over_market_days: days from FBM's first sight until HT's ROS projection / "
        "league % owned moved the same way (ht_caught_up_rate / market_moved_rate: how often it happened within the "
        "window). acted_rate: share followed by a decision or proposal involving the player. avg_replay_*: replay "
        "grade of those decisions (empty until the replay grader has run).",
    }
