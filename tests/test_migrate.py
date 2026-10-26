"""Overhaul migration of local data (fbm.gm.migrate)."""

from __future__ import annotations

from datetime import date

import pytest

from fbm.gm import decisions as dlog
from fbm.gm import migrate
from fbm.gm import proposals as props
from fbm.store import db


@pytest.fixture()
def conn():
    c = db.connect(":memory:")
    yield c
    c.close()


def _proposal(conn, kind, payload, status, title="t", executed="2026-10-23T09:25:54+00:00"):
    cur = conn.execute(
        "INSERT INTO proposals(created_at, kind, title, summary, payload, rationale, status, expires_at, executed_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        ("2026-10-23T09:00:00+00:00", kind, title, "", __import__("json").dumps(payload), "", status,
         "2026-10-24T09:00:00+00:00", executed),
    )  # fmt: skip
    conn.commit()
    return f"P-{cur.lastrowid}"


def test_backfill_maps_kinds_options_and_chosen(conn):
    _proposal(conn, "strategy", {"punts": []}, "done")
    claim = _proposal(conn, "waiver_claim", {"add": "1", "drop": "2"}, "done")
    trade = _proposal(conn, "trade", {"partner": "t.3", "give": ["5"], "get": ["6"]}, "rejected", executed=None)
    il = _proposal(conn, "other", {"action": props.LEGACY_IL_ACTION, "activate": "7", "drop": "8"}, "rejected")
    _proposal(conn, "add_drop", {"add": "9"}, "expired")
    _proposal(conn, "waiver_claim", {"add": "10"}, "failed")
    live = _proposal(conn, "trade", {"partner": "t.12", "give": ["5"], "get": ["11"]}, "awaiting_owner")
    made = migrate.backfill_decisions(conn, "t.5", season_start=date(2026, 10, 20))
    assert [(m["ref"], m["kind"]) for m in made] == [
        (claim, "waiver_claim"),
        (trade, "trade"),
        (il, "il"),
        (live, "trade"),
    ]
    rows = {d["proposal_ref"]: d for d in dlog.query(conn)}
    assert rows[claim]["chosen"] == "proposal" and rows[claim]["chosen_source"] == "proposal"
    assert rows[claim]["options"][0]["spec"] == {"moves": [{"add": "1", "drop": "2", "date": "2026-10-23"}]}
    assert rows[claim]["week"] == 1 and rows[claim]["policy_version"] == 0
    assert rows[trade]["chosen"] == "stand_pat" and rows[trade]["chosen_source"] == "owner"
    assert rows[il]["options"][1]["action"] == "hold"
    assert rows[live]["chosen"] is None
    assert migrate.backfill_decisions(conn, "t.5") == []  # idempotent


def test_enable_hashtag_adds_then_updates(tmp_path):
    path = tmp_path / "settings.toml"
    path.write_text('[league]\nsource = "yahoo"\n\n[notify]\nemail_enabled = true\n', encoding="utf-8")
    assert migrate.enable_hashtag("30273", path) == "added"
    assert migrate.enable_hashtag("999", path) == "updated"
    text = path.read_text(encoding="utf-8")
    assert text.count("[hashtag]") == 1 and 'league_id = "999"' in text and "email_enabled = true" in text
    from fbm.settings import load_settings

    s = load_settings(path)
    assert s.hashtag_enabled and s.hashtag_league_id == "999" and s.league_source == "yahoo"


def test_overhaul_runs_steps_and_survives_yahoo_errors(conn, tmp_path):
    path = tmp_path / "settings.toml"
    path.write_text("", encoding="utf-8")
    _proposal(conn, "waiver_claim", {"add": "1"}, "done")

    def boom():
        raise RuntimeError("yahoo down")

    notes = []
    report = migrate.overhaul(
        conn,
        team_key="t.5",
        league_id="30273",
        settings_path=path,
        sync_yahoo=boom,
        regrade=lambda c: {"regraded": 0},
        do_backup=lambda: tmp_path / "b.zip",
        journal=lambda kind, title, body: notes.append(body) or tmp_path / "j.md",
    )
    assert report["settings"] == "added" and len(report["decisions_created"]) == 1
    assert "yahoo down" in report["yahoo_sync"]["error"] and report["predictions_regraded"] == {"regraded": 0}
    assert notes and "P-1" in notes[0]
