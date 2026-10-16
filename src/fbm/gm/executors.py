"""Executors: apply approved moves. Demo league -> applied to demo JSON. Yahoo -> API if write scope is confirmed,
otherwise exact manual instructions for the owner (status awaiting_manual until the owner confirms)."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from fbm.config import RAW_DIR
from fbm.gm import memory
from fbm.gm.proposals import Proposal
from fbm.league.demo import default_waiver_priority, roster_player, rotate_waiver_priority, save_league
from fbm.league.model import LeagueState, RosterPlayer


@dataclass
class ExecutionResult:
    status: str  # executed | awaiting_manual | failed
    detail: dict[str, Any] = field(default_factory=dict)


def yahoo_write_enabled() -> bool:
    path = RAW_DIR / "write_check.json"
    return path.exists() and json.loads(path.read_text(encoding="utf-8")).get("ok") is True


def _rostered(league: LeagueState) -> dict[str, str]:
    return {p.player_id: t.team_key for t in league.teams for p in t.players}


def revalidate(p: Proposal, league: LeagueState) -> list[str]:
    """Re-check a proposal against the CURRENT league state right before execution."""
    problems: list[str] = []
    owners = _rostered(league)
    mine = league.my_team_key
    pay = p.payload
    if p.kind in ("add_drop", "waiver_claim"):
        add, drop = pay.get("add"), pay.get("drop")
        if not add:
            problems.append("payload.add is required")
        elif add in owners:
            problems.append(f"{pay.get('add_name', add)} is no longer available (rostered by {owners[add]})")
        if drop and owners.get(drop) != mine:
            problems.append(f"{pay.get('drop_name', drop)} is not on your roster")
        if not drop and len(league.my_team.players) >= league.roster_size():
            problems.append("roster is full: a drop is required")
    elif p.kind == "trade":
        partner = pay.get("partner")
        for pid in pay.get("give", []):
            if owners.get(pid) != mine:
                problems.append(f"you no longer own {pid}")
        for pid in pay.get("get", []):
            if owners.get(pid) != partner:
                problems.append(f"{partner} no longer owns {pid}")
    return problems


def _manual_add_drop(pay: Mapping[str, Any], waiver: bool) -> list[str]:
    add, drop = pay.get("add_name", pay.get("add")), pay.get("drop_name", pay.get("drop"))
    steps = [
        "Open the Yahoo Fantasy app → your league → Players.",
        f"Search for **{add}** and tap **{'Claim' if waiver else 'Add'}**.",
    ]
    if drop:
        steps.append(f"When asked who to drop, choose **{drop}**.")
    steps.append("Confirm, then tell the GM it's done (it will verify on the next sync).")
    return steps


def _manual_trade(pay: Mapping[str, Any]) -> list[str]:
    return [
        f"Open Yahoo Fantasy → league → **{pay.get('partner_name', pay.get('partner'))}**'s team → Propose Trade.",
        f"Send: **{', '.join(pay.get('give_names', pay.get('give', [])))}**.",
        f"Request: **{', '.join(pay.get('get_names', pay.get('get', [])))}**.",
        f"Trade note (optional): {pay.get('note', '')}".rstrip(),
        "Submit, then tell the GM it's sent.",
    ]


def roster_player_from_values(pid: str, values, eligibility=None) -> RosterPlayer:
    return roster_player(pid, values, eligibility)


def execute(p: Proposal, league: LeagueState, values=None, eligibility=None) -> ExecutionResult:
    problems = revalidate(p, league)
    if problems:
        return ExecutionResult("failed", {"revalidation": problems})
    pay = p.payload
    if p.kind == "strategy":
        data = memory.write_strategy(
            list(pay.get("punts", [])), list(pay.get("targets", [])), str(pay.get("notes", "")), p.ref
        )
        return ExecutionResult("executed", {"strategy": data})

    if league.source == "demo":
        if p.kind in ("add_drop", "waiver_claim"):
            team = league.my_team
            if pay.get("drop"):
                team.players = [x for x in team.players if x.player_id != pay["drop"]]
            team.players.append(roster_player_from_values(pay["add"], values, eligibility))
            if p.kind == "waiver_claim":
                current = league.waiver_priority or default_waiver_priority(league)
                league.waiver_priority = rotate_waiver_priority(current, league.my_team_key)
        elif p.kind == "trade":
            mine, partner = league.my_team, league.team(pay["partner"])
            give, get = set(pay.get("give", [])), set(pay.get("get", []))
            moving_out = [x for x in mine.players if x.player_id in give]
            moving_in = [x for x in partner.players if x.player_id in get]
            mine.players = [x for x in mine.players if x.player_id not in give] + moving_in
            partner.players = [x for x in partner.players if x.player_id not in get] + moving_out
            for x in [*moving_in, *moving_out]:
                x.selected = "BN"
        else:
            return ExecutionResult("failed", {"error": f"no demo executor for {p.kind}"})
        save_league(league)
        return ExecutionResult("executed", {"applied_to": "demo league"})

    # Yahoo: transaction writes are not available via the API for this app yet -> manual instructions.
    if p.kind in ("add_drop", "waiver_claim"):
        steps = _manual_add_drop(pay, waiver=p.kind == "waiver_claim")
    elif p.kind == "trade":
        steps = _manual_trade(pay)
    else:
        return ExecutionResult("failed", {"error": f"no executor for {p.kind}"})
    return ExecutionResult("awaiting_manual", {"manual_steps": steps})


def apply_lineup(league: LeagueState, assignments: Mapping[str, str], day: date) -> ExecutionResult:
    """Auto-allowed action: set the day's lineup (demo: saved; Yahoo: API if write scope, else manual steps)."""
    team = league.my_team
    changes = {
        p.player_id: (p.selected, assignments[p.player_id])
        for p in team.players
        if p.player_id in assignments and p.selected != assignments[p.player_id]
    }
    if not changes:
        return ExecutionResult("executed", {"changes": [], "note": "lineup already optimal"})
    names = {p.player_id: p.name for p in team.players}
    described = [f"{names[pid]}: {old} → {new}" for pid, (old, new) in changes.items()]
    if league.source == "demo":
        for p in team.players:
            p.selected = assignments.get(p.player_id, p.selected)
        save_league(league)
        return ExecutionResult("executed", {"changes": described, "applied_to": "demo league"})
    if yahoo_write_enabled():
        from fbm.config import load_league_config
        from fbm.yahoo.client import YahooClient
        from fbm.yahoo.writes import set_positions

        keys = {p.player_id: p.yahoo_key for p in team.players}
        payload = {keys[pid]: new for pid, (_old, new) in changes.items() if keys.get(pid)}
        with YahooClient() as client:
            set_positions(client, load_league_config().team_key, day, payload)
        return ExecutionResult("executed", {"changes": described, "applied_to": "yahoo"})
    steps = [f"Open Yahoo Fantasy → My Team → date **{day.isoformat()}** → Edit lineup:"] + [
        f"- {d}" for d in described
    ]
    return ExecutionResult("awaiting_manual", {"changes": described, "manual_steps": steps})
