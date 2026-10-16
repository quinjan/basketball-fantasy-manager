"""Draft assistant: board with tiers, live draft state, build-aware recommendations, and mock drafts.

Values are punt-aware G-scores from the projection table (pre-season = last season's per-game stats). Market
expectations come from ESPN average draft position (ADP). Recommendations add a build adjustment (categories where
my roster trails the league so far get more weight) and flag players unlikely to last until my next pick.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from fbm.analytics.categories import NINE_CAT, Category
from fbm.config import DATA_DIR
from fbm.league.model import eligibility_from_espn_position

DRAFT_STATE_PATH = DATA_DIR / "draft_state.json"
BUILD_WEIGHT = 0.5
TIER_GAP = 0.35
POSITION_NEEDS = {"PG": 1, "SG": 1, "SF": 1, "PF": 1, "C": 2}


# ------------------------------------------------------------------ pick order


def team_on_clock(overall: int, teams: int) -> int:
    """1-based team slot making the `overall`-th pick (1-based) in a snake draft."""
    rnd, idx = divmod(overall - 1, teams)
    return idx + 1 if rnd % 2 == 0 else teams - idx


def my_pick_numbers(slot: int, teams: int, rounds: int) -> list[int]:
    return [n for n in range(1, teams * rounds + 1) if team_on_clock(n, teams) == slot]


# ------------------------------------------------------------------ board


def build_board(
    values: pd.DataFrame,
    categories: Sequence[Category] = NINE_CAT,
    punts: Sequence[str] = (),
    adp: Mapping[str, float] | None = None,
    eligibility: Mapping[str, tuple[str, ...]] | None = None,
    tier_gap: float = TIER_GAP,
) -> pd.DataFrame:
    adp = adp or {}
    eligibility = eligibility or {}
    keys = [c.key for c in categories if c.key not in punts]
    board = pd.DataFrame(index=values.index)
    board["name"] = values["name"]
    board["team"] = values["team"]
    board["eligible"] = [
        eligibility.get(pid) or eligibility_from_espn_position(str(values.at[pid, "position"])) for pid in values.index
    ]
    board["value"] = values[[f"g_{k}" for k in keys]].sum(axis=1)
    for c in categories:
        board[f"g_{c.key}"] = values[f"g_{c.key}"]
    board["adp"] = [adp.get(pid, np.nan) for pid in values.index]
    board = board.sort_values("value", ascending=False)
    board["rank"] = range(1, len(board) + 1)
    board["adp_rank"] = board["adp"].rank(method="min")
    tiers, tier, prev = [], 1, None
    for v in board["value"]:
        if prev is not None and prev - v > tier_gap:
            tier += 1
        tiers.append(tier)
        prev = v
    board["tier"] = tiers
    board["value_vs_adp"] = board["adp_rank"] - board["rank"]  # positive = market drafts him later than we'd value
    return board


# ------------------------------------------------------------------ state


@dataclass
class DraftPick:
    overall: int
    team_slot: int
    pid: str
    name: str


@dataclass
class DraftState:
    teams: int = 12
    my_slot: int = 5
    rounds: int = 13
    punts: list[str] = field(default_factory=list)
    picks: list[DraftPick] = field(default_factory=list)

    @property
    def next_overall(self) -> int:
        return len(self.picks) + 1

    @property
    def done(self) -> bool:
        return self.next_overall > self.teams * self.rounds

    def on_clock(self) -> int:
        return team_on_clock(self.next_overall, self.teams)

    def my_picks(self) -> list[DraftPick]:
        return [p for p in self.picks if p.team_slot == self.my_slot]

    def my_next_picks(self) -> list[int]:
        return [n for n in my_pick_numbers(self.my_slot, self.teams, self.rounds) if n >= self.next_overall]

    def taken(self) -> set[str]:
        return {p.pid for p in self.picks}

    def record(self, pid: str, name: str) -> DraftPick:
        if self.done:
            raise ValueError("The draft is complete.")
        if pid in self.taken():
            raise ValueError(f"{name} was already drafted.")
        pick = DraftPick(self.next_overall, self.on_clock(), pid, name)
        self.picks.append(pick)
        return pick

    def undo(self) -> DraftPick | None:
        return self.picks.pop() if self.picks else None

    def save(self, path: Path = DRAFT_STATE_PATH) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=1), encoding="utf-8")

    @classmethod
    def load(cls, path: Path = DRAFT_STATE_PATH) -> DraftState:
        if not path.exists():
            raise FileNotFoundError("No draft in progress. Start one with draft_start / `fbm draft start`.")
        data = json.loads(path.read_text(encoding="utf-8"))
        data["picks"] = [DraftPick(**p) for p in data.get("picks", [])]
        return cls(**data)


# ------------------------------------------------------------------ recommendations


def _category_weights(state: DraftState, board: pd.DataFrame, categories: Sequence[Category]) -> dict[str, float]:
    """1.0 baseline; up to 1+BUILD_WEIGHT for categories where my picks trail the average drafted player."""
    mine = [p.pid for p in state.my_picks() if p.pid in board.index]
    drafted = [p.pid for p in state.picks if p.pid in board.index]
    weights = {}
    for c in categories:
        if c.key in state.punts:
            weights[c.key] = 0.0
            continue
        if not mine or not drafted:
            weights[c.key] = 1.0
            continue
        deficit = float(board.loc[drafted, f"g_{c.key}"].mean() - board.loc[mine, f"g_{c.key}"].mean())
        weights[c.key] = 1.0 + BUILD_WEIGHT * math.tanh(deficit / 0.5)
    return weights


def _position_bonus(state: DraftState, board: pd.DataFrame) -> dict[str, float]:
    """Large bonus for positions I still need when my remaining picks are running out."""
    mine = [p.pid for p in state.my_picks() if p.pid in board.index]
    have = {pos: sum(1 for pid in mine if pos in board.at[pid, "eligible"]) for pos in POSITION_NEEDS}
    unmet = {pos: n - have[pos] for pos, n in POSITION_NEEDS.items() if have[pos] < n}
    remaining = len(state.my_next_picks())
    if not unmet or remaining > sum(unmet.values()) + 1:
        return {}
    return dict.fromkeys(unmet, 1.0)


def availability_probability(adp: float, pick: int) -> float:
    """Rough P(player still available at `pick`) from ADP with spread growing with ADP."""
    if np.isnan(adp):
        return 0.9
    spread = 2.0 + 0.15 * adp
    z = (adp - pick) / spread
    return float(0.5 * (1 + math.erf(z / math.sqrt(2))))


def recommend(
    state: DraftState, board: pd.DataFrame, categories: Sequence[Category] = NINE_CAT, top: int = 8
) -> dict[str, Any]:
    weights = _category_weights(state, board, categories)
    pos_bonus = _position_bonus(state, board)
    avail = board[~board.index.isin(state.taken())].copy()
    avail["build_score"] = sum(avail[f"g_{c.key}"] * weights[c.key] for c in categories)
    if pos_bonus:
        avail["build_score"] += [
            max((b for pos, b in pos_bonus.items() if pos in el), default=0.0) for el in avail["eligible"]
        ]
    avail = avail.sort_values("build_score", ascending=False)

    my_next = state.my_next_picks()
    is_mine = state.on_clock() == state.my_slot and not state.done
    following = my_next[1] if is_mine and len(my_next) > 1 else (my_next[0] if my_next and not is_mine else None)
    recs = []
    for pid, row in avail.head(top).iterrows():
        p_avail = availability_probability(row["adp"], following) if following else None
        recs.append(
            {
                "player": row["name"],
                "id": pid,
                "nba": row["team"],
                "positions": [e for e in row["eligible"] if e != "UTIL"],
                "value": round(float(row["value"]), 2),
                "build_score": round(float(row["build_score"]), 2),
                "rank": int(row["rank"]),
                "tier": int(row["tier"]),
                "adp": None if np.isnan(row["adp"]) else round(float(row["adp"]), 1),
                "p_available_next_pick": None if p_avail is None else round(p_avail, 2),
            }
        )

    projected_next: list[str] = []
    if following:
        others = max(0, following - state.next_overall - (1 if is_mine else 0))
        by_adp = avail.assign(_adp=avail["adp"].fillna(avail["rank"] + 30)).sort_values("_adp")
        gone = set(by_adp.index[: others + (1 if is_mine else 0)])
        projected_next = [str(avail.at[pid, "name"]) for pid in avail.index if pid not in gone][:5]

    mine = [p.pid for p in state.my_picks() if p.pid in board.index]
    profile = {c.label: round(float(board.loc[mine, f"g_{c.key}"].sum()), 2) for c in categories} if mine else {}
    return {
        "overall": state.next_overall,
        "round": (state.next_overall - 1) // state.teams + 1,
        "on_clock": state.on_clock(),
        "is_my_pick": is_mine,
        "my_next_picks": my_next[:3],
        "punts": state.punts,
        "category_weights": {k: round(v, 2) for k, v in weights.items()},
        "position_needs": sorted(pos_bonus),
        "recommendations": recs,
        "projected_best_at_my_following_pick": projected_next,
        "my_team": [{"player": p.name, "overall": p.overall} for p in state.my_picks()],
        "my_category_g_sum": profile,
    }


# ------------------------------------------------------------------ mock draft


def mock_draft(
    board: pd.DataFrame,
    state: DraftState,
    categories: Sequence[Category] = NINE_CAT,
    seed: int = 0,
    adp_noise: float = 0.12,
) -> DraftState:
    """Complete the draft: other teams pick by noisy ADP, my team follows `recommend`."""
    rng = np.random.default_rng(seed)
    fallback = board["rank"] + 30
    market = board["adp"].fillna(fallback)
    while not state.done:
        if state.on_clock() == state.my_slot:
            pick = recommend(state, board, categories, top=1)["recommendations"][0]
            state.record(pick["id"], pick["player"])
            continue
        taken = state.taken()
        avail = market[~market.index.isin(taken)]
        noisy = avail * (1 + rng.normal(0, adp_noise, len(avail))) + rng.normal(0, 1.5, len(avail))
        pid = str(noisy.idxmin())
        state.record(pid, str(board.at[pid, "name"]))
    return state


def rosters_from_state(state: DraftState) -> dict[int, list[str]]:
    out: dict[int, list[str]] = {slot: [] for slot in range(1, state.teams + 1)}
    for p in state.picks:
        out[p.team_slot].append(p.pid)
    return out
