"""League-wide Yahoo reads: teams, standings, scoreboard, player lists (waivers/free agents), transactions.

Parsers take the dict from `xmlparse.parse` (XML: plural containers are always lists) and return plain dataclasses;
fetchers wrap the API paths. Everything here is read-only. Paths follow the Yahoo Fantasy Sports API v2 docs;
re-check them against live responses once API access is approved (raw responses are kept by the snapshot).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from fbm.yahoo.client import YahooClient
from fbm.yahoo.xmlparse import as_list, text

# Yahoo NBA stat ids -> engine stat columns (for banked weekly totals).
COUNT_STATS = {"10": "tpm", "12": "pts", "15": "reb", "16": "ast", "17": "stl", "18": "blk", "19": "tov"}
MADE_ATTEMPTED = {"9004003": ("fgm", "fga"), "9007006": ("ftm", "fta")}


def _int(value: Any, default: int | None = None) -> int | None:
    raw = text(value)
    try:
        return int(float(raw)) if raw not in (None, "", "-") else default
    except ValueError:
        return default


def _float(value: Any) -> float | None:
    raw = text(value)
    try:
        return float(raw) if raw not in (None, "", "-") else None
    except ValueError:
        return None


def _date(value: Any) -> date | None:
    raw = text(value)
    return date.fromisoformat(raw) if raw else None


def _league(fc: dict[str, Any]) -> dict[str, Any]:
    return fc.get("league") or {}


def _team_name(t: dict[str, Any]) -> str:
    return text(t.get("name"), "") or ""


def _manager(t: dict[str, Any]) -> tuple[str, bool]:
    managers = as_list((t.get("managers") or {}).get("manager"))
    if not managers:
        return "", False
    m = managers[0]
    return text(m.get("nickname"), "") or "", text(m.get("is_current_login"), "0") == "1"


# ------------------------------------------------------------------ teams


@dataclass(frozen=True)
class YahooTeam:
    team_key: str
    name: str
    manager: str
    is_mine: bool
    waiver_priority: int | None
    moves: int
    trades: int
    week_adds: int | None  # roster adds used in the current week


def parse_teams(fc: dict[str, Any]) -> list[YahooTeam]:
    out = []
    for t in as_list((_league(fc).get("teams") or {}).get("team")):
        manager, current = _manager(t)
        adds = t.get("roster_adds") or {}
        out.append(
            YahooTeam(
                team_key=text(t.get("team_key"), "") or "",
                name=_team_name(t),
                manager=manager,
                is_mine=text(t.get("is_owned_by_current_login"), "0") == "1" or current,
                waiver_priority=_int(t.get("waiver_priority")),
                moves=_int(t.get("number_of_moves"), 0) or 0,
                trades=_int(t.get("number_of_trades"), 0) or 0,
                week_adds=_int(adds.get("value")) if isinstance(adds, dict) else None,
            )
        )
    return out


def fetch_teams(client: YahooClient, league_key: str) -> list[YahooTeam]:
    return parse_teams(client.get(f"league/{league_key}/teams"))


# ------------------------------------------------------------------ standings


@dataclass(frozen=True)
class StandingRow:
    team_key: str
    name: str
    rank: int | None
    wins: int
    losses: int
    ties: int
    pct: float | None
    games_back: str
    playoff_seed: int | None


def parse_standings(fc: dict[str, Any]) -> list[StandingRow]:
    container = _league(fc).get("standings") or {}
    rows = []
    for t in as_list((container.get("teams") or {}).get("team")):
        st = t.get("team_standings") or {}
        totals = st.get("outcome_totals") or {}
        rows.append(
            StandingRow(
                team_key=text(t.get("team_key"), "") or "",
                name=_team_name(t),
                rank=_int(st.get("rank")),
                wins=_int(totals.get("wins"), 0) or 0,
                losses=_int(totals.get("losses"), 0) or 0,
                ties=_int(totals.get("ties"), 0) or 0,
                pct=_float(totals.get("percentage")),
                games_back=text(st.get("games_back"), "") or "",
                playoff_seed=_int(st.get("playoff_seed")),
            )
        )
    return sorted(rows, key=lambda r: (r.rank is None, r.rank or 0))


def fetch_standings(client: YahooClient, league_key: str) -> list[StandingRow]:
    return parse_standings(client.get(f"league/{league_key}/standings"))


# ------------------------------------------------------------------ scoreboard


@dataclass(frozen=True)
class MatchupTeam:
    team_key: str
    name: str
    stats: dict[str, str]  # Yahoo stat_id -> raw value ("45/98", ".459", "12")
    points: float | None  # categories won so far (H2H categories)


@dataclass(frozen=True)
class Matchup:
    week: int | None
    week_start: date | None
    week_end: date | None
    status: str  # preevent | midevent | postevent
    is_playoffs: bool
    teams: tuple[MatchupTeam, ...]
    stat_winners: dict[str, str | None] = field(default_factory=dict)  # stat_id -> winner team_key (None = tied)

    def team(self, team_key: str) -> MatchupTeam | None:
        return next((t for t in self.teams if t.team_key == team_key), None)

    def opponent_of(self, team_key: str) -> MatchupTeam | None:
        if not self.team(team_key):
            return None
        return next((t for t in self.teams if t.team_key != team_key), None)


def _team_stats(t: dict[str, Any]) -> dict[str, str]:
    stats = ((t.get("team_stats") or {}).get("stats") or {}).get("stat")
    return {text(s.get("stat_id"), "") or "": text(s.get("value"), "") or "" for s in as_list(stats)}


def parse_scoreboard(fc: dict[str, Any]) -> list[Matchup]:
    board = _league(fc).get("scoreboard") or {}
    out = []
    for m in as_list((board.get("matchups") or {}).get("matchup")):
        winners: dict[str, str | None] = {}
        for w in as_list((m.get("stat_winners") or {}).get("stat_winner")):
            tied = text(w.get("is_tied"), "0") == "1"
            winners[text(w.get("stat_id"), "") or ""] = None if tied else text(w.get("winner_team_key"))
        teams = tuple(
            MatchupTeam(
                team_key=text(t.get("team_key"), "") or "",
                name=_team_name(t),
                stats=_team_stats(t),
                points=_float((t.get("team_points") or {}).get("total")),
            )
            for t in as_list((m.get("teams") or {}).get("team"))
        )
        out.append(
            Matchup(
                week=_int(m.get("week")),
                week_start=_date(m.get("week_start")),
                week_end=_date(m.get("week_end")),
                status=text(m.get("status"), "") or "",
                is_playoffs=text(m.get("is_playoffs"), "0") == "1",
                teams=teams,
                stat_winners=winners,
            )
        )
    return out


def fetch_scoreboard(client: YahooClient, league_key: str, week: int | None = None) -> list[Matchup]:
    suffix = f";week={week}" if week else ""
    return parse_scoreboard(client.get(f"league/{league_key}/scoreboard{suffix}"))


def banked_stats(stats: dict[str, str]) -> dict[str, float]:
    """Weekly team totals (Yahoo stat ids) -> engine stat columns. Percentages come from makes/attempts."""
    out: dict[str, float] = {}
    for stat_id, (made, att) in MADE_ATTEMPTED.items():
        raw = stats.get(stat_id, "")
        if "/" in raw:
            m, a = raw.split("/", 1)
            out[made] = float(m) if m.strip() not in ("", "-") else 0.0
            out[att] = float(a) if a.strip() not in ("", "-") else 0.0
    for stat_id, col in COUNT_STATS.items():
        value = _float(stats.get(stat_id))
        if value is not None:
            out[col] = value
    return out


# ------------------------------------------------------------------ player lists


@dataclass(frozen=True)
class YahooPlayer:
    player_key: str
    player_id: str
    name: str
    nba_team: str
    display_position: str
    eligible: tuple[str, ...]
    status: str
    ownership_type: str  # freeagents | waivers | team
    owner_team_key: str
    waiver_date: date | None
    percent_owned: float | None
    percent_owned_delta: float | None


def _player_id(p: dict[str, Any]) -> str:
    pid = text(p.get("player_id"))
    if pid:
        return pid
    key = text(p.get("player_key"), "") or ""
    return key.rsplit(".p.", 1)[-1]


def parse_player(p: dict[str, Any]) -> YahooPlayer:
    name = p.get("name") or {}
    own = p.get("ownership") or {}
    pct = p.get("percent_owned") or {}
    return YahooPlayer(
        player_key=text(p.get("player_key"), "") or "",
        player_id=_player_id(p),
        name=(text(name.get("full"), "") if isinstance(name, dict) else text(name, "")) or "",
        nba_team=text(p.get("editorial_team_abbr"), "") or "",
        display_position=text(p.get("display_position"), "") or "",
        eligible=tuple(text(x, "") or "" for x in as_list((p.get("eligible_positions") or {}).get("position"))),
        status=text(p.get("status"), "") or "",
        ownership_type=text(own.get("ownership_type"), "") or "",
        owner_team_key=text(own.get("owner_team_key"), "") or "",
        waiver_date=_date(own.get("waiver_date")),
        percent_owned=_float(pct.get("value")) if isinstance(pct, dict) else None,
        percent_owned_delta=_float(pct.get("delta")) if isinstance(pct, dict) else None,
    )


def parse_players(fc: dict[str, Any]) -> list[YahooPlayer]:
    return [parse_player(p) for p in as_list((_league(fc).get("players") or {}).get("player"))]


def fetch_players(
    client: YahooClient, league_key: str, status: str = "W", limit: int = 100, page: int = 25
) -> list[YahooPlayer]:
    """Players by status (W = on waivers, FA = free agents, A = all available), with ownership (waiver dates)."""
    out: list[YahooPlayer] = []
    start = 0
    while start < limit:
        batch = parse_players(
            client.get(f"league/{league_key}/players;status={status};start={start};count={page}/ownership")
        )
        out.extend(batch)
        if len(batch) < page:
            break
        start += page
    return out[:limit]


# ------------------------------------------------------------------ transactions


@dataclass(frozen=True)
class TxPlayer:
    player_key: str
    player_id: str
    name: str
    nba_team: str
    action: str  # add | drop | trade
    source_type: str  # freeagents | waivers | team
    source_team_key: str
    destination_type: str  # team | waivers | freeagents
    destination_team_key: str


@dataclass(frozen=True)
class Transaction:
    key: str
    type: str  # add | drop | add/drop | trade | waiver | pending_trade
    status: str  # successful | pending | proposed | accepted | ...
    timestamp: datetime | None
    players: tuple[TxPlayer, ...]
    trader_team_key: str = ""  # trades: proposer
    tradee_team_key: str = ""  # trades: receiver
    trade_note: str = ""
    waiver_team_key: str = ""  # pending waiver claims


def parse_transactions(fc: dict[str, Any]) -> list[Transaction]:
    out = []
    for tx in as_list((_league(fc).get("transactions") or {}).get("transaction")):
        players = []
        for p in as_list((tx.get("players") or {}).get("player")):
            data = as_list(p.get("transaction_data"))
            d = data[0] if data else {}
            name = p.get("name") or {}
            players.append(
                TxPlayer(
                    player_key=text(p.get("player_key"), "") or "",
                    player_id=_player_id(p),
                    name=(text(name.get("full"), "") if isinstance(name, dict) else text(name, "")) or "",
                    nba_team=text(p.get("editorial_team_abbr"), "") or "",
                    action=text(d.get("type"), "") or "",
                    source_type=text(d.get("source_type"), "") or "",
                    source_team_key=text(d.get("source_team_key"), "") or "",
                    destination_type=text(d.get("destination_type"), "") or "",
                    destination_team_key=text(d.get("destination_team_key"), "") or "",
                )
            )
        stamp = _int(tx.get("timestamp"))
        out.append(
            Transaction(
                key=text(tx.get("transaction_key"), "") or "",
                type=text(tx.get("type"), "") or "",
                status=text(tx.get("status"), "") or "",
                timestamp=datetime.fromtimestamp(stamp, UTC) if stamp else None,
                players=tuple(players),
                trader_team_key=text(tx.get("trader_team_key"), "") or "",
                tradee_team_key=text(tx.get("tradee_team_key"), "") or "",
                trade_note=text(tx.get("trade_note"), "") or "",
                waiver_team_key=text(tx.get("waiver_team_key"), "") or "",
            )
        )
    return out


def fetch_transactions(client: YahooClient, league_key: str, count: int = 50, start: int = 0) -> list[Transaction]:
    """Newest first; `start` pages backwards through history."""
    paging = f";start={start}" if start else ""
    return parse_transactions(
        client.get(f"league/{league_key}/transactions;types=add,drop,trade{paging};count={count}")
    )


def fetch_pending(client: YahooClient, league_key: str, team_key: str) -> list[Transaction]:
    """My pending waiver claims and pending trades (offers I sent or received)."""
    path = f"league/{league_key}/transactions;types=waiver,pending_trade;team_key={team_key}"
    return parse_transactions(client.get(path))
