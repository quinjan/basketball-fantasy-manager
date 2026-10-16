"""Source-agnostic league model. Yahoo (or the demo league) is mapped into these types."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from fbm.analytics.categories import NINE_CAT, Category

INACTIVE_SLOTS = frozenset({"BN", "IL", "IL+", "NA"})
IL_SLOTS = frozenset({"IL", "IL+"})
OUT_STATUSES = frozenset({"O", "OUT", "INJ", "SUSP", "NA", "OUT FOR SEASON"})

_ESPN_POSITION_ELIGIBILITY = {
    "PG": ("PG", "G"),
    "SG": ("SG", "G"),
    "G": ("PG", "SG", "G"),
    "SF": ("SF", "F"),
    "PF": ("PF", "F"),
    "F": ("SF", "PF", "F"),
    "C": ("C",),
    "FC": ("PF", "F", "C"),
    "GF": ("SG", "SF", "G", "F"),
}


@dataclass(frozen=True)
class SlotSpec:
    position: str
    count: int
    is_starting: bool


DEFAULT_SLOTS = (
    SlotSpec("PG", 1, True),
    SlotSpec("SG", 1, True),
    SlotSpec("G", 1, True),
    SlotSpec("SF", 1, True),
    SlotSpec("PF", 1, True),
    SlotSpec("F", 1, True),
    SlotSpec("C", 2, True),
    SlotSpec("UTIL", 2, True),
    SlotSpec("BN", 3, False),
    SlotSpec("IL", 2, False),
    SlotSpec("IL+", 1, False),
)


def eligibility_from_espn_position(position: str) -> tuple[str, ...]:
    base = _ESPN_POSITION_ELIGIBILITY.get((position or "").upper().replace("-", ""), ("UTIL",))
    return tuple(dict.fromkeys((*base, "UTIL")))


def can_fill(eligible: tuple[str, ...], slot: str) -> bool:
    return slot in ("UTIL", "BN") or slot in eligible


def il_statuses() -> set[str]:
    from fbm.constants import C

    return {s.strip().upper() for s in str(C("lineup.il_statuses")).split(",") if s.strip()}


def with_il_eligibility(eligible: tuple[str, ...], status: str) -> tuple[str, ...]:
    """Add or remove IL/IL+ eligibility from injury status (demo league; Yahoo reports eligibility directly)."""
    base = tuple(e for e in eligible if e not in IL_SLOTS)
    return (*base, "IL", "IL+") if (status or "").upper() in il_statuses() else base


@dataclass
class RosterPlayer:
    player_id: str  # internal id (ESPN athlete id)
    name: str
    nba_team: str
    eligible: tuple[str, ...]
    selected: str = "BN"
    status: str = ""  # Yahoo status: '', 'GTD', 'DTD', 'O', 'INJ', ...
    yahoo_key: str = ""

    @property
    def is_out(self) -> bool:
        return self.status.upper() in OUT_STATUSES


@dataclass
class FantasyTeam:
    team_key: str
    name: str
    manager: str = ""
    players: list[RosterPlayer] = field(default_factory=list)

    def player_ids(self) -> list[str]:
        return [p.player_id for p in self.players]


@dataclass
class LeagueState:
    league_key: str
    name: str
    season: int
    my_team_key: str
    teams: list[FantasyTeam]
    slots: tuple[SlotSpec, ...] = DEFAULT_SLOTS
    categories: tuple[Category, ...] = NINE_CAT
    week: int = 1
    week_start: date | None = None
    week_end: date | None = None
    max_weekly_adds: int | None = None
    source: str = "yahoo"
    waiver_priority: dict[str, int] = field(default_factory=dict)  # team_key -> rank (1 = first claim)

    def team(self, team_key: str) -> FantasyTeam:
        return next(t for t in self.teams if t.team_key == team_key)

    @property
    def my_team(self) -> FantasyTeam:
        return self.team(self.my_team_key)

    def starting_slots(self) -> list[str]:
        return [s.position for s in self.slots if s.is_starting for _ in range(s.count)]

    def roster_size(self) -> int:
        return sum(s.count for s in self.slots if s.position not in IL_SLOTS)
