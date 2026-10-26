"""Decision log: every FBM decision with its situation, the options considered, what FBM recommended, what actually
happened, and the policy settings in force. The replay grader (fbm.gm.replay) grades these against real box scores
and Yahoo final results. Logging never changes what is recommended.

Option format: a list of dicts, each with a unique string `id` and a `label`; anything else the grader needs to
replay the option (player ids per day, adds/drops with dates, category weights, ...) goes in the same dict.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, date, datetime
from typing import Any

from fbm.gm import policy

KINDS = ("lineup", "chase_concede", "category_weights", "stream_plan", "waiver_claim", "il", "trade", "drop")
TRIGGERS = ("daily-gm", "lineup-guard", "owner-ask", "trade-scan", "weekly-plan", "migration", "other")
CHOSEN_SOURCES = ("yahoo_lineup", "proposal", "owner", "unknown")


class DecisionError(ValueError):
    pass


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def log(
    conn: sqlite3.Connection,
    *,
    kind: str,
    trigger: str,
    et_date: date | str,
    situation: dict[str, Any],
    options: list[dict[str, Any]],
    recommended: str | None = None,
    week: int | None = None,
    proposal_ref: str | None = None,
    created_at: str | None = None,
    policy_snapshot: tuple[int, dict[str, Any]] | None = None,
) -> int:
    """Insert one decision; returns its id. `policy_snapshot` defaults to the settings in force now."""
    if kind not in KINDS:
        raise DecisionError(f"Unknown decision kind '{kind}'. Use one of {KINDS}.")
    if trigger not in TRIGGERS:
        raise DecisionError(f"Unknown trigger '{trigger}'. Use one of {TRIGGERS}.")
    ids = [str(o.get("id", "")) for o in options]
    if not options or any(not i for i in ids) or len(set(ids)) != len(ids):
        raise DecisionError("options must be a non-empty list of dicts with unique, non-empty 'id'.")
    if recommended is not None and str(recommended) not in ids:
        raise DecisionError(f"recommended '{recommended}' is not one of the option ids {ids}.")
    version, values = policy_snapshot or policy.snapshot(conn)
    cur = conn.execute(
        "INSERT INTO decisions(created_at, et_date, week, kind, trigger, situation, options, recommended, "
        "policy_version, policy_params, proposal_ref) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            created_at or _now(),
            str(et_date),
            week,
            kind,
            trigger,
            json.dumps(situation, default=str),
            json.dumps(options, default=str),
            None if recommended is None else str(recommended),
            version,
            json.dumps(values, default=str),
            proposal_ref,
        ),
    )
    conn.commit()
    return int(cur.lastrowid)


def set_chosen(conn: sqlite3.Connection, decision_id: int, chosen: Any, source: str) -> None:
    """Record what actually happened (an option id, or a JSON-able description when it matched no option)."""
    if source not in CHOSEN_SOURCES:
        raise DecisionError(f"Unknown chosen source '{source}'. Use one of {CHOSEN_SOURCES}.")
    value = chosen if isinstance(chosen, str) else json.dumps(chosen, default=str)
    conn.execute("UPDATE decisions SET chosen = ?, chosen_source = ? WHERE id = ?", (value, source, decision_id))
    conn.commit()


def save_result(conn: sqlite3.Connection, decision_id: int, result: dict[str, Any]) -> None:
    conn.execute(
        "UPDATE decisions SET graded_at = ?, result = ? WHERE id = ?",
        (_now(), json.dumps(result, default=str), decision_id),
    )
    conn.commit()


def _decode(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    for k in ("situation", "options", "policy_params", "result"):
        d[k] = json.loads(d[k]) if d.get(k) else None
    return d


def get(conn: sqlite3.Connection, decision_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM decisions WHERE id = ?", (decision_id,)).fetchone()
    return _decode(row) if row else None


def query(
    conn: sqlite3.Connection,
    kind: str | None = None,
    since: str | None = None,
    until: str | None = None,
    graded: bool | None = None,
    limit: int = 500,
) -> list[dict[str, Any]]:
    where, args = [], []
    if kind:
        where.append("kind = ?")
        args.append(kind)
    if since:
        where.append("et_date >= ?")
        args.append(since)
    if until:
        where.append("et_date <= ?")
        args.append(until)
    if graded is True:
        where.append("graded_at IS NOT NULL")
    elif graded is False:
        where.append("graded_at IS NULL")
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    rows = conn.execute(f"SELECT * FROM decisions {clause} ORDER BY et_date, id LIMIT ?", (*args, limit)).fetchall()
    return [_decode(r) for r in rows]
