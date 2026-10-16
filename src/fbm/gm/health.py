"""System health heartbeat: data freshness, Yahoo access, proposals, missed scheduled runs, notifications, storage.

Check functions are pure over explicit inputs; `gather()` reads the real DB/files. Each check returns
{check, status: OK|WARN|FAIL, detail, fix}. Never reads out credential values (presence booleans only).
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from fbm.constants import C

OK, WARN, FAIL = "OK", "WARN", "FAIL"
_RANK = {OK: 0, WARN: 1, FAIL: 2}
DATA_JOBS = ("schedule", "boxscores", "injuries", "news")


def result(check: str, status: str, detail: str, fix: str = "") -> dict[str, str]:
    return {"check": check, "status": status, "detail": detail, "fix": fix}


def overall(checks: list[dict[str, str]]) -> str:
    return max((c["status"] for c in checks), key=_RANK.__getitem__, default=OK)


def _parse(ts: str) -> datetime:
    dt = datetime.fromisoformat(ts)
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _age_h(ts: str, now: datetime) -> float:
    return (now - _parse(ts)).total_seconds() / 3600


# ------------------------------------------------------------------ pure checks


def season_phase(regular_dates: list[str], today: date) -> str:
    """'regular' between first and last regular-season game dates (inclusive), else 'preseason'/'offseason'."""
    if not regular_dates:
        return "offseason"
    first, last = date.fromisoformat(min(regular_dates)), date.fromisoformat(max(regular_dates))
    if today < first:
        return "preseason"
    return "regular" if today <= last else "offseason"


def check_data_freshness(
    sync_runs: dict[str, str], phase: str, games_last_day: int, now: datetime
) -> list[dict[str, str]]:
    if games_last_day > 0:
        limit, why = C("health.data_max_age_h_gameday"), f"{games_last_day} NBA games in the last day"
    elif phase == "regular":
        limit, why = C("health.data_max_age_h"), "regular season"
    else:
        limit, why = C("health.data_max_age_h_offseason"), phase
    out = []
    for job in DATA_JOBS:
        name = f"ESPN {job}"
        runs = [ts for j, ts in sync_runs.items() if j == job or j.startswith(f"{job}:")]
        if not runs:
            out.append(result(name, FAIL, "never synced", "Run `fbm data sync` (or `refresh_data`)."))
            continue
        age = _age_h(max(runs, key=_parse), now)
        detail = f"{age:.1f}h old (limit {limit}h, {why})"
        if age <= limit:
            out.append(result(name, OK, detail))
        else:
            status = FAIL if age > 3 * limit else WARN
            out.append(result(name, status, detail, "Run `refresh_data` / `fbm data sync`."))
    return out


def check_yahoo(
    source: str,
    creds: dict[str, bool],
    league_configured: bool,
    demo_file_exists: bool,
    snapshot_age_min: float | None,
    refresh_error: str,
    unmatched_count: int,
) -> list[dict[str, str]]:
    out = []
    missing = [k for k, v in creds.items() if not v]
    cred_status = OK if not missing else (FAIL if source == "yahoo" else WARN)
    out.append(
        result(
            "Yahoo credentials",
            cred_status,
            "all present" if not missing else f"missing: {', '.join(missing)}",
            "" if not missing else "Run `fbm auth` (stores them in your OS credential store).",
        )
    )
    out.append(
        result(
            "League configured",
            OK if league_configured else (FAIL if source == "yahoo" else WARN),
            "config/league.toml present" if league_configured else "config/league.toml missing",
            "" if league_configured else "After Yahoo access works: `fbm auth --reset`, then `fbm league select`.",
        )
    )
    if source != "yahoo":
        out.append(
            result(
                "League source",
                WARN,
                f"source={source}: Yahoo API approval pending",
                'Switch the [league] source to "yahoo" in config/settings.toml once approved.',
            )
        )
        out.append(
            result(
                "Demo league",
                OK if demo_file_exists else FAIL,
                "data/demo_league.json present" if demo_file_exists else "data/demo_league.json missing",
                "" if demo_file_exists else "Run `fbm demo build`.",
            )
        )
        return out
    max_age = C("yahoo.snapshot_max_age_min")
    if snapshot_age_min is None:
        out.append(result("Yahoo snapshot", FAIL, "no snapshot", "Run `fbm snapshot`."))
    elif snapshot_age_min > max_age:
        status = FAIL if snapshot_age_min > 24 * 60 else WARN
        out.append(
            result("Yahoo snapshot", status, f"{snapshot_age_min:.0f} min old (max {max_age})", "Run `fbm snapshot`.")
        )
    else:
        out.append(result("Yahoo snapshot", OK, f"{snapshot_age_min:.0f} min old (max {max_age})"))
    if refresh_error:
        out.append(
            result("Yahoo refresh", FAIL, f"last refresh failed: {refresh_error[:200]}", "Check token / re-auth.")
        )
    else:
        out.append(result("Yahoo refresh", OK, "no recent refresh error"))
    out.append(
        result(
            "Yahoo ID matching",
            OK if unmatched_count == 0 else WARN,
            f"{unmatched_count} unmatched players",
            "" if unmatched_count == 0 else "Add overrides in config/yahoo_id_overrides.toml.",
        )
    )
    return out


def check_proposals(rows: list[dict[str, Any]], now: datetime) -> list[dict[str, str]]:
    """rows: {ref, status, created_at, expires_at, decided_at, executed_at}."""
    from fbm.gm.proposals import OPEN

    manual_h, approved_h = C("health.manual_stale_h"), C("health.approved_stale_h")
    manual = [
        r["ref"]
        for r in rows
        if r["status"] == "awaiting_owner" and _age_h(r["executed_at"] or r["created_at"], now) > manual_h
    ]
    approved = [
        r["ref"]
        for r in rows
        if r["status"] == "approved" and _age_h(r["decided_at"] or r["created_at"], now) > approved_h
    ]
    expired = [r["ref"] for r in rows if r["status"] in OPEN and _parse(r["expires_at"]) <= now]
    return [
        result(
            "Proposals awaiting the owner",
            WARN if manual else OK,
            f"older than {manual_h}h: {', '.join(manual)}" if manual else "none stale",
            "Owner: make the move in Yahoo, then confirm it (`confirm_done`)." if manual else "",
        ),
        result(
            "Proposals approved, no steps yet",
            WARN if approved else OK,
            f"older than {approved_h}h: {', '.join(approved)}" if approved else "none",
            "Run `proposal_steps` (re-validates, then gives the owner exact steps)." if approved else "",
        ),
        result(
            "Proposals past expiry",
            WARN if expired else OK,
            f"still open: {', '.join(expired)}" if expired else "none",
            "Run `list_proposals` (expires stale ones)." if expired else "",
        ),
    ]


_LINEUP_RE = re.compile(r"^## .*· Lineup (\d{4}-\d{2}-\d{2})", re.M)


def check_journal(
    journal: dict[date, str], phase: str, local_today: date, game_days: set[date], season_start: date | None
) -> dict[str, str]:
    """journal: local date -> file text. Daily entries keyed by local date; lineups by ET game date in the title."""
    if phase != "regular":
        return result("Scheduled runs", OK, f"{phase}: no scheduled runs expected yet")
    days = C("health.journal_lookback_days")
    start = local_today - timedelta(days=days)
    if season_start:
        start = max(start, season_start)
    window = [start + timedelta(days=i) for i in range((local_today - start).days)]
    missing_daily = [d for d in window if not journal.get(d, "").count("\n## ")]
    lineups = {date.fromisoformat(m) for text in journal.values() for m in _LINEUP_RE.findall(text)}
    missing_lineup = [d for d in window if d in game_days and d not in lineups]
    if not missing_daily and not missing_lineup:
        return result("Scheduled runs", OK, f"journal complete for last {len(window)} day(s)")
    parts = []
    if missing_daily:
        parts.append("no daily GM entry: " + ", ".join(d.isoformat() for d in missing_daily))
    if missing_lineup:
        parts.append("no lineup entry for game day: " + ", ".join(d.isoformat() for d in missing_lineup))
    status = FAIL if len(missing_daily) >= C("health.journal_fail_days") else WARN
    return result("Scheduled runs", status, "; ".join(parts), "Check the PC was awake / scheduled tasks ran.")


def check_notifications(email_enabled: bool, outbox: list[dict[str, Any]], now: datetime) -> list[dict[str, str]]:
    hours = C("health.outbox_lookback_h")
    failed = [o for o in outbox if o["status"] == "failed" and _age_h(o["created_at"], now) <= hours]
    return [
        result(
            "Email",
            OK,
            "enabled" if email_enabled else "disabled (reports live in reports/ + your agent session)",
        ),
        result(
            "Outbox failures",
            WARN if failed else OK,
            f"{len(failed)} failed in last {hours}h: {failed[0]['subject'][:60]}"
            if failed
            else f"none in last {hours}h",
            "See `fbm notify outbox`." if failed else "",
        ),
    ]


def check_storage(data_dir_exists: bool, db_bytes: int | None) -> list[dict[str, str]]:
    if not data_dir_exists:
        return [result("Storage", FAIL, "data/ directory missing", "Run `fbm data sync`.")]
    if db_bytes is None:
        return [result("Storage", FAIL, "data/fbm.db missing", "Run `fbm data sync`.")]
    mb, limit = db_bytes / 1e6, C("health.db_max_mb")
    return [result("Storage", WARN if mb > limit else OK, f"fbm.db {mb:.1f} MB (warn above {limit} MB)")]


# ------------------------------------------------------------------ gather (real DB / files)


def _cred_presence() -> dict[str, bool]:
    from fbm import credentials

    names = (credentials.YAHOO_CLIENT_ID, credentials.YAHOO_CLIENT_SECRET, credentials.YAHOO_REFRESH_TOKEN)
    return {n: credentials.get(n) is not None for n in names}


def _snapshot_age_min(path: Path, now: datetime) -> float | None:
    try:
        fetched = json.loads(path.read_text(encoding="utf-8")).get("fetched_at")
    except (OSError, ValueError):
        return None
    return (now - _parse(fetched)).total_seconds() / 60 if fetched else None


def _read_journal(journal_dir: Path, since: date) -> dict[date, str]:
    out: dict[date, str] = {}
    if journal_dir.exists():
        for f in journal_dir.glob("*.md"):
            try:
                d = date.fromisoformat(f.stem)
            except ValueError:
                continue
            if d >= since:
                out[d] = "\n" + f.read_text(encoding="utf-8")
    return out


def gather(
    conn: sqlite3.Connection | None = None,
    db_path: Path | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    from fbm.config import DATA_DIR, LEAGUE_CONFIG_PATH
    from fbm.gm import memory
    from fbm.league import yahoo_league
    from fbm.league.demo import DEMO_PATH
    from fbm.settings import load_settings
    from fbm.store import db
    from fbm.timeutil import nba_today

    now = now or datetime.now(UTC)
    today_et = nba_today(now)
    local_today = now.astimezone().date()
    db_path = Path(db_path or db.DB_PATH)
    settings = load_settings()
    checks: list[dict[str, str]] = []
    checks += check_storage(DATA_DIR.exists(), db_path.stat().st_size if db_path.exists() else None)
    own = conn is None
    if own:
        if not db_path.exists():
            return {"overall": FAIL, "phase": "unknown", "checks": checks, "as_of": now.isoformat()}
        conn = db.connect(db_path)
    try:
        sync_runs = {r[0]: r[1] for r in conn.execute("SELECT job, last_run FROM sync_log")}
        regular = [
            r[0]
            for r in conn.execute(
                "SELECT DISTINCT et_date FROM games WHERE season_type = 2 AND season = (SELECT MAX(season) FROM games)"
            )
        ]
        phase = season_phase(regular, today_et)
        yesterday = (today_et - timedelta(days=1)).isoformat()
        games_last_day = conn.execute(
            "SELECT COUNT(*) FROM games WHERE et_date BETWEEN ? AND ? AND (completed = 1 OR state != 'pre')",
            (yesterday, today_et.isoformat()),
        ).fetchone()[0]
        prop_rows = [
            {
                "ref": f"P-{r['id']}",
                "status": r["status"],
                "created_at": r["created_at"],
                "expires_at": r["expires_at"],
                "decided_at": r["decided_at"],
                "executed_at": r["executed_at"],
            }
            for r in conn.execute(
                "SELECT * FROM proposals WHERE status IN "
                "('pending_review','pending_approval','approved','awaiting_owner')"
            )
        ]
        since = (now - timedelta(hours=C("health.outbox_lookback_h"))).isoformat()
        outbox = [
            dict(r)
            for r in conn.execute("SELECT created_at, subject, status FROM outbox WHERE created_at >= ?", (since,))
        ]
        unmatched = 0
        if settings.league_source == "yahoo":
            from fbm.yahoo.ids import unmatched as _unmatched

            try:
                unmatched = len(_unmatched(conn))
            except sqlite3.Error:
                unmatched = 0
    finally:
        if own:
            conn.close()

    checks += check_data_freshness(sync_runs, phase, games_last_day, now)
    checks += check_yahoo(
        settings.league_source,
        _cred_presence(),
        LEAGUE_CONFIG_PATH.exists(),
        DEMO_PATH.exists(),
        _snapshot_age_min(yahoo_league.SNAPSHOT_PATH, now),
        yahoo_league.last_refresh_error(),
        unmatched,
    )
    checks += check_proposals(prop_rows, now)
    lookback = C("health.journal_lookback_days")
    journal = _read_journal(memory.JOURNAL_DIR, local_today - timedelta(days=lookback + 1))
    game_days = {date.fromisoformat(d) for d in regular}
    checks.append(
        check_journal(journal, phase, local_today, game_days, date.fromisoformat(min(regular)) if regular else None)
    )
    checks += check_notifications(settings.email_enabled, outbox, now)
    return {"overall": overall(checks), "phase": phase, "as_of": now.isoformat(), "checks": checks}
