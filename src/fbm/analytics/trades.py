"""Trade evaluation and search.

Season-level lens: each team's schedule-neutral typical week is simulated (common random numbers via DrawBank) and
compared against every other team. A trade's value to a team = change in its average expected categories won per
week vs the league. Rosters are kept legal: the side receiving more players drops its least valuable players; the
side receiving fewer fills open spots with the best free agents (true free agents only, never players on waivers).

How a partner judges an offer by rankings uses value above replacement (the last rostered player in the league):
a player they would cut anyway is worth ~0 to them, so a forced drop is never counted as a gain. The search also
asks for a partner's expendable players (lowest value to their own build) so a 2-for-1 can be offered as a
roster-neutral 2-for-2 that takes their likely cut off their hands.

The partner's build (`PartnerBuild`: likely punts and the categories they chase on the waiver wire, from
`fbm.analytics.opponents`) shapes how their side is judged: punted categories don't count toward their gain
(`expected_delta_build`) or their ranking value (`value_delta_build`, above a punt-aware replacement level), their
forced drops follow their build, and the search weights categories they chase more. The raw all-category numbers
are kept next to the build-aware ones.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from itertools import combinations

import numpy as np
import pandas as pd

from fbm.analytics.bank import DrawBank
from fbm.analytics.simulation import compare
from fbm.constants import C
from fbm.league.model import IL_SLOTS, LeagueState

GENERIC_GAMES = 4


def need_weights(win_rates: Mapping[str, float], punts: Sequence[str] = ()) -> dict[str, float]:
    """Marginal importance of each category: highest near 50% win rate, low when locked/lost, 0 when punted."""
    floor, slope = float(C("trade.need_weight_floor")), float(C("trade.need_weight_slope"))
    return {k: 0.0 if k in punts else max(floor, 1.0 - abs(p - 0.5) * slope) for k, p in win_rates.items()}


@dataclass(frozen=True)
class PartnerBuild:
    """How a trade partner values categories: punts (ignored) and wire targets (weighted up), category keys."""

    punts: tuple[str, ...] = ()
    targets: tuple[str, ...] = ()


def partner_weights(win_rates: Mapping[str, float], build: PartnerBuild | None = None) -> dict[str, float]:
    """The partner's need weights: punts at 0, categories they chase on the wire boosted by trade.wire_target_bonus."""
    build = build or PartnerBuild()
    w = need_weights(win_rates, build.punts)
    bonus = 1.0 + float(C("trade.wire_target_bonus"))
    return {k: v * bonus if k in build.targets else v for k, v in w.items()}


class LeagueSim:
    def __init__(self, league: LeagueState, bank: DrawBank, values: pd.DataFrame):
        self.league = league
        self.bank = bank
        self.values = values
        self.categories = league.categories
        self.n_start = len(league.starting_slots())
        self.capacity = league.roster_size()
        self.active = {t.team_key: [p.player_id for p in t.players if p.selected not in IL_SLOTS] for t in league.teams}
        self.totals = {k: self.team_totals(ids) for k, ids in self.active.items()}
        self._base_profiles: dict[str, tuple[dict[str, float], float]] = {}
        g = values["g_total"].astype(float) if "g_total" in values.columns else pd.Series(dtype=float)
        self._pool = max(1, self.capacity * len(league.teams))
        self.replacement = float(g.nlargest(self._pool).min()) if len(g) else 0.0
        self._replacement_by_punts: dict[tuple[str, ...], float] = {}

    def replacement_for(self, punts: Sequence[str] = ()) -> float:
        """Replacement level (the last rostered player in the league) for a build that ignores `punts`."""
        key = tuple(sorted(punts))
        if not key:
            return self.replacement
        if key not in self._replacement_by_punts:
            cols = [f"g_{c.key}" for c in self.categories if c.key not in key and f"g_{c.key}" in self.values.columns]
            g = self.values[cols].astype(float).sum(axis=1) if cols else pd.Series(dtype=float)
            self._replacement_by_punts[key] = float(g.nlargest(self._pool).min()) if len(g) else 0.0
        return self._replacement_by_punts[key]

    def above_replacement(self, pid: str, punts: Sequence[str] = ()) -> float:
        """Value above the league's replacement level (0 for anyone a team would cut); `punts` = a build's punts."""
        if pid not in self.values.index:
            return 0.0
        if not punts:
            return max(_g(self.values, pid, "g_total") - self.replacement, 0.0)
        return max(_rank_value(self.values, pid, punts, self.categories) - self.replacement_for(punts), 0.0)

    def team_totals(self, ids: Sequence[str]) -> np.ndarray:
        if not ids:
            return np.zeros((self.bank.n, 11))
        share = min(1.0, self.n_start / len(ids)) * float(C("week.avg_games_per_week")) / GENERIC_GAMES
        total = np.zeros((self.bank.n, 11))
        for pid in ids:
            total += self.bank.generic_week(pid, GENERIC_GAMES)
        return total * share

    def profile(
        self, team_key: str, overrides: Mapping[str, np.ndarray] | None = None
    ) -> tuple[dict[str, float], float]:
        if not overrides and team_key in self._base_profiles:
            return self._base_profiles[team_key]
        totals = {**self.totals, **(overrides or {})}
        mine = totals[team_key]
        acc = {c.key: 0.0 for c in self.categories}
        expected = 0.0
        others = [k for k in totals if k != team_key]
        for other in others:
            odds = compare(mine, totals[other], self.categories)
            for o in odds.categories:
                acc[o.category.key] += o.p_win + 0.5 * o.p_tie
            expected += odds.expected_score
        n = max(1, len(others))
        result = ({k: v / n for k, v in acc.items()}, expected / n)
        if not overrides:
            self._base_profiles[team_key] = result
        return result


@dataclass
class TradeSide:
    team_key: str
    name: str
    gives: list[str]
    gets: list[str]
    drops: list[str] = field(default_factory=list)
    fa_adds: list[str] = field(default_factory=list)
    cats_before: dict[str, float] = field(default_factory=dict)
    cats_after: dict[str, float] = field(default_factory=dict)
    expected_before: float = 0.0
    expected_after: float = 0.0
    value_delta: float = 0.0  # G-score value above replacement in minus out, incl. drops/fills (ranking lens)
    g_delta_by_cat: dict[str, float] = field(default_factory=dict)
    punts: tuple[str, ...] = ()  # the build this side is judged by (partner: likely punts; empty = all categories)
    targets: tuple[str, ...] = ()  # categories this side chases on the waiver wire (partner only)
    value_delta_build: float | None = None  # value_delta over non-punted categories (punt-aware replacement level)

    @property
    def expected_delta(self) -> float:
        return self.expected_after - self.expected_before

    @property
    def expected_delta_build(self) -> float:
        """Change in expected categories won per week, counting only categories this side doesn't punt."""
        return sum(self.cats_after[k] - self.cats_before[k] for k in self.cats_before if k not in self.punts)

    @property
    def judged_value_delta(self) -> float:
        return self.value_delta if self.value_delta_build is None else self.value_delta_build


@dataclass
class TradeEval:
    me: TradeSide
    them: TradeSide
    verdict: str
    notes: list[str] = field(default_factory=list)


def _g(values: pd.DataFrame, pid: str, col: str) -> float:
    return float(values.at[pid, col]) if pid in values.index else 0.0


def _rank_value(values: pd.DataFrame, pid: str, punts: Sequence[str], categories) -> float:
    return sum(_g(values, pid, f"g_{c.key}") for c in categories if c.key not in punts)


def _rebalance(
    ids: list[str],
    keep: set[str],
    capacity: int,
    values: pd.DataFrame,
    punts: Sequence[str],
    categories,
    free_agents: list[str],
    taken: set[str],
) -> tuple[list[str], list[str], list[str]]:
    drops: list[str] = []
    adds: list[str] = []
    while len(ids) > capacity:
        candidates = [pid for pid in ids if pid not in keep] or ids
        worst = min(candidates, key=lambda pid: _rank_value(values, pid, punts, categories))
        ids.remove(worst)
        drops.append(worst)
    for fa in free_agents:
        if len(ids) >= capacity:
            break
        if fa not in taken and fa not in ids:
            ids.append(fa)
            adds.append(fa)
            taken.add(fa)
    return ids, drops, adds


def verdict_for(me_delta: float, them_delta: float, them_value_delta: float = 0.0) -> str:
    if me_delta <= 0.02:
        return "not worth it for us"
    if them_value_delta < -1.5:
        return "lopsided by rankings (unlikely to be accepted)"
    if them_delta >= 0.02:
        return "win-win"
    if them_delta >= -0.05:
        return "favors us (defensible)"
    return "lopsided (unlikely to be accepted)"


def evaluate_trade(
    sim: LeagueSim,
    my_key: str,
    partner_key: str,
    give: Sequence[str],
    get: Sequence[str],
    free_agents: Sequence[str],
    my_punts: Sequence[str] = (),
    partner: PartnerBuild | None = None,
) -> TradeEval:
    """Season-level evaluation for both sides. `partner` = the partner's build (likely punts, wire targets): their
    forced drops follow it and the verdict judges their side by it (`expected_delta_build`, `value_delta_build`)."""
    league = sim.league
    build = partner or PartnerBuild()
    mine_ids, their_ids = list(sim.active[my_key]), list(sim.active[partner_key])
    mine_all = {p.player_id for p in league.team(my_key).players}
    their_all = {p.player_id for p in league.team(partner_key).players}
    missing = [pid for pid in give if pid not in mine_all] + [pid for pid in get if pid not in their_all]
    if missing:
        raise ValueError(f"players not owned by the expected team: {missing}")
    if not give or not get:
        raise ValueError("a trade needs at least one player on each side")

    new_mine = [pid for pid in mine_ids if pid not in give] + list(get)
    new_theirs = [pid for pid in their_ids if pid not in get] + list(give)
    # roster-balance add-ins we receive (below replacement) are cut for the best free agent right away
    my_cuts = [pid for pid in get if len(get) > 1 and pid in sim.values.index and sim.above_replacement(pid) == 0.0]
    new_mine = [pid for pid in new_mine if pid not in my_cuts]
    taken = {p.player_id for t in league.teams for p in t.players}
    fas = list(free_agents)
    new_mine, my_drops, my_adds = _rebalance(
        new_mine, set(get), sim.capacity, sim.values, my_punts, sim.categories, fas, taken
    )
    my_drops = [*my_cuts, *my_drops]
    new_theirs, their_drops, their_adds = _rebalance(
        new_theirs, set(give), sim.capacity, sim.values, build.punts, sim.categories, fas, taken
    )
    overrides = {my_key: sim.team_totals(new_mine), partner_key: sim.team_totals(new_theirs)}

    sides = []
    for key, gives, gets, drops, adds, punts in (
        (my_key, give, get, my_drops, my_adds, ()),
        (partner_key, get, give, their_drops, their_adds, build.punts),
    ):
        before_cats, before_exp = sim.profile(key)
        after_cats, after_exp = sim.profile(key, overrides)
        incoming, outgoing = [*gets, *adds], [*gives, *drops]
        g_delta = {
            c.label: round(
                sum(_g(sim.values, p, f"g_{c.key}") for p in incoming)
                - sum(_g(sim.values, p, f"g_{c.key}") for p in outgoing),
                2,
            )
            for c in sim.categories
        }
        sides.append(
            TradeSide(
                key,
                league.team(key).name,
                list(gives),
                list(gets),
                drops,
                adds,
                before_cats,
                after_cats,
                before_exp,
                after_exp,
                round(
                    sum(sim.above_replacement(p) for p in incoming) - sum(sim.above_replacement(p) for p in outgoing),
                    2,
                ),
                g_delta,
                tuple(punts),
                build.targets if key == partner_key else (),
                round(
                    sum(sim.above_replacement(p, punts) for p in incoming)
                    - sum(sim.above_replacement(p, punts) for p in outgoing),
                    2,
                )
                if punts
                else None,
            )
        )
    me, them = sides
    labels = {c.key: c.label for c in sim.categories}
    notes = []
    if them.judged_value_delta >= 0:
        notes.append("Looks fair or better to them by overall rankings (helps acceptance).")
    elif them.judged_value_delta < -1.0:
        notes.append("They lose overall ranking value; expect pushback unless it fixes a clear need.")
    if build.punts:
        noise = them.expected_delta - them.expected_delta_build
        if abs(noise) >= 0.02:
            notes.append(
                f"Judged by their build (likely punts {', '.join(labels.get(k, k) for k in build.punts)}): "
                f"{them.expected_delta_build:+.2f} exp cats vs {them.expected_delta:+.2f} counting every category."
            )
    helped = [k for k in build.targets if them.cats_after.get(k, 0.0) - them.cats_before.get(k, 0.0) >= 0.02]
    if helped:
        notes.append(
            f"Helps {', '.join(labels.get(k, k) for k in helped)}, which they've been chasing on the waiver wire "
            "(better odds they accept)."
        )
    if my_drops:
        notes.append(f"We must drop {len(my_drops)} player(s) to make room.")
    if my_adds:
        notes.append(f"Opens {len(my_adds)} roster spot(s) for us; filled with the best free agent(s).")
    if len(give) > 1 and len(get) > 1:
        for pid in get:
            if sim.above_replacement(pid) == 0.0:
                known = "name" in sim.values.columns and pid in sim.values.index
                who = str(sim.values.at[pid, "name"]) if known else pid
                notes.append(
                    f"{who} is a roster-balance piece (below replacement; their likely cut): he keeps their roster "
                    "count even; we cut him for the best free agent (already counted)."
                )
    return TradeEval(
        me, them, verdict_for(me.expected_delta, them.expected_delta_build, them.judged_value_delta), notes
    )


@dataclass(frozen=True)
class TradeCandidate:
    partner_key: str
    give: tuple[str, ...]
    get: tuple[str, ...]
    screen_me: float
    screen_them: float


def screen_trades(
    sim: LeagueSim,
    my_key: str,
    my_punts: Sequence[str],
    free_agents: Sequence[str] = (),
    partners: Sequence[str] | None = None,
    max_give: int = 2,
    max_get: int = 2,
    top_k: int = 10,
    max_their_value_loss: float | None = None,
    partner_builds: Mapping[str, PartnerBuild] | None = None,
) -> list[TradeCandidate]:
    """Cheap need-weighted G-score screen of 1-1, 2-1, 1-2 (and 2-2) packages. Each partner is weighted by its own
    build (`partner_builds`: likely punts at 0, wire targets boosted)."""
    league, values = sim.league, sim.values
    keys = [c.key for c in sim.categories]
    partner_builds = partner_builds or {}

    def vec(pid: str) -> np.ndarray:
        return np.array([_g(values, pid, f"g_{k}") for k in keys])

    gmat = {pid: vec(pid) for ids in sim.active.values() for pid in ids}
    my_w = np.array([need_weights(sim.profile(my_key)[0], my_punts)[k] for k in keys])

    def top(team_key: str, punts: Sequence[str]) -> list[str]:
        return sorted(sim.active[team_key], key=lambda p: -_rank_value(values, p, punts, sim.categories))[:top_k]

    def worst_vec(team_key: str, punts: Sequence[str]) -> np.ndarray:
        worst = min(sim.active[team_key], key=lambda p: _rank_value(values, p, punts, sim.categories))
        return gmat[worst]

    def expendable(team_key: str, weights: np.ndarray, exclude: Sequence[str]) -> list[str]:
        """Players the partner would give up most easily: lowest need-weighted value to their own build."""
        rest = [p for p in sim.active[team_key] if p not in exclude and p in values.index]  # unknown ≠ worthless
        return sorted(rest, key=lambda p: float(weights @ gmat[p]))[:n_expendable]

    def vorp(ids: Sequence[str], punts: Sequence[str] = ()) -> float:
        return sum(sim.above_replacement(p, punts) for p in ids)

    n_expendable = int(C("trade.expendable_pool"))
    out: list[TradeCandidate] = []
    partners = partners or [t.team_key for t in league.teams if t.team_key != my_key]
    mine_top = top(my_key, my_punts)
    my_worst = worst_vec(my_key, my_punts)
    best_fa = max(free_agents, key=lambda p: _g(values, p, "g_total"), default=None)
    replacement = vec(best_fa) if best_fa else np.zeros(len(keys))
    for partner in partners:
        build = partner_builds.get(partner) or PartnerBuild()
        their_w = np.array([partner_weights(sim.profile(partner)[0], build)[k] for k in keys])
        their_top = top(partner, build.punts)
        spare = expendable(partner, their_w, their_top)
        their_worst = worst_vec(partner, build.punts)
        for ng in range(1, max_give + 1):
            for nr in range(1, max_get + 1):
                for give in combinations(mine_top, ng):
                    give_vec = sum(gmat[p] for p in give)
                    for get in combinations([*their_top, *spare], nr):
                        if all(p in spare for p in get):
                            continue  # asking only for their spare parts is not a trade worth proposing
                        get_vec = sum(gmat[p] for p in get)
                        # to us, a below-replacement add-in is worth the free agent we'd swap him for
                        my_get_vec = sum(
                            replacement if p in spare and sim.above_replacement(p) == 0.0 else gmat[p] for p in get
                        )
                        mine_delta = my_get_vec - give_vec
                        their_delta = give_vec - get_vec
                        if nr > ng:  # we receive more: we drop our worst; they fill with replacement
                            mine_delta = mine_delta - (nr - ng) * my_worst
                            their_delta = their_delta + (nr - ng) * replacement
                        elif ng > nr:
                            mine_delta = mine_delta + (ng - nr) * replacement
                            their_delta = their_delta - (ng - nr) * their_worst
                        s_me = float(my_w @ mine_delta)
                        s_them = float(their_w @ their_delta)
                        if (
                            max_their_value_loss is not None
                            and vorp(give, build.punts) - vorp(get, build.punts) < -max_their_value_loss - 0.5
                        ):
                            continue
                        if s_me > 0 and s_them > -1.0:
                            out.append(TradeCandidate(partner, give, get, s_me, s_them))
    out.sort(key=lambda c: -(c.screen_me + 0.5 * min(c.screen_them, 0.0)))
    return out


def find_trades(
    sim: LeagueSim,
    my_key: str,
    free_agents: Sequence[str],
    my_punts: Sequence[str] = (),
    partners: Sequence[str] | None = None,
    max_give: int = 2,
    max_get: int = 2,
    confirm: int = 40,
    limit: int = 8,
    max_their_value_loss: float | None = None,
    partner_builds: Mapping[str, PartnerBuild] | None = None,
) -> list[TradeEval]:
    """Trades that help us, don't hurt them in simulation, and don't cost them more than `max_their_value_loss`
    in overall ranking value (real managers judge offers largely by rankings). The partner's side is judged by its
    build (`partner_builds`): punted categories don't count for them."""
    if max_their_value_loss is None:
        max_their_value_loss = float(C("trade.max_their_value_loss"))
    partner_builds = partner_builds or {}
    candidates = screen_trades(
        sim,
        my_key,
        my_punts,
        free_agents,
        partners,
        max_give,
        max_get,
        max_their_value_loss=max_their_value_loss,
        partner_builds=partner_builds,
    )[:confirm]
    results = [
        evaluate_trade(
            sim, my_key, c.partner_key, c.give, c.get, free_agents, my_punts, partner_builds.get(c.partner_key)
        )
        for c in candidates
    ]
    good = [
        r
        for r in results
        if r.me.expected_delta > 0.02
        and r.them.expected_delta_build >= -0.05
        and r.them.judged_value_delta >= -max_their_value_loss
    ]
    good.sort(key=lambda r: -(r.me.expected_delta + 0.25 * min(r.them.expected_delta_build, 0.0)))
    return good[:limit]
