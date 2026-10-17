"""ESPN Fantasy (public, no auth): average draft position, rankings, positional eligibility, and season projections.

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


# ------------------------------------------------------------------ season projections

# ESPN fantasy stat ids (averageStats of the season projection split) -> FBM per-game columns.
PROJ_STAT_IDS = {
    "pts": "0",
    "blk": "1",
    "stl": "2",
    "ast": "3",
    "reb": "6",
    "tov": "11",
    "fgm": "13",
    "fga": "14",
    "ftm": "15",
    "fta": "16",
    "tpm": "17",
    "tpa": "18",
    "minutes": "40",
}
GP_STAT_ID = "42"
DEFAULT_POSITION = {1: "PG", 2: "SG", 3: "SF", 4: "PF", 5: "C"}
PROJ_COLUMNS = ("gp", *PROJ_STAT_IDS)


@dataclass(frozen=True)
class ProjectionLine:
    espn_id: str
    name: str
    team: str
    position: str
    stats: dict[str, float]  # gp + per-game PROJ_STAT_IDS keys


def parse_projections(payload: dict[str, Any], season: int, team_abbr: dict[str, str]) -> list[ProjectionLine]:
    """ESPN's season projection per player (statSourceId 1, split 0); players without one are skipped."""
    out = []
    for entry in payload.get("players", []):
        pl = entry.get("player") or {}
        split = next(
            (
                s
                for s in pl.get("stats") or []
                if s.get("seasonId") == season and s.get("statSourceId") == 1 and s.get("statSplitTypeId") == 0
            ),
            None,
        )
        avg = (split or {}).get("averageStats") or {}
        gp = float(avg.get(GP_STAT_ID) or 0)
        minutes = float(avg.get(PROJ_STAT_IDS["minutes"]) or 0)
        if gp <= 0 or minutes <= 0:
            continue
        stats = {"gp": gp, **{k: float(avg.get(sid) or 0.0) for k, sid in PROJ_STAT_IDS.items()}}
        out.append(
            ProjectionLine(
                espn_id=str(entry.get("id") or pl.get("id")),
                name=pl.get("fullName", ""),
                team=team_abbr.get(str(pl.get("proTeamId")), "FA"),
                position=DEFAULT_POSITION.get(pl.get("defaultPositionId"), ""),
                stats=stats,
            )
        )
    return out


def fetch_projections(
    fetcher: Fetcher, season: int, team_abbr: dict[str, str], limit: int = 1000, ttl: float = 6 * 3600
) -> list[ProjectionLine]:
    flt = {
        "players": {
            "limit": limit,
            "sortDraftRanks": {"sortPriority": 100, "sortAsc": True, "value": "STANDARD"},
            "filterStatsForSourceIds": {"value": [1]},
            "filterStatsForSplitTypeIds": {"value": [0]},
            "filterStatsForExternalIds": {"value": [season]},
        }
    }
    payload = fetcher.get_json(
        URL.format(season=season),
        params={"view": "kona_player_info"},
        ttl=ttl,
        headers={"X-Fantasy-Filter": json.dumps(flt)},
    )
    return parse_projections(payload, season, team_abbr)


def store_projections(conn: sqlite3.Connection, season: int, snapshot_date: str, lines: list[ProjectionLine]) -> int:
    """Save one snapshot per ET day (a later download the same day replaces it)."""
    stamp = db.now_iso()
    cols = ("season", "snapshot_date", "espn_id", "name", "team", "position", *PROJ_COLUMNS, "fetched_at")
    conn.executemany(
        f"INSERT OR REPLACE INTO espn_projections({', '.join(cols)}) VALUES ({','.join('?' * len(cols))})",
        [
            (season, snapshot_date, p.espn_id, p.name, p.team, p.position, *[p.stats[c] for c in PROJ_COLUMNS], stamp)
            for p in lines
        ],
    )
    db.log_sync(conn, f"espn_projections:{season}", f"{len(lines)} players ({snapshot_date})")
    conn.commit()
    return len(lines)
