"""Daily roster reads for NBA (coverage_type=date, Eastern game days)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from fbm.yahoo.client import YahooClient
from fbm.yahoo.xmlparse import as_list, text


@dataclass(frozen=True)
class RosterPlayer:
    player_key: str
    name: str
    nba_team: str
    display_position: str
    eligible_positions: tuple[str, ...]
    selected_position: str
    status: str
    is_editable: bool


@dataclass(frozen=True)
class Roster:
    team_key: str
    day: date
    is_editable: bool
    players: tuple[RosterPlayer, ...] = field(default_factory=tuple)

    def assignments(self) -> dict[str, str]:
        return {p.player_key: p.selected_position for p in self.players}


def _player(p: dict[str, Any]) -> RosterPlayer:
    name = p.get("name") or {}
    eligible = tuple(text(x, "") for x in as_list((p.get("eligible_positions") or {}).get("position")))
    selected = text((p.get("selected_position") or {}).get("position"), "")
    editable_raw = text(p.get("is_editable"))
    return RosterPlayer(
        player_key=text(p.get("player_key"), ""),
        name=text(name.get("full"), "") if isinstance(name, dict) else text(name, ""),
        nba_team=text(p.get("editorial_team_abbr"), ""),
        display_position=text(p.get("display_position"), ""),
        eligible_positions=eligible,
        selected_position=selected,
        status=text(p.get("status"), "") or "",
        is_editable=editable_raw is None or editable_raw == "1",
    )


def parse_roster(fc: dict[str, Any], team_key: str, day: date) -> Roster:
    roster = (fc.get("team") or {}).get("roster") or {}
    players = as_list((roster.get("players") or {}).get("player"))
    return Roster(
        team_key=team_key,
        day=day,
        is_editable=text(roster.get("is_editable"), "0") == "1",
        players=tuple(_player(p) for p in players),
    )


def get_roster(client: YahooClient, team_key: str, day: date) -> Roster:
    fc = client.get(f"team/{team_key}/roster;date={day.isoformat()}")
    return parse_roster(fc, team_key, day)
