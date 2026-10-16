"""ESPN Fantasy (public, no auth): average draft position, rankings, and positional eligibility.

ESPN fantasy player ids are the same as ESPN athlete ids used everywhere else in FBM.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any

from fbm.data.http import Fetcher
from fbm.store import db

URL = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/fba/seasons/{season}/segments/0/leaguedefaults/3"
SLOT_TO_POSITION = {0: "PG", 1: "SG", 2: "SF", 3: "PF", 4: "C", 5: "G", 6: "F"}
POSITION_ORDER = ("PG", "SG", "G", "SF", "PF", "F", "C")


@dataclass(frozen=True)
class FantasyMeta:
    espn_id: str
    name: str
    adp: float | None
    rank_standard: int | None
    rank_roto: int | None
    eligible: tuple[str, ...]  # Yahoo-style positions + UTIL


def eligibility_from_slots(slots: list[int]) -> tuple[str, ...]:
    positions = {SLOT_TO_POSITION[s] for s in slots if s in SLOT_TO_POSITION}
    return (*[p for p in POSITION_ORDER if p in positions], "UTIL")


def parse_players(payload: dict[str, Any]) -> list[FantasyMeta]:
    out = []
    for entry in payload.get("players", []):
        pl = entry.get("player") or {}
        ranks = pl.get("draftRanksByRankType") or {}
        adp = (pl.get("ownership") or {}).get("averageDraftPosition")
        out.append(
            FantasyMeta(
                espn_id=str(entry.get("id") or pl.get("id")),
                name=pl.get("fullName", ""),
                adp=float(adp) if adp else None,
                rank_standard=(ranks.get("STANDARD") or {}).get("rank"),
                rank_roto=(ranks.get("ROTO") or {}).get("rank"),
                eligible=eligibility_from_slots(pl.get("eligibleSlots") or []),
            )
        )
    return out


def fetch(fetcher: Fetcher, season: int, limit: int = 400, ttl: float = 86400) -> list[FantasyMeta]:
    flt = {"players": {"limit": limit, "sortDraftRanks": {"sortPriority": 100, "sortAsc": True, "value": "STANDARD"}}}
    payload = fetcher.get_json(
        URL.format(season=season),
        params={"view": "kona_player_info"},
        ttl=ttl,
        headers={"X-Fantasy-Filter": json.dumps(flt)},
    )
    return parse_players(payload)


def store(conn: sqlite3.Connection, season: int, metas: list[FantasyMeta]) -> int:
    stamp = db.now_iso()
    conn.executemany(
        "INSERT OR REPLACE INTO player_meta(espn_id, season, name, adp, rank_standard, rank_roto, eligible, "
        "updated_at) VALUES (?,?,?,?,?,?,?,?)",
        [(m.espn_id, season, m.name, m.adp, m.rank_standard, m.rank_roto, ",".join(m.eligible), stamp) for m in metas],
    )
    db.log_sync(conn, f"fantasy_meta:{season}", f"{len(metas)} players")
    conn.commit()
    return len(metas)


def eligibility_map(conn: sqlite3.Connection) -> dict[str, tuple[str, ...]]:
    return {
        r["espn_id"]: tuple(r["eligible"].split(",")) for r in conn.execute("SELECT espn_id, eligible FROM player_meta")
    }


def adp_map(conn: sqlite3.Connection) -> dict[str, float]:
    return {r["espn_id"]: r["adp"] for r in conn.execute("SELECT espn_id, adp FROM player_meta WHERE adp IS NOT NULL")}
