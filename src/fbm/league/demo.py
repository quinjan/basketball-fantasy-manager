"""Demo league: a simulated snake draft over real players, for developing/testing before Yahoo access is approved."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from fbm.analytics.lineup import optimize_day
from fbm.config import DATA_DIR
from fbm.league.model import (
    DEFAULT_SLOTS,
    FantasyTeam,
    LeagueState,
    RosterPlayer,
    SlotSpec,
    eligibility_from_espn_position,
    with_il_eligibility,
)

DEMO_PATH = DATA_DIR / "demo_league.json"
DEMO_TEAM_NAMES = (
    "Pick and Roll Tide",
    "Dunk Tank",
    "Box Out Bandits",
    "Triple Threat",
    "My Team (Demo)",
    "Euro Steppers",
    "Glass Cleaners",
    "Splash Brothers",
    "Fast Break Fund",
    "Backdoor Cutters",
    "Zone Busters",
    "Bench Mob",
)


def _pick(available: pd.DataFrame, rng: np.random.Generator, center_count: int, max_centers: int) -> str:
    pool = available
    if center_count >= max_centers:
        pool = available[available["position"] != "C"]
    top = pool.head(3)
    weights = np.array([0.6, 0.25, 0.15][: len(top)])
    return str(rng.choice(top.index.to_numpy(), p=weights / weights.sum()))


def roster_player(
    pid: str, values: pd.DataFrame, eligibility: Mapping[str, tuple[str, ...]] | None = None, status: str = ""
) -> RosterPlayer:
    eligible = (eligibility or {}).get(pid) or eligibility_from_espn_position(str(values.loc[pid, "position"]))
    return RosterPlayer(pid, str(values.loc[pid, "name"]), str(values.loc[pid, "team"]), tuple(eligible), "BN", status)


def league_from_rosters(
    rosters: Sequence[Sequence[str]],
    values: pd.DataFrame,
    season: int,
    my_index: int,
    statuses: Mapping[str, str] | None = None,
    eligibility: Mapping[str, tuple[str, ...]] | None = None,
    slots: tuple[SlotSpec, ...] = DEFAULT_SLOTS,
    name: str = "Demo League (simulated draft)",
    key_prefix: str = "demo",
) -> LeagueState:
    """LeagueState from drafted rosters (list per team, 0-based my_index); lineups set to the value-optimal ones."""
    statuses = statuses or {}
    starting = [s.position for s in slots if s.is_starting for _ in range(s.count)]
    teams = []
    for t, ids in enumerate(rosters):
        players = [roster_player(pid, values, eligibility, statuses.get(pid, "")) for pid in ids]
        lineup = optimize_day(players, starting, {p.player_id for p in players}, values["g_total"].to_dict())
        for p in players:
            p.selected = lineup.assignments[p.player_id]
        team_name = "My Team (Demo)" if t == my_index else DEMO_TEAM_NAMES[t % len(DEMO_TEAM_NAMES)]
        if t != my_index and team_name == "My Team (Demo)":
            team_name = f"Team {t + 1}"
        teams.append(FantasyTeam(f"{key_prefix}.t.{t + 1}", team_name, f"Manager {t + 1}", players))
    return LeagueState(
        league_key=key_prefix,
        name=name,
        season=season,
        my_team_key=f"{key_prefix}.t.{my_index + 1}",
        teams=teams,
        slots=slots,
        max_weekly_adds=4,
        source="demo",
    )


def build_demo_league(
    values: pd.DataFrame,
    season: int,
    statuses: dict[str, str],
    n_teams: int = 12,
    my_slot: int = 5,
    slots: tuple[SlotSpec, ...] = DEFAULT_SLOTS,
    seed: int = 1,
    eligibility: Mapping[str, tuple[str, ...]] | None = None,
) -> LeagueState:
    rng = np.random.default_rng(seed)
    roster_size = sum(s.count for s in slots if s.position not in ("IL", "IL+"))
    available = values.sort_values("g_total", ascending=False).head(n_teams * roster_size + 60).copy()
    rosters: list[list[str]] = [[] for _ in range(n_teams)]
    for rnd in range(roster_size):
        order = range(n_teams) if rnd % 2 == 0 else range(n_teams - 1, -1, -1)
        for t in order:
            centers = sum(1 for pid in rosters[t] if values.loc[pid, "position"] == "C")
            pid = _pick(available, rng, centers, max_centers=4)
            rosters[t].append(pid)
            available = available.drop(index=pid)
    return league_from_rosters(rosters, values, season, my_slot - 1, statuses, eligibility, slots)


def refresh_players(
    league: LeagueState, eligibility: Mapping[str, tuple[str, ...]], statuses: Mapping[str, str]
) -> LeagueState:
    """Overlay current ESPN eligibility and injury statuses (with derived IL eligibility) on a demo league."""
    for team in league.teams:
        for p in team.players:
            base = tuple(eligibility[p.player_id]) if p.player_id in eligibility else p.eligible
            p.status = statuses.get(p.player_id, "")
            p.eligible = with_il_eligibility(base, p.status)
    return league


def default_waiver_priority(league: LeagueState) -> dict[str, int]:
    """Yahoo's initial rolling order is reverse draft order (team keys are in draft order)."""
    n = len(league.teams)
    return {t.team_key: n - i for i, t in enumerate(league.teams)}


def rotate_waiver_priority(priority: Mapping[str, int], claimer: str) -> dict[str, int]:
    """Rolling waivers: a successful claimer drops to last; everyone behind moves up one."""
    old = priority[claimer]
    out = {k: (v - 1 if v > old else v) for k, v in priority.items()}
    out[claimer] = len(priority)
    return out


def save_league(league: LeagueState, path: Path = DEMO_PATH) -> None:
    data = asdict(league)
    data["categories"] = [c["key"] for c in data["categories"]]
    for k in ("week_start", "week_end"):
        data[k] = data[k].isoformat() if isinstance(data[k], date) else data[k]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1), encoding="utf-8")


def load_league(path: Path = DEMO_PATH) -> LeagueState:
    from fbm.analytics.categories import NINE_CAT

    if not path.exists():
        raise FileNotFoundError("No demo league yet. Run `fbm demo build`.")
    data = json.loads(path.read_text(encoding="utf-8"))
    by_key = {c.key: c for c in NINE_CAT}
    teams = [
        FantasyTeam(
            t["team_key"],
            t["name"],
            t["manager"],
            [RosterPlayer(**{**p, "eligible": tuple(p["eligible"])}) for p in t["players"]],
        )
        for t in data["teams"]
    ]
    return LeagueState(
        league_key=data["league_key"],
        name=data["name"],
        season=data["season"],
        my_team_key=data["my_team_key"],
        teams=teams,
        slots=tuple(SlotSpec(**s) for s in data["slots"]),
        categories=tuple(by_key[k] for k in data["categories"]),
        week=data.get("week", 1),
        week_start=date.fromisoformat(data["week_start"]) if data.get("week_start") else None,
        week_end=date.fromisoformat(data["week_end"]) if data.get("week_end") else None,
        max_weekly_adds=data.get("max_weekly_adds"),
        source=data.get("source", "demo"),
        waiver_priority={k: int(v) for k, v in (data.get("waiver_priority") or {}).items()},
        opponent_key=data.get("opponent_key"),
        banked={k: {s: float(x) for s, x in v.items()} for k, v in (data.get("banked") or {}).items()},
        standings=list(data.get("standings") or []),
        on_waivers=dict(data.get("on_waivers") or {}),
        adds_used={k: int(v) for k, v in (data.get("adds_used") or {}).items()},
        fetched_at=data.get("fetched_at", ""),
    )
