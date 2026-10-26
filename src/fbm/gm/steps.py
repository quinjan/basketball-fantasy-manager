"""Owner steps. FBM never changes the fantasy league: every approved move becomes exact steps the OWNER performs in
Yahoo. In the demo league (no real Yahoo league to act in), the owner's confirmation updates the demo roster instead,
simulating the move they would have made."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from fbm.analytics.il_activation import validate_activation
from fbm.gm import memory
from fbm.gm.proposals import Proposal, effective_kind
from fbm.league.demo import default_waiver_priority, roster_player, rotate_waiver_priority, save_league
from fbm.league.model import IL_SLOTS, LeagueState, RosterPlayer


@dataclass
class StepsResult:
    status: str  # awaiting_owner | done | failed
    detail: dict[str, Any] = field(default_factory=dict)


def _rostered(league: LeagueState) -> dict[str, str]:
    return {p.player_id: t.team_key for t in league.teams for p in t.players}


def already_made(p: Proposal, league: LeagueState) -> bool:
    """True when the current league already reflects the move: the owner made it in Yahoo before asking for steps
    (or before the next sync). Such a proposal must not "fail" re-validation for its own success."""
    owners = _rostered(league)
    mine = league.my_team_key
    pay = p.payload
    kind = effective_kind(p.kind, pay)
    if kind in ("add_drop", "waiver_claim"):
        add, drop = pay.get("add"), pay.get("drop")
        return bool(add) and owners.get(add) == mine and (not drop or owners.get(drop) != mine)
    if kind == "il_activation":
        act, drop = pay.get("activate"), pay.get("drop")
        mover = next((x for x in league.my_team.players if x.player_id == act), None)
        return mover is not None and mover.selected not in IL_SLOTS and (not drop or owners.get(drop) != mine)
    if kind == "trade":
        give, get = list(pay.get("give", [])), list(pay.get("get", []))
        return (
            bool(give or get) and all(owners.get(x) != mine for x in give) and all(owners.get(x) == mine for x in get)
        )
    return False


def revalidate(p: Proposal, league: LeagueState, teamless: frozenset[str] = frozenset()) -> list[str]:
    """Re-check a proposal against the CURRENT league state right before handing the owner the steps.
    `teamless`: players on no current NBA roster (never acquire them)."""
    problems: list[str] = []
    owners = _rostered(league)
    mine = league.my_team_key
    pay = p.payload
    kind = effective_kind(p.kind, pay)
    if kind in ("add_drop", "waiver_claim"):
        add, drop = pay.get("add"), pay.get("drop")
        if not add:
            problems.append("payload.add is required")
        elif add in owners:
            problems.append(f"{pay.get('add_name', add)} is no longer available (rostered by {owners[add]})")
        elif add in teamless:
            problems.append(f"{pay.get('add_name', add)} has no NBA team (not on any current NBA roster)")
        if drop and owners.get(drop) != mine:
            problems.append(f"{pay.get('drop_name', drop)} is not on your roster")
        active = [x for x in league.my_team.players if x.selected not in IL_SLOTS]
        if not drop and len(active) >= league.roster_size():
            problems.append("roster is full: a drop is required")
    elif kind == "il_activation":
        if not pay.get("activate"):
            problems.append("payload.activate is required")
        else:
            problems.extend(_il_problems(pay, league))
    elif p.kind == "trade":
        partner = pay.get("partner")
        for pid in pay.get("give", []):
            if owners.get(pid) != mine:
                problems.append(f"you no longer own {pid}")
        for pid in pay.get("get", []):
            if owners.get(pid) != partner:
                problems.append(f"{partner} no longer owns {pid}")
            elif pid in teamless:
                problems.append(f"{pid} has no NBA team (not on any current NBA roster)")
    return problems


def _il_problems(pay: Mapping[str, Any], league: LeagueState) -> list[str]:
    problems = validate_activation(league.my_team.players, pay["activate"], pay.get("drop"), league.roster_size())
    labels = {pay["activate"]: pay.get("activate_name"), pay.get("drop"): pay.get("drop_name")}
    for pid, label in labels.items():  # name players the roster no longer has instead of showing raw ids
        if pid and label:
            problems = [x.replace(f"{pid} is not on your roster", f"{label} is not on your roster") for x in problems]
    return problems


def _il_activation_steps(pay: Mapping[str, Any]) -> list[str]:
    act, drop = pay.get("activate_name", pay.get("activate")), pay.get("drop_name", pay.get("drop"))
    effective = pay.get("effective_et")
    steps = ["Open the Yahoo Fantasy app → your league → My Team."]
    if effective:
        steps.append(
            f"Timing: make this move on **{effective}** (ET), before that day's tip-offs; "
            "do not do it earlier, so nobody loses a game."
        )
    elif pay.get("timing"):
        steps.append(f"Timing: {pay['timing']}")
    if drop:
        steps.append(
            f"Drop **{drop}** first (tap him → Drop): Yahoo needs the open spot before **{act}** can leave IL."
        )
    steps.append(f"Move **{act}** from IL to an active or bench slot (tap his slot → Move → pick a slot).")
    steps.append("This uses no weekly add. Confirm, then tell the GM it's done (it will verify on the next sync).")
    return steps


def _generic_steps(p: Proposal) -> list[str]:
    return [
        f"FBM has no step template for proposal kind '{p.kind}'. The owner-approved proposal was:",
        f"**{p.title}**",
        p.summary,
        f"Payload: {p.payload}",
        "Make this change in Yahoo yourself, then tell the GM it's done (confirm_done).",
    ]


def _add_drop_steps(pay: Mapping[str, Any], waiver: bool) -> list[str]:
    add, drop = pay.get("add_name", pay.get("add")), pay.get("drop_name", pay.get("drop"))
    steps = [
        "Open the Yahoo Fantasy app → your league → Players.",
        f"Search for **{add}** and tap **{'Claim' if waiver else 'Add'}**.",
    ]
    if drop:
        steps.append(f"When asked who to drop, choose **{drop}**.")
    steps.append("Confirm, then tell the GM it's done (it will verify on the next sync).")
    return steps


def _trade_steps(pay: Mapping[str, Any]) -> list[str]:
    return [
        f"Open Yahoo Fantasy → league → **{pay.get('partner_name', pay.get('partner'))}**'s team → Propose Trade.",
        f"Send: **{', '.join(pay.get('give_names', pay.get('give', [])))}**.",
        f"Request: **{', '.join(pay.get('get_names', pay.get('get', [])))}**.",
        f"Trade note (optional): {pay.get('note', '')}".rstrip(),
        "Submit, then tell the GM it's sent.",
    ]


def roster_player_from_values(pid: str, values, eligibility=None) -> RosterPlayer:
    return roster_player(pid, values, eligibility)


def owner_steps(p: Proposal, league: LeagueState, teamless: frozenset[str] = frozenset()) -> StepsResult:
    """Approved proposal -> exact steps for the owner. Strategy changes are FBM memory only, recorded directly."""
    if already_made(p, league):
        return StepsResult(
            "awaiting_owner",
            {
                "already_made": True,
                "manual_steps": [],
                "note": "Your roster already reflects this move: ask the owner to confirm it's done (confirm_done).",
            },
        )
    problems = revalidate(p, league, teamless)
    if problems:
        return StepsResult("failed", {"revalidation": problems})
    pay = p.payload
    kind = effective_kind(p.kind, pay)
    if kind == "strategy":
        data = memory.write_strategy(
            list(pay.get("punts", [])), list(pay.get("targets", [])), str(pay.get("notes", "")), p.ref
        )
        return StepsResult("done", {"strategy": data, "note": "strategy recorded in FBM memory (no league change)"})
    detail: dict[str, Any] = {}
    if kind in ("add_drop", "waiver_claim"):
        steps = _add_drop_steps(pay, waiver=kind == "waiver_claim")
    elif kind == "il_activation":
        steps = _il_activation_steps(pay)
    elif kind == "trade":
        steps = _trade_steps(pay)
    else:  # an owner-approved proposal with no template is not a failed re-validation: hand over generic steps
        steps = _generic_steps(p)
        detail["warning"] = f"no step template for proposal kind '{p.kind}'; generic steps returned"
    if league.source == "demo":
        steps.append("Demo league: confirming it's done applies this move to the demo roster.")
    return StepsResult("awaiting_owner", {**detail, "manual_steps": steps})


def simulate_in_demo(p: Proposal, league: LeagueState, values=None, eligibility=None) -> dict[str, Any]:
    """Demo league only: apply the move the owner confirmed (stands in for the real Yahoo league)."""
    if league.source != "demo":
        return {}
    pay = p.payload
    kind = effective_kind(p.kind, pay)
    if kind in ("add_drop", "waiver_claim"):
        team = league.my_team
        if pay.get("drop"):
            team.players = [x for x in team.players if x.player_id != pay["drop"]]
        team.players.append(roster_player_from_values(pay["add"], values, eligibility))
        if kind == "waiver_claim":
            current = league.waiver_priority or default_waiver_priority(league)
            league.waiver_priority = rotate_waiver_priority(current, league.my_team_key)
    elif kind == "il_activation":
        team = league.my_team
        if pay.get("drop"):
            team.players = [x for x in team.players if x.player_id != pay["drop"]]
        for x in team.players:
            if x.player_id == pay["activate"]:
                x.selected = "BN"
    elif kind == "trade":
        mine, partner = league.my_team, league.team(pay["partner"])
        give, get = set(pay.get("give", [])), set(pay.get("get", []))
        moving_out = [x for x in mine.players if x.player_id in give]
        moving_in = [x for x in partner.players if x.player_id in get]
        mine.players = [x for x in mine.players if x.player_id not in give] + moving_in
        partner.players = [x for x in partner.players if x.player_id not in get] + moving_out
        for x in [*moving_in, *moving_out]:
            x.selected = "BN"
    else:
        return {}
    save_league(league)
    return {"demo_league": "updated to reflect the owner's move"}


def lineup_changes(league: LeagueState, assignments: Mapping[str, str]) -> dict[str, tuple[str, str]]:
    return {
        p.player_id: (p.selected, assignments[p.player_id])
        for p in league.my_team.players
        if p.player_id in assignments and p.selected != assignments[p.player_id]
    }


def lineup_steps(league: LeagueState, assignments: Mapping[str, str], day: date) -> dict[str, Any]:
    """Recommended lineup -> exact steps for the owner (nothing is changed)."""
    changes = lineup_changes(league, assignments)
    names = {p.player_id: p.name for p in league.my_team.players}
    described = [f"{names[pid]}: {old} → {new}" for pid, (old, new) in changes.items()]
    if not described:
        return {"changes": [], "manual_steps": [], "note": "your lineup already matches the recommendation"}
    steps = [f"Open Yahoo Fantasy → My Team → date **{day.isoformat()}** → Edit lineup:"]
    steps += [f"- {d}" for d in described]
    steps.append("Save, then tell the GM it's done (optional; it re-reads your lineup on the next sync).")
    return {"changes": described, "manual_steps": steps}


def simulate_lineup_in_demo(league: LeagueState, assignments: Mapping[str, str]) -> bool:
    """Demo league only: the owner confirmed they set the recommended lineup."""
    if league.source != "demo":
        return False
    for p in league.my_team.players:
        p.selected = assignments.get(p.player_id, p.selected)
    save_league(league)
    return True
