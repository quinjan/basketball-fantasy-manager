"""Map Yahoo players onto the engine's ESPN athlete ids (all valuations and simulations are keyed by ESPN id).

Matching order: manual override (config/yahoo_id_overrides.toml) -> exact normalized name (team breaks ties) ->
same team + same last name + same first initial (nicknames: "Nicolas"/"Nic") -> close spelling on the same team.
Yahoo's numeric player id (the part after ".p.") is stable across seasons; mappings are cached in SQLite.
"""

from __future__ import annotations

import difflib
import re
import sqlite3
import tomllib
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from fbm.config import CONFIG_DIR

OVERRIDES_PATH = CONFIG_DIR / "yahoo_id_overrides.toml"
SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}

# Yahoo editorial team abbreviations (several spellings seen across seasons) -> ESPN abbreviations.
_TEAM_ALIASES = {
    "GS": "GS",
    "GSW": "GS",
    "NY": "NY",
    "NYK": "NY",
    "NO": "NO",
    "NOP": "NO",
    "NOR": "NO",
    "SA": "SA",
    "SAS": "SA",
    "UTA": "UTAH",
    "UTAH": "UTAH",
    "WAS": "WSH",
    "WSH": "WSH",
    "PHO": "PHX",
    "PHX": "PHX",
    "BKN": "BKN",
    "BRK": "BKN",
    "BK": "BKN",
    "CHA": "CHA",
    "CHO": "CHA",
}


def espn_team(yahoo_abbr: str) -> str:
    abbr = (yahoo_abbr or "").strip().upper()
    return _TEAM_ALIASES.get(abbr, abbr)


def norm_name(name: str) -> str:
    ascii_name = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode()
    words = re.sub(r"[^a-z ]", " ", ascii_name.lower().replace(".", "").replace("'", "")).split()
    return " ".join(w for w in words if w not in SUFFIXES)


@dataclass(frozen=True)
class Match:
    yahoo_id: str
    espn_id: str | None
    method: str  # override | exact | exact+team | initial+team | fuzzy+team | unmatched
    name: str
    team: str


@dataclass(frozen=True)
class EspnPlayer:
    espn_id: str
    name: str
    team: str


def load_overrides(path: Path | None = None) -> dict[str, str]:
    p = path or OVERRIDES_PATH
    if not p.exists():
        return {}
    data = tomllib.loads(p.read_text(encoding="utf-8"))
    return {str(k): str(v) for k, v in (data.get("players") or {}).items()}


class IdMapper:
    def __init__(self, espn_players: Iterable[EspnPlayer], overrides: Mapping[str, str] | None = None):
        self.players = list(espn_players)
        self.overrides = dict(overrides or {})
        self._by_name: dict[str, list[EspnPlayer]] = {}
        for p in self.players:
            self._by_name.setdefault(norm_name(p.name), []).append(p)

    def match(self, yahoo_id: str, name: str, yahoo_team: str) -> Match:
        team = espn_team(yahoo_team)
        if yahoo_id in self.overrides:
            return Match(yahoo_id, self.overrides[yahoo_id], "override", name, team)
        key = norm_name(name)
        exact = self._by_name.get(key, [])
        if len(exact) == 1:
            return Match(yahoo_id, exact[0].espn_id, "exact", name, team)
        if len(exact) > 1:
            same = [p for p in exact if p.team == team]
            if len(same) == 1:
                return Match(yahoo_id, same[0].espn_id, "exact+team", name, team)
            return Match(yahoo_id, None, "unmatched", name, team)
        parts = key.split()
        on_team = [p for p in self.players if p.team == team]
        if parts:
            first, last = parts[0], parts[-1]
            initial = [
                p for p in on_team if (n := norm_name(p.name).split()) and n[-1] == last and n[0][:1] == first[:1]
            ]
            if len(initial) == 1:
                return Match(yahoo_id, initial[0].espn_id, "initial+team", name, team)
        names = {norm_name(p.name): p for p in on_team}
        close = difflib.get_close_matches(key, list(names), n=2, cutoff=0.85)
        if len(close) == 1:
            return Match(yahoo_id, names[close[0]].espn_id, "fuzzy+team", name, team)
        return Match(yahoo_id, None, "unmatched", name, team)


SCHEMA = """
CREATE TABLE IF NOT EXISTS yahoo_players (
    yahoo_id TEXT PRIMARY KEY,
    espn_id TEXT,
    name TEXT NOT NULL,
    team TEXT,
    method TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


def espn_players_from_db(conn: sqlite3.Connection) -> list[EspnPlayer]:
    """Every ESPN athlete we know (current team from the players table, else latest season averages)."""
    rows = conn.execute(
        """
        SELECT p.espn_id, p.name, COALESCE(NULLIF(p.team, ''), s.team, '') AS team
        FROM players p
        LEFT JOIN (SELECT espn_id, team, MAX(season) FROM season_averages GROUP BY espn_id) s USING (espn_id)
        UNION
        SELECT m.espn_id, m.name, '' FROM player_meta m WHERE m.espn_id NOT IN (SELECT espn_id FROM players)
        """
    ).fetchall()
    return [EspnPlayer(str(r[0]), str(r[1] or ""), str(r[2] or "")) for r in rows if r[1]]


def save_matches(conn: sqlite3.Connection, matches: Iterable[Match]) -> None:
    conn.executescript(SCHEMA)
    now = datetime.now(UTC).isoformat(timespec="seconds")
    conn.executemany(
        "INSERT OR REPLACE INTO yahoo_players (yahoo_id, espn_id, name, team, method, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [(m.yahoo_id, m.espn_id, m.name, m.team, m.method, now) for m in matches],
    )
    conn.commit()


def unmatched(conn: sqlite3.Connection) -> list[dict[str, str]]:
    conn.executescript(SCHEMA)
    rows = conn.execute("SELECT yahoo_id, name, team FROM yahoo_players WHERE espn_id IS NULL ORDER BY name")
    return [{"yahoo_id": r[0], "name": r[1], "team": r[2]} for r in rows]
