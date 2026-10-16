"""Yahoo write operations (XML bodies). Callers are responsible for authorization/guardrails."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from xml.sax.saxutils import escape

from fbm.yahoo.client import YahooAPIError, YahooClient
from fbm.yahoo.roster import get_roster


def roster_positions_xml(day: date, assignments: Mapping[str, str]) -> str:
    if not assignments:
        raise ValueError("assignments must not be empty")
    players = "".join(
        f"<player><player_key>{escape(pk)}</player_key><position>{escape(pos)}</position></player>"
        for pk, pos in assignments.items()
    )
    return (
        '<?xml version="1.0"?>'
        "<fantasy_content><roster>"
        "<coverage_type>date</coverage_type>"
        f"<date>{day.isoformat()}</date>"
        f"<players>{players}</players>"
        "</roster></fantasy_content>"
    )


def set_positions(client: YahooClient, team_key: str, day: date, assignments: Mapping[str, str]) -> None:
    client.put_xml(f"team/{team_key}/roster", roster_positions_xml(day, assignments))


@dataclass(frozen=True)
class WriteCheck:
    ok: bool | None
    detail: str


def verify_write_scope(client: YahooClient, team_key: str, day: date) -> WriteCheck:
    """Re-submit one player's *current* position (a no-op) to prove the app has write scope."""
    before = get_roster(client, team_key, day)
    if not before.is_editable:
        return WriteCheck(
            None, f"Roster for {day} is not editable (locked or out of season); try a later date."
        )
    candidates = [p for p in before.players if p.is_editable and p.selected_position]
    if not candidates:
        return WriteCheck(None, f"No editable players on {day}.")
    bench = [p for p in candidates if p.selected_position == "BN"]
    target = (bench or candidates)[0]
    try:
        set_positions(client, team_key, day, {target.player_key: target.selected_position})
    except YahooAPIError as err:
        if err.is_scope_error or err.status in (401, 403):
            return WriteCheck(False, f"Write rejected ({err.status}): {err.description}")
        raise
    after = get_roster(client, team_key, day)
    if after.assignments() != before.assignments():
        return WriteCheck(False, "Write succeeded but the lineup changed unexpectedly — inspect immediately.")
    return WriteCheck(
        True, f"Write scope confirmed (no-op re-save of {target.name} at {target.selected_position})."
    )
