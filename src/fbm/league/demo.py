"""Demo league: a simulated snake draft over real players, for developing/testing before Yahoo access is approved."""

from __future__ import annotations

import json
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


def build_demo_league(
    values: pd.DataFrame,
    season: int,
    statuses: dict[str, str],
    n_teams: int = 12,
    my_slot: int = 5,
    slots: tuple[SlotSpec, ...] = DEFAULT_SLOTS,
    seed: int = 1,
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

    starting = [s.position for s in slots if s.is_starting for _ in range(s.count)]
    teams = []
    for t, ids in enumerate(rosters):
        players = [
            RosterPlayer(
                pid,
                str(values.loc[pid, "name"]),
                str(values.loc[pid, "team"]),
                eligibility_from_espn_position(str(values.loc[pid, "position"])),
                "BN",
                statuses.get(pid, ""),
            )
            for pid in ids
        ]
        lineup = optimize_day(players, starting, {p.player_id for p in players}, values["g_total"].to_dict())
        for p in players:
            p.selected = lineup.assignments[p.player_id]
        teams.append(
            FantasyTeam(f"demo.t.{t + 1}", DEMO_TEAM_NAMES[t % len(DEMO_TEAM_NAMES)], f"Manager {t + 1}", players)
        )
    return LeagueState(
        league_key="demo",
        name="Demo League (simulated draft)",
        season=season,
        my_team_key=f"demo.t.{my_slot}",
        teams=teams,
        slots=slots,
        max_weekly_adds=4,
        source="demo",
    )


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
    )
