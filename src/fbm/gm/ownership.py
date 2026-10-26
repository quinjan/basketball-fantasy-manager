"""Yahoo percent-owned history and in-league add/drop moves.

Yahoo only gives a *weekly* percent-owned delta (often 0 early in a week), so every league snapshot (and the daily
sync) appends `(player, observed_at, pct_owned, owner)` to `ownership_history`; deltas over a configurable window come
from that history. A value that has not changed is re-recorded at most every `ownership.record_gap_hours`, and a
player who leaves the available pool gets a row with the team that rostered him (his last known percent stays).

The history table is created lazily (additive, `CREATE TABLE IF NOT EXISTS`); pure helpers (`trend`, `recent_moves`)
take plain data so they are unit-testable.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from fbm.constants import C
from fbm.league.model import LeagueState
from fbm.store.db import now_iso
from fbm.yahoo.league_reads import Transaction

SCHEMA = """
CREATE TABLE IF NOT EXISTS ownership_history (
    player_id TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    pct_owned REAL,
    yahoo_delta REAL,
    owner_team_key TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (player_id, observed_at)
);
CREATE INDEX IF NOT EXISTS ix_ownership_history_at ON ownership_history(observed_at);
"""


def ensure(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


def _dt(stamp: str) -> datetime:
    d = datetime.fromisoformat(stamp)
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def _iso(stamp: str) -> str:
    return _dt(stamp).astimezone(UTC).isoformat(timespec="seconds")


@dataclass(frozen=True)
class Obs:
    at: datetime
    pct: float | None
    owner: str  # "" = available (free agent / waivers); else the rostering team key


@dataclass(frozen=True)
class Trend:
    current: float  # last known percent owned
    delta: float | None  # None until the history spans `ownership.min_span_hours`
    span_days: float
    partial: bool  # history is shorter than the requested window (delta covers `span_days` only)
    owner: str  # team key now rostering him, "" while available


# ------------------------------------------------------------------ recording


def _last_rows(conn: sqlite3.Connection) -> dict[str, Obs]:
    rows = conn.execute(
        """SELECT h.player_id, h.observed_at, h.pct_owned, h.owner_team_key FROM ownership_history h
           JOIN (SELECT player_id, MAX(observed_at) AS at FROM ownership_history GROUP BY player_id) m
             ON m.player_id = h.player_id AND m.at = h.observed_at"""
    )
    return {r[0]: Obs(_dt(r[1]), r[2], r[3] or "") for r in rows}


def record(conn: sqlite3.Connection, league: LeagueState, observed_at: str | None = None) -> int:
    """Append this snapshot's percent-owned (available players) and newly rostered players. Returns rows written."""
    ensure(conn)
    stamp = _iso(observed_at or league.fetched_at or now_iso())
    at, gap = _dt(stamp), timedelta(hours=float(C("ownership.record_gap_hours")))
    last = _last_rows(conn)
    rostered = {p.player_id: t.team_key for t in league.teams for p in t.players}
    rows: list[tuple[Any, ...]] = []
    for pid, own in league.ownership.items():
        pct = own[0] if own else None
        if pct is None or pid.startswith("yahoo:") or pid in rostered:
            continue
        prev = last.get(pid)
        if prev and (at <= prev.at or (prev.pct == pct and prev.owner == "" and at - prev.at < gap)):
            continue
        rows.append((pid, stamp, float(pct), own[1] if len(own) > 1 else None, ""))
    for pid, team_key in rostered.items():
        prev = last.get(pid)
        if prev and at > prev.at and prev.owner != team_key:  # he was tracked as available and is now rostered
            rows.append((pid, stamp, None, None, team_key))
    conn.executemany(
        "INSERT OR IGNORE INTO ownership_history(player_id, observed_at, pct_owned, yahoo_delta, owner_team_key) "
        "VALUES (?,?,?,?,?)",
        rows,
    )
    cutoff = _iso((datetime.now(UTC) - timedelta(days=int(C("ownership.retention_days")))).isoformat())
    conn.execute("DELETE FROM ownership_history WHERE observed_at < ?", (cutoff,))
    conn.commit()
    return len(rows)


# ------------------------------------------------------------------ trends


def load_history(conn: sqlite3.Connection) -> dict[str, list[Obs]]:
    ensure(conn)
    out: dict[str, list[Obs]] = defaultdict(list)
    for r in conn.execute(
        "SELECT player_id, observed_at, pct_owned, owner_team_key FROM ownership_history ORDER BY observed_at"
    ):
        out[r[0]].append(Obs(_dt(r[1]), r[2], r[3] or ""))
    return out


def trend(obs: Sequence[Obs], window_days: float, now: datetime | None = None) -> Trend | None:
    """Percent-owned change over `window_days` ending at `now` (the latest snapshot; the day he was rostered if he
    left the pool). The baseline is the last observation at or before the window start; with a shorter history it is
    the oldest observation (`partial`)."""
    known = [o for o in obs if o.pct is not None]
    if not known:
        return None
    cur = known[-1]
    left = obs[-1].pct is None
    end = obs[-1].at if left else (now or cur.at)
    start = end - timedelta(days=window_days)
    before = [o for o in known if o.at <= start]
    base = before[-1] if before else known[0]
    span = (end - base.at).total_seconds() / 86400
    delta = None
    if span * 24 >= float(C("ownership.min_span_hours")):
        delta = round(float(cur.pct) - float(base.pct), 1)  # type: ignore[arg-type]
    return Trend(float(cur.pct), delta, round(span, 2), not before, obs[-1].owner)  # type: ignore[arg-type]


def snapshot_bounds(history: Mapping[str, Sequence[Obs]]) -> tuple[datetime | None, datetime | None]:
    times = [o.at for obs in history.values() for o in obs]
    return (min(times), max(times)) if times else (None, None)


def trend_map(conn: sqlite3.Connection, windows: Iterable[int]) -> dict[str, dict[int, Trend]]:
    history = load_history(conn)
    _, now = snapshot_bounds(history)
    out: dict[str, dict[int, Trend]] = {}
    for pid, obs in history.items():
        by_window = {w: t for w in windows if (t := trend(obs, w, now)) is not None}
        if by_window:
            out[pid] = by_window
    return out


def annotate(conn: sqlite3.Connection, rows: list[dict[str, Any]], league: LeagueState) -> None:
    """Set pct_owned / pct_owned_delta (history over `ownership.delta_days`, else Yahoo's weekly delta) and
    pct_owned_delta_trend (history over `ownership.trend_days`) on player rows with a known percent owned."""
    short, long_ = int(C("ownership.delta_days")), int(C("ownership.trend_days"))
    try:
        trends = trend_map(conn, (short, long_))
    except Exception:  # history is optional context: fall back to Yahoo's own delta
        trends = {}
    for row in rows:
        own = league.ownership.get(row["id"])
        if not own:
            continue
        by = trends.get(row["id"], {})
        t_short, t_long = by.get(short), by.get(long_)
        row["pct_owned"] = own[0]
        if t_short is not None and t_short.delta is not None:
            row["pct_owned_delta"], row["pct_owned_delta_basis"] = t_short.delta, f"history_{short}d"
        else:
            row["pct_owned_delta"], row["pct_owned_delta_basis"] = own[1], "yahoo_week"
        row["pct_owned_delta_trend"] = t_long.delta if t_long is not None else None


# ------------------------------------------------------------------ in-league adds / drops


def yahoo_to_espn(conn: sqlite3.Connection) -> dict[str, str]:
    try:
        return {
            str(r[0]): str(r[1])
            for r in conn.execute("SELECT yahoo_id, espn_id FROM yahoo_players WHERE espn_id IS NOT NULL")
        }
    except sqlite3.OperationalError:  # no Yahoo player mapping yet (demo league)
        return {}


def recent_moves(
    txs: Iterable[Transaction],
    team_names: Mapping[str, str],
    now: datetime,
    days: float,
    espn_ids: Mapping[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Successful adds/drops by league teams in the last `days` (newest first). Trades are not included."""
    cutoff = now - timedelta(days=days)
    espn_ids = espn_ids or {}
    moves = []
    for tx in txs:
        if tx.type not in ("add", "drop", "add/drop") or tx.status != "successful" or not tx.timestamp:
            continue
        if tx.timestamp < cutoff:
            continue
        for p in tx.players:
            if p.action == "add":
                key, via = p.destination_team_key, p.source_type
            elif p.action == "drop":
                key, via = p.source_team_key, p.destination_type
            else:
                continue
            moves.append(
                {
                    "time_utc": tx.timestamp.isoformat(timespec="minutes"),
                    "team": team_names.get(key, key or "?"),
                    "team_key": key,
                    "action": p.action,
                    "player": p.name,
                    "id": espn_ids.get(p.player_id),
                    "nba": p.nba_team,
                    "via": via,  # adds: freeagents | waivers; drops: where he went
                }
            )
    return sorted(moves, key=lambda m: m["time_utc"], reverse=True)


def summarize_moves(moves: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    adds, drops = [m for m in moves if m["action"] == "add"], [m for m in moves if m["action"] == "drop"]
    by_team: dict[str, dict[str, int]] = defaultdict(lambda: {"adds": 0, "drops": 0})
    for m in moves:
        by_team[m["team"]]["adds" if m["action"] == "add" else "drops"] += 1
    return {
        "adds": adds,
        "drops": drops,
        "by_team": dict(sorted(by_team.items(), key=lambda kv: -(kv[1]["adds"] + kv[1]["drops"]))),
    }
