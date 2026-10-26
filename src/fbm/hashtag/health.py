"""Is Hashtag Basketball healthy enough for FBM to make suggestions? (Owner decision 4: no fallback.)

HT is healthy only when, within `hashtag.max_age_hours`:
- the newest import of every projection view and of the H2H Planner succeeded (status ok), and no later attempt on
  those pages reported `session_expired` or `layout_changed`;
- the latest league sync check (HT's copy of the league vs the Yahoo snapshot) is ok;
- unmatched projected players (HT top 300, ROS view) <= `hashtag.max_unmatched`;
- a signed-in session is stored.

When `[hashtag] enabled = false` (demo league / no HT) the gate does not apply: `status().enabled` is False and
`require_healthy` returns without raising.
"""

from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from fbm.constants import C
from fbm.hashtag import VIEWS

ERROR_CODE = "HT_UNHEALTHY"
BAD_STATUSES = ("session_expired", "layout_changed", "league_mismatch", "error")

FIX_LOGIN = "Sign in again: `fbm hashtag login` (your own browser sign-in), then `fbm hashtag import`."
FIX_IMPORT = "Run `fbm hashtag import` (or `refresh_data`) after games are final."
FIX_LAYOUT = "Hashtag changed a page layout: the FBM parser for that page needs an update (see `fbm hashtag check`)."
FIX_SYNC = "Refresh the league on Hashtag (Premium > League Home > refresh) and re-run `fbm hashtag import`."
FIX_IDS = "Add the unmatched players to config/hashtag_id_overrides.toml (see `fbm hashtag check`)."


class HTUnhealthyError(RuntimeError):
    def __init__(self, status: HTHealth):
        super().__init__(f"{ERROR_CODE}: " + "; ".join(status.reasons))
        self.status = status


@dataclass
class HTHealth:
    enabled: bool
    healthy: bool
    reasons: list[str] = field(default_factory=list)
    fix: list[str] = field(default_factory=list)
    last_ok: dict[str, str] = field(default_factory=dict)  # page[:view] -> newest ok finished_at (UTC ISO)
    checked_at: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    def gate_payload(self) -> dict[str, Any]:
        """What a suggestion tool returns instead of a suggestion while HT is unhealthy."""
        return {
            "error_code": ERROR_CODE,
            "message": "No suggestions: Hashtag Basketball data is not healthy (owner rule: no fallback).",
            "reasons": self.reasons,
            "fix": self.fix,
            "last_ok": self.last_ok,
        }


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _latest(conn: sqlite3.Connection, page: str, view: str | None = None) -> tuple[sqlite3.Row | None, str | None]:
    """(newest log row for the page[/view], newest ok finished_at)."""
    vclause, args = ("AND view = ?", (page, view)) if view else ("", (page,))
    last = conn.execute(
        f"SELECT * FROM hashtag_import_log WHERE page = ? {vclause} ORDER BY started_at DESC, id DESC LIMIT 1", args
    ).fetchone()
    ok = conn.execute(
        f"SELECT MAX(COALESCE(finished_at, started_at)) FROM hashtag_import_log WHERE page = ? {vclause} "
        "AND status = 'ok'",
        args,
    ).fetchone()[0]
    return last, ok


def _has_session() -> bool:
    try:
        from fbm import credentials

        return bool(credentials.get_large(credentials.HASHTAG_WEB_SESSION))
    except Exception:  # noqa: BLE001 - keyring backend missing counts as no session
        return False


def status(
    conn: sqlite3.Connection,
    enabled: bool | None = None,
    now: datetime | None = None,
    check_session: bool = True,
) -> HTHealth:
    if enabled is None:
        from fbm.settings import load_settings

        enabled = load_settings().hashtag_enabled
    now = now or datetime.now(UTC)
    out = HTHealth(enabled=bool(enabled), healthy=False, checked_at=now.isoformat(timespec="seconds"))
    if not enabled:
        out.reasons.append("Hashtag is not enabled ([hashtag] enabled = false).")
        return out
    max_age = timedelta(hours=float(C("hashtag.max_age_hours")))
    fixes: list[str] = []

    if check_session and not _has_session():
        out.reasons.append("No Hashtag sign-in stored.")
        fixes.append(FIX_LOGIN)

    checks: list[tuple[str, str | None]] = [("projections", v) for v in VIEWS] + [("h2h_planner", None)]
    for page, view in checks:
        label = f"{page}:{view}" if view else page
        last, ok = _latest(conn, page, view)
        if ok:
            out.last_ok[label] = ok
        ok_dt = _parse(ok)
        if last is not None and last["status"] in BAD_STATUSES and (ok is None or last["started_at"] > ok):
            out.reasons.append(f"{label}: last import {last['status']} ({last['detail'] or 'no detail'}).")
            fixes.append(
                FIX_LOGIN
                if last["status"] == "session_expired"
                else FIX_LAYOUT
                if last["status"] == "layout_changed"
                else FIX_IMPORT
            )
        elif ok_dt is None:
            out.reasons.append(f"{label}: never imported.")
            fixes.append(FIX_IMPORT)
        elif now - ok_dt > max_age:
            out.reasons.append(f"{label}: newest import is {(now - ok_dt).total_seconds() / 3600:.0f} h old.")
            fixes.append(FIX_IMPORT)

    sync, sync_ok = _latest(conn, "league_sync")
    if sync is None:
        out.reasons.append("league_sync: HT's copy of the league was never checked against Yahoo.")
        fixes.append(FIX_IMPORT)
    elif sync["status"] != "ok":
        out.reasons.append(f"league_sync: {sync['status']} ({sync['detail'] or 'no detail'}).")
        fixes.append(FIX_SYNC)
    elif sync_ok:
        out.last_ok["league_sync"] = sync_ok

    from fbm.hashtag.store import unmatched

    missing = unmatched(conn, "ROS")
    limit = int(C("hashtag.max_unmatched"))
    if len(missing) > limit:
        names = ", ".join(m["name"] or m["ht_id"] for m in missing[:5])
        out.reasons.append(f"{len(missing)} projected players have no ESPN id (limit {limit}), e.g. {names}.")
        fixes.append(FIX_IDS)

    out.fix = list(dict.fromkeys(fixes))
    out.healthy = not out.reasons
    return out


def require_healthy(conn: sqlite3.Connection, enabled: bool | None = None, now: datetime | None = None) -> HTHealth:
    """Raise HTUnhealthyError when HT is enabled but unhealthy; otherwise return the status (enabled or not)."""
    st = status(conn, enabled, now)
    if st.enabled and not st.healthy:
        raise HTUnhealthyError(st)
    return st
