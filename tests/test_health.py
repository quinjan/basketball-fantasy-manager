from datetime import UTC, date, datetime, timedelta

import pytest

from fbm.gm import health
from fbm.store import db

NOW = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _by(checks, name):
    return next(c for c in checks if c["check"] == name)


def test_season_phase():
    dates = ["2026-10-20", "2027-04-12"]
    assert health.season_phase(dates, date(2026, 10, 16)) == "preseason"
    assert health.season_phase(dates, date(2026, 11, 1)) == "regular"
    assert health.season_phase(dates, date(2027, 6, 1)) == "offseason"
    assert health.season_phase([], date(2026, 11, 1)) == "offseason"


def test_data_freshness_thresholds():
    runs = {
        "schedule:2027": _iso(NOW - timedelta(hours=5)),
        "boxscores:2027": _iso(NOW - timedelta(hours=20)),
        "injuries": _iso(NOW - timedelta(hours=50)),
    }
    out = health.check_data_freshness(runs, "regular", games_last_day=8, now=NOW)
    assert _by(out, "ESPN schedule")["status"] == "OK"
    assert _by(out, "ESPN boxscores")["status"] == "WARN"
    assert _by(out, "ESPN injuries")["status"] == "FAIL"
    assert _by(out, "ESPN news")["status"] == "FAIL"
    relaxed = health.check_data_freshness(runs, "preseason", games_last_day=0, now=NOW)
    assert _by(relaxed, "ESPN injuries")["status"] == "OK"


def test_yahoo_demo_is_warn_not_fail():
    out = health.check_yahoo("demo", {"yahoo_client_id": False}, False, True, None, "", 0)
    assert {c["status"] for c in out} == {"WARN", "OK"}
    assert "approval pending" in _by(out, "League source")["detail"]
    missing_demo = health.check_yahoo("demo", {}, True, False, None, "", 0)
    assert _by(missing_demo, "Demo league")["status"] == "FAIL"


def test_yahoo_live_checks():
    creds = {"a": True, "b": True}
    out = health.check_yahoo("yahoo", creds, True, False, 30.0, "", 0)
    assert health.overall(out) == "OK"
    bad = health.check_yahoo("yahoo", {"a": False}, True, False, 90.0, "401 token expired", 3)
    assert _by(bad, "Yahoo credentials")["status"] == "FAIL"
    assert _by(bad, "Yahoo snapshot")["status"] == "WARN"
    assert _by(bad, "Yahoo refresh")["status"] == "FAIL"
    assert "3 unmatched" in _by(bad, "Yahoo ID matching")["detail"]


def test_yahoo_web_session_replaces_api_credentials():
    out = health.check_yahoo("yahoo", {"a": False}, True, False, 30.0, "", 0, web_session_age_days=2.0)
    assert health.overall(out) == "OK"
    assert "web session" in _by(out, "Yahoo access")["detail"]
    assert not any(c["check"] == "Yahoo credentials" for c in out)
    demo = health.check_yahoo("demo", {"a": False}, True, True, None, "", 0, web_session_age_days=0.0)
    assert "web session ready" in _by(demo, "League source")["detail"]


def test_proposals():
    future, past = _iso(NOW + timedelta(hours=5)), _iso(NOW - timedelta(hours=1))
    rows = [
        {
            "ref": "P-1",
            "status": "awaiting_owner",
            "created_at": _iso(NOW - timedelta(hours=30)),
            "expires_at": future,
            "decided_at": None,
            "executed_at": _iso(NOW - timedelta(hours=20)),
        },
        {
            "ref": "P-2",
            "status": "approved",
            "created_at": _iso(NOW - timedelta(hours=5)),
            "expires_at": future,
            "decided_at": _iso(NOW - timedelta(hours=4)),
            "executed_at": None,
        },
        {
            "ref": "P-3",
            "status": "pending_review",
            "created_at": _iso(NOW - timedelta(hours=20)),
            "expires_at": past,
            "decided_at": None,
            "executed_at": None,
        },
    ]
    out = health.check_proposals(rows, NOW)
    assert [c["status"] for c in out] == ["WARN", "WARN", "WARN"]
    assert "P-3" in out[2]["detail"]
    assert health.overall(health.check_proposals([], NOW)) == "OK"


def test_journal_gaps():
    today = date(2026, 11, 5)
    game_days = {date(2026, 11, 3), date(2026, 11, 4)}
    entry = "\n## 09:00 local / Wed 20:00 ET · decision · Daily GM\n"
    lineup = "\n## 16:16 local / Wed 04:16 ET · execution · Lineup 2026-11-04\n"
    journal = {date(2026, 11, 2): entry, date(2026, 11, 3): entry, date(2026, 11, 4): entry + lineup}
    r = health.check_journal(journal, "regular", today, game_days, date(2026, 10, 20))
    assert r["status"] == "WARN" and "2026-11-03" in r["detail"] and "lineup" in r["detail"]
    r = health.check_journal({date(2026, 11, 4): entry}, "regular", today, set(), date(2026, 10, 20))
    assert r["status"] == "FAIL"
    assert health.check_journal({}, "preseason", today, set(), None)["status"] == "OK"


def test_notifications_and_storage():
    outbox = [{"status": "failed", "subject": "Daily", "created_at": _iso(NOW - timedelta(hours=2))}]
    out = health.check_notifications(False, outbox, NOW)
    assert out[0]["status"] == "OK" and "disabled" in out[0]["detail"]
    assert out[1]["status"] == "WARN"
    assert health.check_storage(True, 5_000_000)[0]["status"] == "OK"
    assert health.check_storage(True, None)[0]["status"] == "FAIL"
    assert health.check_storage(False, None)[0]["status"] == "FAIL"


def test_gather_with_temp_db(tmp_path, monkeypatch):
    from fbm.gm import memory

    path = tmp_path / "fbm.db"
    conn = db.connect(path)
    db.migrate(conn)
    conn.execute("INSERT INTO sync_log VALUES ('news', ?, 'x')", (_iso(NOW - timedelta(hours=1)),))
    conn.execute(
        "INSERT INTO games VALUES ('1', 2027, 2, ?, '2026-10-20', 'BOS', 'NYK', 'pre', 0, 0, 1, 0, ?)",
        (_iso(NOW), _iso(NOW)),
    )
    conn.commit()
    monkeypatch.setattr(memory, "JOURNAL_DIR", tmp_path / "journal")
    monkeypatch.setattr(health, "_cred_presence", lambda: {"yahoo_client_id": True})
    monkeypatch.setattr(health, "_web_session_age_days", lambda now: None)
    r = health.gather(conn=conn, db_path=path, now=datetime(2026, 10, 16, 10, tzinfo=UTC))
    conn.close()
    assert r["phase"] == "preseason"
    assert _by(r["checks"], "ESPN news")["status"] == "OK"
    assert _by(r["checks"], "Scheduled runs")["status"] == "OK"
    assert r["overall"] in ("WARN", "FAIL")


@pytest.mark.parametrize("statuses,expected", [(["OK"], "OK"), (["OK", "WARN"], "WARN"), (["WARN", "FAIL"], "FAIL")])
def test_overall(statuses, expected):
    assert health.overall([{"status": s} for s in statuses]) == expected
