"""One-off migrations of the owner's personal data (local-only state) to new FBM structures.

`overhaul` (2026-10-26, Hashtag data layer + decision calibration):
1. Backup: `fbm backup` zip (memory, reports, settings, league, fbm.db) before anything changes.
2. Schema: new tables are created by `db.connect()` (additive; old tables are kept read-only).
3. Settings: `[hashtag] enabled = true` + `league_id` in config/settings.toml (owner decision: HT is the data layer;
   suggestions pause until HT is healthy, i.e. after `fbm hashtag login` + `fbm hashtag import`).
4. Decisions: every past proposal the owner actually decided (done / rejected / awaiting_owner) becomes a decision
   row (trigger "migration") with the proposed move and standing pat as options, so the replay grader and the policy
   tuner learn from the season so far. Failed/expired proposals (never decided, or re-filed) are skipped; strategy
   proposals stay in memory/strategy.json (season multipliers derive from it).
5. Yahoo results: finals + both teams' daily lineups of finished weeks are cached, then legacy matchup forecasts are
   re-graded against Yahoo FINAL results (the 2026-10-26 grading fix).
6. Journal entry with what changed.

Idempotent: re-running skips decisions already migrated (same proposal_ref) and rewrites the [hashtag] section only.
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from fbm.analytics.week import fantasy_week
from fbm.gm import decisions as dlog
from fbm.gm import policy
from fbm.gm import proposals as props
from fbm.settings import SETTINGS_PATH
from fbm.timeutil import ET

KIND_MAP = {"waiver_claim": "waiver_claim", "add_drop": "stream_plan", "trade": "trade", "il_activation": "il"}
DECIDED = ("done", "rejected", "awaiting_owner")


def _et_day(iso: str | None) -> date | None:
    if not iso:
        return None
    return datetime.fromisoformat(iso).astimezone(ET).date()


def week_number(day: date, season_start: date | None) -> int | None:
    if season_start is None:
        return None
    first = fantasy_week(season_start)[0]
    return (fantasy_week(day)[0] - first).days // 7 + 1


def _decision_date(p: props.Proposal) -> date:
    pay = p.payload or {}
    eff = pay.get("effective_et")
    if isinstance(eff, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", eff):
        return date.fromisoformat(eff)
    return _et_day(p.executed_at if p.status == "done" else None) or _et_day(p.created_at) or date.today()


def _options(kind: str, p: props.Proposal, day: date) -> list[dict[str, Any]]:
    pay = p.payload or {}
    when = day.isoformat()
    stand = {"id": "stand_pat", "label": "Stand pat (no move)", "stand_pat": True, "cat_scores": {}}
    if kind == "trade":
        move = {
            "id": "proposal",
            "label": p.title,
            "spec": {
                "partner": pay.get("partner"),
                "give": list(pay.get("give") or []),
                "get": list(pay.get("get") or []),
            },
            "cat_scores": {},
        }
        stand["spec"] = {"partner": pay.get("partner"), "give": [], "get": []}
        return [move, stand]
    if kind == "il":
        move = {
            "id": "proposal",
            "label": p.title,
            "action": "activate",
            "spec": {
                "action": "activate",
                "player": pay.get("activate"),
                "replacement": None,
                "drop": pay.get("drop"),
                "date": when,
            },
            "cat_scores": {},
        }
        hold = {
            "id": "stand_pat",
            "label": "Hold (no activation)",
            "action": "hold",
            "stand_pat": True,
            "spec": {"action": "hold", "player": pay.get("activate"), "replacement": None, "date": when},
            "cat_scores": {},
        }
        return [move, hold]
    move = {
        "id": "proposal",
        "label": p.title,
        "adds": 1 if pay.get("add") else 0,
        "spec": {"moves": [{"add": pay.get("add"), "drop": pay.get("drop"), "date": when}]},
        "cat_scores": {},
    }
    stand["spec"] = {"moves": []}
    return [move, stand]


def backfill_decisions(
    conn: sqlite3.Connection, team_key: str, season_start: date | None = None
) -> list[dict[str, Any]]:
    """Decision rows for past owner-decided proposals. Returns what was created (skips already-migrated refs)."""
    done_refs = {
        r["proposal_ref"]
        for r in conn.execute(
            "SELECT proposal_ref FROM decisions WHERE trigger = 'migration' AND proposal_ref IS NOT NULL"
        )
    }
    baseline = (0, {k: p.default for k, p in policy.REGISTRY.items()})
    created = []
    for p in sorted(props.list_proposals(conn, None, 1000), key=lambda x: x.id):
        kind = KIND_MAP.get(props.effective_kind(p.kind, p.payload or {}))
        if kind is None or p.status not in DECIDED or p.ref in done_refs:
            continue
        day = _decision_date(p)
        situation = {
            "migrated": True,
            "note": "Pre-overhaul recommendation (ESPN-based engine, before the decision log existed); "
            "options = the proposed move vs standing pat.",
            "proposal_ref": p.ref,
            "title": p.title,
            "status": p.status,
            "review_verdict": p.review_verdict,
            "owner_quote": p.owner_quote,
            "team_key": team_key,
            "week": week_number(day, season_start),
            "weekday": day.weekday(),
            "odds": {},
            "punts": [],
            "evidence": p.evidence or {},
        }
        did = dlog.log(
            conn,
            kind=kind,
            trigger="migration",
            et_date=day,
            situation=situation,
            options=_options(kind, p, day),
            recommended="proposal",
            week=situation["week"],
            proposal_ref=p.ref,
            created_at=p.created_at,
            policy_snapshot=baseline,
        )
        if p.status == "done":
            dlog.set_chosen(conn, did, "proposal", "proposal")
        elif p.status == "rejected":
            dlog.set_chosen(conn, did, "stand_pat", "owner")
        created.append({"decision_id": did, "ref": p.ref, "kind": kind, "et_date": day.isoformat(), "status": p.status})
    return created


def enable_hashtag(league_id: str, path: Path = SETTINGS_PATH) -> str:
    """Write `[hashtag] enabled = true` + league_id into settings.toml, keeping everything else. Returns the action."""
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    section = (
        "[hashtag]\n"
        "# Hashtag Basketball Premium is FBM's data layer (projections, weekly totals). No suggestions while it is\n"
        "# unhealthy (no fallback). Sign in: `fbm hashtag login`; check: `fbm hashtag check`.\n"
        "enabled = true\n"
        f'league_id = "{league_id}"\n'
    )
    pattern = re.compile(r"(?ms)^\[hashtag\]\s*\n.*?(?=^\[|\Z)")
    if pattern.search(text):
        new = pattern.sub(section + "\n", text).rstrip() + "\n"
        action = "updated"
    else:
        new = text.rstrip() + "\n\n" + section
        action = "added"
    path.write_text(new, encoding="utf-8")
    return action


def overhaul(
    conn: sqlite3.Connection,
    *,
    team_key: str,
    league_id: str,
    season_start: date | None = None,
    enable: bool = True,
    settings_path: Path = SETTINGS_PATH,
    sync_yahoo: Any = None,
    regrade: Any = None,
    do_backup: Any = None,
    journal: Any = None,
) -> dict[str, Any]:
    """Run the overhaul migration. Callables (sync_yahoo, regrade, do_backup, journal) are injected for tests; the CLI
    passes the real ones."""
    report: dict[str, Any] = {"started_at": datetime.now(UTC).isoformat(timespec="seconds")}
    if do_backup is not None:
        report["backup"] = str(do_backup())
    report["settings"] = enable_hashtag(league_id, settings_path) if enable else "unchanged"
    report["decisions_created"] = backfill_decisions(conn, team_key, season_start)
    if sync_yahoo is not None:
        try:
            report["yahoo_sync"] = sync_yahoo()
        except Exception as err:  # noqa: BLE001 - Yahoo reads may fail; the rest of the migration still stands
            report["yahoo_sync"] = {"error": f"{type(err).__name__}: {err}"}
    if regrade is not None:
        try:
            report["predictions_regraded"] = regrade(conn)
        except Exception as err:  # noqa: BLE001
            report["predictions_regraded"] = {"error": f"{type(err).__name__}: {err}"}
    if journal is not None:
        made = report["decisions_created"]
        body = (
            "Migrated personal data to the Hashtag/decision-calibration FBM. "
            f"Backup: {report.get('backup', 'none')}. Settings [hashtag]: {report['settings']}. "
            f"Decision log backfilled from {len(made)} owner-decided proposals "
            f"({', '.join(m['ref'] for m in made) or 'none new'}) with policy version 0 (defaults). "
            f"Yahoo finals/lineups sync: {json.dumps(report.get('yahoo_sync'), default=str)[:300]}. "
            f"Legacy forecast re-grade: {json.dumps(report.get('predictions_regraded'), default=str)[:300]}. "
            "Old tables (projection_log, espn_projections, yahoo_adp) kept read-only; draft tools and projection "
            "calibration retired. Suggestions pause until Hashtag is healthy "
            "(`fbm hashtag login`, `fbm hashtag import`)."
        )
        report["journal"] = str(journal("system", "Migration: Hashtag data layer + decision calibration", body))
    return report
