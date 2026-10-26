"""Service layer for Hashtag Basketball (MCP tools + CLI): status, import, check. Read-only toward HT and Yahoo."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fbm.store import db

PAGE_CHECKS = (
    # (label, path builder, parser name)
    ("premium home", lambda lid: "/premium/", "parse_premium_home"),
    ("projections", lambda lid: "/fantasy-basketball-projections", "parse_projections"),
    ("season rankings", lambda lid: "/fantasy-basketball-rankings", "parse_season_rankings"),
    ("H2H planner", lambda lid: f"/import-v4/plan-cat-yahoo.aspx?lid={lid}", "parse_h2h_planner"),
    ("waiver wire", lambda lid: f"/import-v4/waiver-cat-yahoo.aspx?lid={lid}", "parse_waiver"),
    ("scouting report", lambda lid: f"/import-v4/scouting-cat-yahoo.aspx?lid={lid}", "parse_team_ranks"),
    ("trade finder", lambda lid: f"/import-v4/trade-finder-cat-yahoo.aspx?lid={lid}", "parse_trade_finder"),
    ("player notes", lambda lid: "/import-v4/notebook", "parse_notebook"),
    ("next man up", lambda lid: "/next-man-up", "parse_next_man_up"),
)


def _session_info() -> dict[str, Any]:
    from fbm.hashtag import session as ht_session

    s = ht_session.load()
    if s is None:
        return {"stored": False}
    age = s.age_days(datetime.now(UTC))
    return {"stored": True, "source": s.source, "cookies": len(s.cookies),
            "age_days": round(age, 1) if age is not None else None}  # fmt: skip


def hashtag_status(conn=None, enabled: bool | None = None) -> dict[str, Any]:
    """HT health (the suggestion gate), the latest import of every page[/view], unmatched players, session info."""
    from fbm.hashtag import health
    from fbm.hashtag import ids as ht_ids
    from fbm.hashtag import store as ht_store

    own = conn is None
    conn = conn or db.connect()
    try:
        st = health.status(conn, enabled)
        rows = conn.execute(
            "SELECT page, view, status, rows, detail, started_at, finished_at, run_id FROM hashtag_import_log l "
            "WHERE id = (SELECT MAX(id) FROM hashtag_import_log m WHERE m.page = l.page "
            "AND COALESCE(m.view, '') = COALESCE(l.view, '')) ORDER BY page, view"
        ).fetchall()
        return {
            "health": st.as_dict(),
            "last_imports": [dict(r) for r in rows],
            "unmatched_projected": ht_store.unmatched(conn, "ROS"),
            "unmatched_players": ht_ids.unmatched_players(conn)[:50],
            "session": _session_info(),
            "how_to_fix_unmatched": 'Add `"<ht_id>" = "<espn_id>"` under [players] in '
            "config/hashtag_id_overrides.toml, then `fbm hashtag import`.",
        }
    finally:
        if own:
            conn.close()


def _league_or_none():
    from fbm.settings import load_settings

    if load_settings().league_source != "yahoo":
        return None
    from fbm.gm import services as svc

    return svc.active_league()


def hashtag_import(pages: str | list[str] | None = None, weights: dict[str, float] | None = None) -> dict[str, Any]:
    """Run the HT import now for the active Yahoo league (pages: comma list, default all daily pages)."""
    from fbm.hashtag import importer

    wanted = [p.strip() for p in pages.split(",") if p.strip()] if isinstance(pages, str) else pages
    conn = db.connect()
    try:
        return importer.run_import(conn, league=_league_or_none(), pages=wanted, weights=weights)
    finally:
        conn.close()


def hashtag_check() -> dict[str, Any]:
    """Signed in? Is the league visible on HT? Does every page FBM reads still parse? Unmatched ids."""
    from fbm.hashtag import parsers as P
    from fbm.hashtag.client import HashtagClient
    from fbm.hashtag.errors import HashtagError, HashtagSessionError
    from fbm.hashtag.importer import _ht_league_id

    league = None
    try:
        league = _league_or_none()
    except Exception:  # noqa: BLE001 - the check still runs without a Yahoo snapshot
        league = None
    lid = _ht_league_id(league)
    results: list[dict[str, Any]] = []
    out: dict[str, Any] = {"league_id": lid, "signed_in": False, "league_visible": None, "pages": results}
    try:
        client = HashtagClient()
    except HashtagSessionError as err:
        out["error"] = str(err)
        return out
    with client:
        for label, path, parser in PAGE_CHECKS:
            if "lid=" in path("x") and not lid:
                results.append({"page": label, "ok": False, "detail": "no HT league id ([hashtag] league_id)"})
                continue
            try:
                parsed = getattr(P, parser)(client.get(path(lid)).doc)
            except HashtagSessionError as err:
                results.append({"page": label, "ok": False, "detail": str(err)})
                out["error"] = str(err)
                break
            except HashtagError as err:
                results.append({"page": label, "ok": False, "detail": str(err)})
                continue
            out["signed_in"] = True
            if parser == "parse_premium_home":
                out["league_visible"] = any(lg["lid"] == lid for lg in parsed) if lid else None
                detail = ", ".join(f"{lg['name']} ({lg['lid']})" for lg in parsed) or "no Yahoo leagues on HT"
            elif isinstance(parsed, dict) and "rows" in parsed:
                detail = f"{len(parsed['rows'])} rows"
            elif isinstance(parsed, list):
                detail = f"{len(parsed)} items"
            else:
                detail = "parsed"
            results.append({"page": label, "ok": True, "detail": detail})
    status = hashtag_status()
    out["unmatched_players"] = status["unmatched_players"]
    out["health"] = status["health"]
    return out
