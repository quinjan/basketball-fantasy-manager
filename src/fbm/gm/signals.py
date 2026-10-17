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

from fbm.analytics.projection_calibration import played_lines, window_actuals
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
    """Grade signals whose window has finished. Returns the newly graded ones."""
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
