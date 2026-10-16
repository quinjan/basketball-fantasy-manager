"""Rolling waiver priority: when is a claim worth moving to the back of the line?

- Free agents cost no priority: add if it's an upgrade.
- Waiver claims cost priority. The required rest-of-season gain scales with how valuable my priority is:
  waiver.threshold_top at #1 down to waiver.threshold_bottom at last.
- Below that bar, wait for him to clear waivers and add him as a free agent (keeps priority) only if no other team
  is likely to claim him: P(claimed) = 1 - prod(1 - activity) over every other team he would improve. Teams behind
  us count too: if we don't claim, their claims win.
- P(claimed before me) counts only teams with better priority: the chance our claim fails.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from fbm.constants import C


@dataclass(frozen=True)
class Competitor:
    team_key: str
    name: str
    priority: int
    gain: float  # need-weighted gain over their weakest active player


@dataclass
class WaiverDecision:
    action: str  # add | claim | wait | pass
    ros_gain: float
    on_waivers: bool
    my_priority: int | None
    n_teams: int
    threshold: float | None
    p_claimed_before_me: float  # a team ahead claims him (our claim fails)
    p_claimed_by_other: float  # anyone else claims him if we don't (waiting fails)
    competitors: list[Competitor] = field(default_factory=list)  # every other team he would improve
    reason: str = ""


def claim_threshold(priority: int, n_teams: int) -> float:
    top, bottom = float(C("waiver.threshold_top")), float(C("waiver.threshold_bottom"))
    if n_teams <= 1:
        return bottom
    frac = (priority - 1) / (n_teams - 1)
    return top + (bottom - top) * frac


def p_claimed(competitors: Sequence[Competitor], activity: float | None = None) -> float:
    a = float(C("waiver.manager_activity")) if activity is None else activity
    p_none = 1.0
    for _ in competitors:
        p_none *= 1 - a
    return 1 - p_none


def decide(
    ros_gain: float,
    on_waivers: bool,
    my_priority: int | None,
    n_teams: int,
    competitors: Sequence[Competitor],
) -> WaiverDecision:
    priority = my_priority or n_teams
    comps = sorted(competitors, key=lambda c: c.priority)
    p_before = p_claimed([c for c in comps if c.priority < priority])
    p_other = p_claimed(comps)

    def out(action: str, threshold: float | None, reason: str) -> WaiverDecision:
        return WaiverDecision(
            action, ros_gain, on_waivers, priority, n_teams, threshold, p_before, p_other, comps, reason
        )

    if ros_gain <= 0:
        return out("pass", None, "Not a rest-of-season upgrade over the player you'd drop.")
    if not on_waivers:
        return out("add", None, "Free agent: no waiver priority cost.")
    threshold = claim_threshold(priority, n_teams)
    if ros_gain >= threshold:
        return out("claim", threshold, f"Gain {ros_gain:.2f} clears the {threshold:.2f} bar for priority #{priority}.")
    if p_other < float(C("waiver.wait_max_p_claimed")):
        return out(
            "wait",
            threshold,
            f"Gain {ros_gain:.2f} is below the {threshold:.2f} bar, and other teams are unlikely to claim him "
            f"({p_other:.0%}); add him free after waivers clear and keep priority.",
        )
    return out(
        "pass",
        threshold,
        f"Gain {ros_gain:.2f} is below the {threshold:.2f} value of priority #{priority}, and other teams will "
        f"likely claim him ({p_other:.0%}).",
    )


def interested_teams(
    player_vec: Mapping[str, float],
    my_priority: int,
    priorities: Mapping[str, int],
    weakest_vecs: Mapping[str, Mapping[str, float]],
    team_weights: Mapping[str, Mapping[str, float]],
    names: Mapping[str, str],
) -> list[Competitor]:
    """Other teams for whom the player beats their weakest active player (need-weighted), by priority."""
    out = []
    for team_key, prio in priorities.items():
        if prio == my_priority or team_key not in weakest_vecs:
            continue
        w = team_weights.get(team_key, {})
        gain = sum(w.get(k, 1.0) * (player_vec.get(k, 0.0) - weakest_vecs[team_key].get(k, 0.0)) for k in player_vec)
        if gain > 0:
            out.append(Competitor(team_key, names.get(team_key, team_key), prio, round(gain, 2)))
    return sorted(out, key=lambda c: c.priority)
