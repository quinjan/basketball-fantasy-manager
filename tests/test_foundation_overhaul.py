"""Shared foundation for the Hashtag/decision-calibration overhaul: schema, HT store + health, policy, decision log."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from fbm.gm import decisions, policy
from fbm.hashtag import VIEWS, health, store
from fbm.store import db

NOW = datetime(2026, 10, 27, 15, 0, tzinfo=UTC)


@pytest.fixture()
def conn():
    c = db.connect(":memory:")
    yield c
    c.close()


def _proj(conn, view="ROS", as_of="2026-10-27", ht_id="9196", espn_id="3112335", rank=1, name="Nikola Jokic"):
    conn.execute(
        "INSERT INTO hashtag_projections(as_of, view, ht_id, espn_id, name, team, rank, gp, pts, fgm, fga, fetched_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (as_of, view, ht_id, espn_id, name, "DEN", rank, 72, 28.4, 10.5, 18.3, NOW.isoformat()),
    )


def _log(conn, page, status="ok", view=None, at=NOW - timedelta(hours=2), detail=None):
    conn.execute(
        "INSERT INTO hashtag_import_log(run_id, started_at, finished_at, page, view, status, rows, detail) "
        "VALUES (?,?,?,?,?,?,?,?)",
        ("r1", at.isoformat(), at.isoformat(), page, view, status, 1, detail),
    )


def _all_ok(conn, at=NOW - timedelta(hours=2)):
    for v in VIEWS:
        _log(conn, "projections", view=v, at=at)
    _log(conn, "h2h_planner", at=at)
    _log(conn, "league_sync", at=at)


def test_schema_has_new_tables(conn):
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for t in (
        "hashtag_players",
        "hashtag_projections",
        "hashtag_matchup_totals",
        "hashtag_waiver",
        "hashtag_trade_ideas",
        "hashtag_team_ranks",
        "hashtag_notes",
        "hashtag_next_man_up",
        "hashtag_import_log",
        "yahoo_daily_lineups",
        "decisions",
        "policy_params",
        "policy_history",
    ):
        assert t in names


def test_projections_read_api_drops_unmatched_and_dupes(conn):
    _proj(conn)
    _proj(conn, ht_id="dup", rank=50, name="Dup")  # same ESPN id, worse rank
    _proj(conn, ht_id="x", espn_id=None, rank=2, name="Nobody")
    df = store.projections(conn, "ros")
    assert list(df.index) == ["3112335"]
    assert df.loc["3112335", "name"] == "Nikola Jokic"
    assert df.attrs["as_of"] == "2026-10-27"
    assert [m["name"] for m in store.unmatched(conn)] == ["Nobody"]
    with pytest.raises(ValueError):
        store.projections(conn, "nope")


def test_matchup_totals_latest_pull(conn):
    for pulled, val in (("2026-10-27T10:00:00+00:00", 100.0), ("2026-10-27T14:00:00+00:00", 120.0)):
        conn.execute(
            "INSERT INTO hashtag_matchup_totals(pulled_at, et_date, week, view, team_key, opponent_key, category, "
            "me_before, opp_before, me_after, opp_after, ht_share) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (pulled, "2026-10-27", 2, "STREAM7", "t.5", "t.13", "reb", 0, 0, val, 90.0, 0.5),
        )
    m = store.matchup_totals(conn, week=2)
    assert m["pulled_at"].startswith("2026-10-27T14")
    assert m["categories"]["reb"]["me_after"] == 120.0


def test_health_disabled_never_gates(conn):
    st = health.require_healthy(conn, enabled=False, now=NOW)
    assert not st.enabled


def test_health_requires_every_page(conn, monkeypatch):
    monkeypatch.setattr(health, "_has_session", lambda: True)
    _proj(conn)
    st = health.status(conn, enabled=True, now=NOW)
    assert not st.healthy and any("never imported" in r for r in st.reasons)
    _all_ok(conn)
    st = health.status(conn, enabled=True, now=NOW)
    assert st.healthy, st.reasons
    assert health.require_healthy(conn, enabled=True, now=NOW).healthy


def test_health_stale_expired_and_layout(conn, monkeypatch):
    monkeypatch.setattr(health, "_has_session", lambda: True)
    _all_ok(conn, at=NOW - timedelta(hours=40))
    st = health.status(conn, enabled=True, now=NOW)
    assert not st.healthy and any("h old" in r for r in st.reasons)
    _all_ok(conn)
    _log(conn, "h2h_planner", status="session_expired", at=NOW - timedelta(hours=1))
    _log(conn, "projections", view="STREAM7", status="layout_changed", at=NOW - timedelta(hours=1))
    with pytest.raises(health.HTUnhealthyError) as err:
        health.require_healthy(conn, enabled=True, now=NOW)
    payload = err.value.status.gate_payload()
    assert payload["error_code"] == "HT_UNHEALTHY"
    assert health.FIX_LOGIN in payload["fix"] and health.FIX_LAYOUT in payload["fix"]


def test_health_unmatched_and_league_sync(conn, monkeypatch):
    monkeypatch.setattr(health, "_has_session", lambda: True)
    _all_ok(conn)
    for i in range(12):
        _proj(conn, ht_id=f"u{i}", espn_id=None, rank=i + 2, name=f"U{i}")
    _log(conn, "league_sync", status="league_mismatch", at=NOW - timedelta(minutes=5), detail="3 roster diffs")
    st = health.status(conn, enabled=True, now=NOW)
    assert any("no ESPN id" in r for r in st.reasons)
    assert any("league_sync" in r for r in st.reasons)


def test_policy_defaults_validation_and_versions(conn):
    assert policy.get(conn, "policy.view.claim") == "BLEND14"
    assert policy.version(conn) == 0
    v = policy.apply_changes(conn, {"policy.concede_below": 0.3, "policy.view.lineup": "BLEND7"}, "proposal:P-9", "P-9")
    assert v == 1 and policy.get(conn, "policy.concede_below") == 0.3
    hist = conn.execute("SELECT key, old_value, new_value FROM policy_history ORDER BY key").fetchall()
    assert [(r["key"], json.loads(r["old_value"])) for r in hist] == [
        ("policy.concede_below", 0.25),
        ("policy.view.lineup", "STREAM7"),
    ]
    with pytest.raises(policy.PolicyError):
        policy.apply_changes(conn, {"policy.concede_below": 0.9}, "x")
    with pytest.raises(policy.PolicyError):
        policy.apply_changes(conn, {"policy.view.lineup": "NOPE"}, "x")
    assert {r["key"] for r in policy.table(conn)} == set(policy.REGISTRY)


def test_decision_log_roundtrip(conn):
    did = decisions.log(
        conn,
        kind="lineup",
        trigger="daily-gm",
        et_date="2026-10-27",
        situation={"days_left": 6},
        options=[{"id": "rec", "label": "recommended"}, {"id": "ros", "label": "ROS lineup"}],
        recommended="rec",
        week=2,
    )
    d = decisions.get(conn, did)
    assert d["policy_version"] == 0 and d["policy_params"]["policy.view.lineup"] == "STREAM7"
    decisions.set_chosen(conn, did, "rec", "yahoo_lineup")
    decisions.save_result(conn, did, {"regret": 0})
    assert decisions.query(conn, kind="lineup", graded=True)[0]["result"] == {"regret": 0}
    with pytest.raises(decisions.DecisionError):
        decisions.log(conn, kind="bad", trigger="daily-gm", et_date="2026-10-27", situation={}, options=[{"id": "a"}])
    with pytest.raises(decisions.DecisionError):
        decisions.log(
            conn,
            kind="lineup",
            trigger="daily-gm",
            et_date="2026-10-27",
            situation={},
            options=[{"id": "a"}],
            recommended="b",
        )
