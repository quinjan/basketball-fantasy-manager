"""Yahoo draft data (read-only): league draft results and Yahoo average draft position (ADP).

API paths (GET):
- league/{league_key}/draftresults/players   -> draft_result: pick, round, team_key, player_key (+ player info)
- league/{league_key}/players;sort=AR;start={s};count={n}/draft_analysis -> average_pick, average_round,
  average_cost, percent_drafted (paged)

Yahoo players are mapped onto ESPN ids with `IdMapper`. Yahoo ADP is cached in SQLite (`yahoo_adp`); the draft
board prefers it (the league's own platform) and falls back to ESPN ADP.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from fbm.yahoo.ids import IdMapper
from fbm.yahoo.xmlparse import as_list, text


def _int(v: Any) -> int | None:
    raw = text(v)
    try:
        return int(raw) if raw not in (None, "", "-") else None
    except ValueError:
        return None


def _float(v: Any) -> float | None:
    raw = text(v)
    try:
        return float(raw) if raw not in (None, "", "-") else None
    except ValueError:
        return None


def _pid_from_key(key: str) -> str:
    return key.rsplit(".p.", 1)[-1] if key else ""


def _player_bits(p: dict[str, Any]) -> tuple[str, str, str]:
    name = p.get("name") or {}
    full = (text(name.get("full"), "") if isinstance(name, dict) else text(name, "")) or ""
    key = text(p.get("player_key"), "") or ""
    pid = text(p.get("player_id")) or _pid_from_key(key)
    return pid, full, text(p.get("editorial_team_abbr"), "") or ""


# ------------------------------------------------------------------ draft results


@dataclass(frozen=True)
class DraftResult:
    pick: int
    round: int
    team_key: str
    player_key: str  # "" for slots not yet picked
    player_id: str
    name: str = ""
    nba_team: str = ""


def parse_draftresults(fc: dict[str, Any]) -> list[DraftResult]:
    league = fc.get("league") or {}
    if isinstance(league, list):
        league = league[0]
    out = []
    for d in as_list((league.get("draft_results") or {}).get("draft_result")):
        pick = _int(d.get("pick"))
        if pick is None:
            continue
        player = d.get("player") or {}
        if isinstance(player, list):
            player = player[0] if player else {}
        pid, name, team = _player_bits(player) if player else ("", "", "")
        key = text(d.get("player_key"), "") or text(player.get("player_key"), "") or ""
        out.append(
            DraftResult(
                pick=pick,
                round=_int(d.get("round")) or 0,
                team_key=text(d.get("team_key"), "") or "",
                player_key=key,
                player_id=pid or _pid_from_key(key),
                name=name,
                nba_team=team,
            )
        )
    return sorted(out, key=lambda r: r.pick)


def fetch_draftresults(client: Any, league_key: str) -> list[DraftResult]:
    return parse_draftresults(client.get(f"league/{league_key}/draftresults/players"))


# ------------------------------------------------------------------ ADP


@dataclass(frozen=True)
class YahooAdp:
    player_key: str
    player_id: str
    name: str
    nba_team: str
    average_pick: float | None
    average_round: float | None
    average_cost: float | None
    percent_drafted: float | None


def parse_draft_analysis(fc: dict[str, Any]) -> list[YahooAdp]:
    league = fc.get("league") or {}
    if isinstance(league, list):
        league = league[0]
    out = []
    for p in as_list((league.get("players") or {}).get("player")):
        pid, name, team = _player_bits(p)
        da = p.get("draft_analysis") or {}
        pct = _float(da.get("percent_drafted"))
        out.append(
            YahooAdp(
                player_key=text(p.get("player_key"), "") or "",
                player_id=pid,
                name=name,
                nba_team=team,
                average_pick=_float(da.get("average_pick")),
                average_round=_float(da.get("average_round")),
                average_cost=_float(da.get("average_cost")),
                percent_drafted=None if pct is None else (pct * 100 if pct <= 1 else pct),
            )
        )
    return out


def fetch_adp(client: Any, league_key: str, limit: int = 300, page: int = 25) -> list[YahooAdp]:
    out: list[YahooAdp] = []
    start = 0
    while start < limit:
        batch = parse_draft_analysis(
            client.get(f"league/{league_key}/players;sort=AR;start={start};count={page}/draft_analysis")
        )
        out.extend(batch)
        if len(batch) < page:
            break
        start += page
    return out[:limit]


# ------------------------------------------------------------------ cache


SCHEMA = """
CREATE TABLE IF NOT EXISTS yahoo_adp (
    yahoo_id TEXT PRIMARY KEY,
    espn_id TEXT,
    name TEXT,
    average_pick REAL,
    average_round REAL,
    average_cost REAL,
    percent_drafted REAL,
    updated_at TEXT NOT NULL
);
"""


def save_adp(conn: sqlite3.Connection, rows: Iterable[YahooAdp], mapper: IdMapper) -> dict[str, int]:
    conn.executescript(SCHEMA)
    now = datetime.now(UTC).isoformat(timespec="seconds")
    rows = list(rows)
    data, matched = [], 0
    for r in rows:
        espn_id = mapper.match(r.player_id, r.name, r.nba_team).espn_id
        matched += espn_id is not None
        data.append(
            (r.player_id, espn_id, r.name, r.average_pick, r.average_round, r.average_cost, r.percent_drafted, now)
        )
    conn.execute("DELETE FROM yahoo_adp")
    conn.executemany("INSERT OR REPLACE INTO yahoo_adp VALUES (?,?,?,?,?,?,?,?)", data)
    conn.commit()
    return {"players": len(data), "mapped": matched, "unmapped": len(data) - matched}


def yahoo_adp_map(conn: sqlite3.Connection) -> dict[str, float]:
    conn.executescript(SCHEMA)
    rows = conn.execute("SELECT espn_id, average_pick FROM yahoo_adp WHERE espn_id IS NOT NULL AND average_pick > 0")
    return {str(r[0]): float(r[1]) for r in rows}


# ------------------------------------------------------------------ live sync


def draft_params(results: Iterable[DraftResult], my_team_key: str) -> tuple[int, int, int]:
    """(teams, my slot, rounds) from Yahoo's draft order; (0, 0, 0) when Yahoo has no order yet."""
    results = list(results)
    round1 = sorted((r for r in results if r.round == 1), key=lambda r: r.pick)
    if not round1:
        return 0, 0, 0
    teams = len(round1)
    slot = next((i + 1 for i, r in enumerate(round1) if r.team_key == my_team_key), 1)
    rounds = max(r.round for r in results)
    return teams, slot, rounds


def apply_results(
    state: Any,
    results: Iterable[DraftResult],
    resolve: Callable[[DraftResult], tuple[str, str]],
    my_team_key: str = "",
) -> list[dict[str, Any]]:
    """Record every Yahoo pick not yet in `state` (in pick order). Idempotent: picks already recorded are skipped.

    `resolve` maps a result to (engine player id, name); unmapped players get a "yahoo:<id>" id so draft order
    and turn tracking stay correct.
    """
    new = []
    for r in sorted(results, key=lambda x: x.pick):
        if not r.player_key and not r.player_id:
            break  # first open slot: everything after is unpicked
        if r.pick < state.next_overall:
            continue
        if r.pick > state.next_overall or state.done:
            break  # gap (should not happen) — stop rather than misalign
        pid, name = resolve(r)
        if pid in state.taken():
            pid = f"yahoo:{r.player_id}"
        pick = state.record(pid, name)
        new.append(
            {
                "overall": pick.overall,
                "round": r.round,
                "team_key": r.team_key,
                "player": name,
                "id": pid,
                "mapped": not pid.startswith("yahoo:"),
                "mine": (r.team_key == my_team_key) if my_team_key else pick.team_slot == state.my_slot,
            }
        )
    return new
