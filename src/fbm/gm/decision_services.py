"""Decision-calibration services (MCP tools in the "decisions & replay" section): the decision log, replay grading
and odds reliability. JSON-friendly; every number comes from the engine."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from fbm.gm import decisions as dlog
from fbm.gm import odds_reliability as odds_rel
from fbm.gm import replay


def _svc():
    from fbm.gm import services

    return services


def sync_yahoo_results(weeks_back: int = 4) -> dict[str, Any]:
    """Live Yahoo league only: cache the final matchup results and both teams' daily lineups of recently ended weeks
    (read-only Yahoo reads). Never raises; returns what happened."""
    svc = _svc()
    if not svc._is_yahoo():
        return {"skipped": "demo league: no Yahoo finals (box-score path)"}
    try:
        from fbm.yahoo import lineups

        league = svc.active_league()
        current = int(league.week or 1)
        weeks = [w for w in range(max(1, current - weeks_back), current) if w >= 1]
        with svc._yahoo_client() as client:
            return lineups.sync_weeks(
                svc._conn(), client, league.league_key, league.my_team_key, weeks, svc._yahoo_mapper(league)
            )
    except Exception as err:  # grading still runs on whatever is cached
        return {"error": f"{type(err).__name__}: {err}"}


def decisions_log(kind: str | None = None, since: str | None = None, limit: int = 50) -> dict[str, Any]:
    rows = dlog.query(svc_conn(), kind=kind, since=since, limit=5000)[-int(limit) :]
    out = []
    for d in rows:
        res = d.get("result") or {}
        out.append(
            {
                "id": d["id"],
                "et_date": d["et_date"],
                "week": d["week"],
                "kind": d["kind"],
                "trigger": d["trigger"],
                "options": [{"id": o["id"], "label": o.get("label"), "view": o.get("view")} for o in d["options"]],
                "recommended": d["recommended"],
                "chosen": d["chosen"],
                "chosen_source": d.get("chosen_source"),
                "policy_version": d["policy_version"],
                "proposal_ref": d["proposal_ref"],
                "graded": bool(d.get("graded_at")),
                "best": res.get("best"),
                "regret_cats": res.get("regret_cats"),
            }
        )
    return {"decisions": out, "count": len(out)}


def svc_conn():
    return _svc()._conn()


def replay_grade(lookback_days: int = 14) -> dict[str, Any]:
    svc = _svc()
    sync = sync_yahoo_results(max(1, lookback_days // 7 + 1))
    pool = categories = None
    try:
        c = svc._grading_ctx()
        pool, categories = c.pool, c.cfg.categories
    except Exception:
        pass
    kw: dict[str, Any] = {"pool": pool}
    if categories:
        kw["categories"] = categories
    today = svc.nba_today()
    graded = replay.grade_pending(svc._conn(), today, lookback_days=lookback_days, **kw)
    return {
        "window_et": f"{today - timedelta(days=lookback_days)}..{today - timedelta(days=1)}",
        "yahoo_sync": sync,
        "decisions": graded,
        "regret_by_kind": replay.regret_summary(graded),
        "method": "replay on real box scores + Yahoo final week totals; opponent held at what they actually did; "
        "regret_cats = categories won by the best option minus the recommended one",
    }


def odds_reliability(scale: float | None = None, view: str | None = None) -> dict[str, Any]:
    return odds_rel.report(svc_conn(), scale=scale, view=view)
