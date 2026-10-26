"""Map Hashtag Basketball player ids (the number in `/9196/player`) onto FBM's ESPN athlete ids.

Same matching as `fbm.yahoo.ids` (exact normalized name, team breaks ties, initial + last name on the same team,
close spelling on the same team), plus manual overrides in `config/hashtag_id_overrides.toml`:

    [players]
    "9196" = "3112335"   # HT id = ESPN id

Results are kept in `hashtag_players` (match_method exact | fuzzy | override | unmatched).
"""

from __future__ import annotations

import sqlite3
import tomllib
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fbm.config import CONFIG_DIR
from fbm.yahoo.ids import IdMapper, espn_players_from_db, espn_team

OVERRIDES_PATH = CONFIG_DIR / "hashtag_id_overrides.toml"
_METHOD = {
    "override": "override",
    "exact": "exact",
    "exact+team": "exact",
    "initial+team": "fuzzy",
    "fuzzy+team": "fuzzy",
    "unmatched": "unmatched",
}


def load_overrides(path: Path | None = None) -> dict[str, str]:
    p = path or OVERRIDES_PATH
    if not p.exists():
        return {}
    data = tomllib.loads(p.read_text(encoding="utf-8"))
    return {str(k): str(v) for k, v in (data.get("players") or {}).items()}


def map_players(
    conn: sqlite3.Connection,
    players: Iterable[Mapping[str, Any]],
    overrides: Mapping[str, str] | None = None,
    mapper: IdMapper | None = None,
    commit: bool = True,
) -> dict[str, str | None]:
    """Match {ht_id, name, team} rows to ESPN ids, persist them in hashtag_players, return ht_id -> espn_id."""
    if mapper is None:
        mapper = IdMapper(espn_players_from_db(conn), load_overrides() if overrides is None else overrides)
    now = datetime.now(UTC).isoformat(timespec="seconds")
    out: dict[str, str | None] = {}
    rows = []
    for p in players:
        ht_id = str(p["ht_id"])
        if ht_id in out:
            continue
        m = mapper.match(ht_id, str(p.get("name") or ""), espn_team(str(p.get("team") or "")))
        out[ht_id] = m.espn_id
        rows.append((ht_id, m.espn_id, p.get("name") or "", p.get("team") or "", _METHOD.get(m.method, "fuzzy"), now))
    conn.executemany(
        "INSERT OR REPLACE INTO hashtag_players (ht_id, espn_id, name, team, match_method, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        rows,
    )
    if commit:
        conn.commit()
    return out


def lookup(conn: sqlite3.Connection, ht_ids: Iterable[str]) -> dict[str, str | None]:
    ids = list(dict.fromkeys(str(i) for i in ht_ids))
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    rows = conn.execute(f"SELECT ht_id, espn_id FROM hashtag_players WHERE ht_id IN ({marks})", ids).fetchall()
    return {r[0]: r[1] for r in rows}


def ht_for_espn(conn: sqlite3.Connection, espn_id: str) -> dict[str, Any] | None:
    """{ht_id, name, team} (HT's team abbreviation) for an ESPN id, or None."""
    r = conn.execute(
        "SELECT ht_id, name, team FROM hashtag_players WHERE espn_id = ? ORDER BY updated_at DESC LIMIT 1",
        (str(espn_id),),
    ).fetchone()
    return dict(r) if r else None


def unmatched_players(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT ht_id, name, team FROM hashtag_players WHERE espn_id IS NULL ORDER BY name").fetchall()
    return [dict(r) for r in rows]


def name_mapper(conn: sqlite3.Connection) -> IdMapper:
    """An ESPN mapper for players that HT shows without an id (notes): key them by name."""
    return IdMapper(espn_players_from_db(conn), {})
