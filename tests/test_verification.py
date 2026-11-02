from datetime import UTC, date, datetime, timedelta

import pytest

from fbm.analytics.categories import NINE_CAT
from fbm.gm import outcomes
from fbm.gm import proposals as props
from fbm.gm import verification as ver
from fbm.store import db
from fbm.yahoo.league_reads import Transaction, TxPlayer

from .test_outcomes import POOL

ME, OTHER, THIRD = "l.t.5", "l.t.3", "l.t.6"
DECIDED = datetime(2026, 10, 27, 8, 45, tzinfo=UTC)
CONFIRMED = DECIDED + timedelta(minutes=15)
LATER = CONFIRMED + timedelta(days=10)  # well past every verification window
IDS = {"y1": "A", "y2": "B", "y3": "C", "y4": "D", "y5": "E"}  # yahoo id -> espn id


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    yield c
    c.close()


def done(conn, kind, payload, decided=DECIDED, confirmed=CONFIRMED):
    p = props.create(conn, kind, "title", "summary", payload, "because")
    conn.execute(
        "UPDATE proposals SET status='done', created_at=?, decided_at=?, executed_at=? WHERE id=?",
        (decided.isoformat(), decided.isoformat(), confirmed.isoformat(), p.id),
    )
    conn.commit()
    return props.get(conn, p.id)


def tp(yid, name, action, src="", dst=""):
    return TxPlayer(
        f"p.{yid}", yid, name, "BOS", action,
        "team" if src else "freeagents", src, "team" if dst else "waivers", dst,
    )  # fmt: skip


def tx(key, kind, ts, *players):
    return Transaction(f"l.tr.{key}", kind, "successful", ts, tuple(players))


def view(owners, txs=(), as_of=LATER, my_slots=None, lineups=None):
    return ver.YahooView(
        my_team_key=ME,
        owners=owners,
        my_slots=my_slots or {},
        as_of=as_of,
        transactions=list(txs),
        resolve=lambda t: IDS.get(t.player_id),
        team_names={ME: "Me", OTHER: "dimebag", THIRD: "JD"},
        lineups=lineups or {},
    )


def run(conn, v, now=LATER):
    return {r["ref"]: r for r in ver.verify_all(conn, v, now)}


TRADE = {"partner": OTHER, "give": ["A"], "give_names": ["Zion"], "get": ["B"], "get_names": ["Alperen"]}


def test_rejected_trade_is_not_executed_and_status_flips(conn):
    p = done(conn, "trade", TRADE)
    rows = run(conn, view({"A": ME, "B": OTHER}))
    assert rows[p.ref]["verification"] == "not_executed" and "Zion still on Me" in rows[p.ref]["reason"]
    stored = props.get(conn, p.id)
    assert stored.status == "not_executed" and stored.as_dict()["verification"]["state"] == "not_executed"


def test_trade_inside_the_vote_window_is_pending_not_failed(conn):
    p = done(conn, "trade", TRADE)
    now = CONFIRMED + timedelta(hours=30)
    rows = run(conn, view({"A": ME, "B": OTHER}, as_of=now), now)
    assert rows[p.ref]["verification"] == "pending" and props.get(conn, p.id).status == "done"


def test_stale_snapshot_never_condemns_a_move(conn):
    p = done(conn, "trade", TRADE)
    rows = run(conn, view({"A": ME, "B": OTHER}, as_of=CONFIRMED + timedelta(hours=1)))
    assert rows[p.ref]["verification"] == "pending"


def test_completed_trade_is_verified_with_its_transaction(conn):
    p = done(conn, "trade", TRADE)
    t = tx(80, "trade", CONFIRMED + timedelta(hours=40), tp("y1", "Zion", "trade", ME, OTHER),
           tp("y2", "Alperen", "trade", OTHER, ME))  # fmt: skip
    rows = run(conn, view({"A": OTHER, "B": ME}, [t]))
    assert rows[p.ref]["verification"] == "verified"
    v = props.get(conn, p.id).as_dict()["verification"]
    assert v["transactions"] == ["l.tr.80"] and v["credited"] == {"in": ["B"], "out": ["A"]}
    assert v["effective_et"] == "2026-10-28"  # 00:45Z on the 29th is still the 28th in ET


def test_claim_lost_to_another_team_is_not_executed(conn):
    p = done(conn, "waiver_claim", {"add": "C", "add_name": "Collins", "drop": "D", "drop_name": "Spencer"})
    t = tx(38, "add", CONFIRMED + timedelta(hours=2), tp("y3", "Collins", "add", "", THIRD))
    rows = run(conn, view({"C": THIRD, "D": ME}, [t]))
    assert rows[p.ref]["verification"] == "not_executed"
    assert "JD" in rows[p.ref]["reason"] and "tr.38" in rows[p.ref]["reason"]


def test_one_drop_transaction_is_credited_to_one_proposal(conn):
    add = done(conn, "add_drop", {"add": "E", "add_name": "Sandro", "drop": "D", "drop_name": "Spencer"})
    il = done(
        conn, "il_activation", {"activate": "B", "activate_name": "Ausar", "drop": "D", "drop_name": "Spencer"},
        decided=DECIDED + timedelta(minutes=5), confirmed=CONFIRMED + timedelta(minutes=5),
    )  # fmt: skip
    drop = tx(61, "drop", DECIDED + timedelta(minutes=13), tp("y4", "Spencer", "drop", ME))
    sandro = tx(71, "add", DECIDED + timedelta(hours=36), tp("y5", "Sandro", "add", "", ME))
    lineups = {"2026-10-26": {"B": "IL"}, "2026-10-27": {"B": "G"}}
    rows = run(conn, view({"E": ME, "B": ME}, [drop, sandro], lineups=lineups, my_slots={"B": "G", "E": "BN"}))
    assert rows[add.ref]["verification"] == rows[il.ref]["verification"] == "verified"
    v_add = props.get(conn, add.id).as_dict()["verification"]
    v_il = props.get(conn, il.id).as_dict()["verification"]
    assert v_il["transactions"] == ["l.tr.61"] and v_add["transactions"] == ["l.tr.71"]
    assert v_il["credited"] == {"in": ["B"], "out": ["D"]} and v_add["credited"] == {"in": ["E"], "out": []}
    assert [lg["status"] for lg in v_add["legs"]] == ["verified", "shared"]


def test_il_activation_uses_lineup_history_and_notes_a_return_to_il(conn):
    p = done(conn, "il_activation", {"activate": "B", "activate_name": "Podz", "drop": None})
    lineups = {"2026-10-26": {"B": "IL"}, "2026-10-27": {"B": "UTIL"}, "2026-10-28": {"B": "IL"}}
    rows = run(conn, view({"B": ME}, lineups=lineups, my_slots={"B": "IL"}))
    assert rows[p.ref]["verification"] == "verified"
    assert "back in an IL slot on 2026-10-28" in rows[p.ref]["notes"][0]


def test_il_activation_that_never_left_il_is_not_executed(conn):
    p = done(conn, "il_activation", {"activate": "B", "activate_name": "Podz", "drop": None})
    rows = run(conn, view({"B": ME}, lineups={"2026-10-28": {"B": "IL"}}, my_slots={"B": "IL"}))
    assert rows[p.ref]["verification"] == "not_executed" and "still in the IL slot" in rows[p.ref]["reason"]


def test_roster_evidence_verifies_an_add_without_a_transaction(conn):
    p = done(conn, "add_drop", {"add": "E", "add_name": "Sandro", "drop": None})
    assert run(conn, view({"E": ME}))[p.ref]["verification"] == "verified"


def test_verified_is_sticky_and_not_executed_needs_a_transaction_to_recover(conn):
    ok = done(conn, "add_drop", {"add": "E", "add_name": "Sandro", "drop": None})
    run(conn, view({"E": ME}))
    assert run(conn, view({"E": THIRD}))[ok.ref]["verification"] == "verified"  # later churn never un-verifies
    lost = done(conn, "trade", TRADE, decided=DECIDED + timedelta(hours=1), confirmed=CONFIRMED + timedelta(hours=1))
    assert run(conn, view({"A": ME, "B": OTHER}))[lost.ref]["verification"] == "not_executed"
    # the players later change hands some other way: roster evidence alone must not revive the rejected trade
    assert run(conn, view({"A": OTHER, "B": ME}))[lost.ref]["verification"] == "not_executed"
    t = tx(90, "trade", CONFIRMED + timedelta(hours=50), tp("y1", "Zion", "trade", ME, OTHER),
           tp("y2", "Alperen", "trade", OTHER, ME))  # fmt: skip
    assert run(conn, view({"A": OTHER, "B": ME}, [t]))[lost.ref]["verification"] == "verified"
    assert props.get(conn, lost.id).status == "done"


def test_grader_only_grades_verified_moves(conn):
    good = done(conn, "add_drop", {"add": "E", "add_name": "Sandro", "drop": None})
    bad = done(conn, "trade", TRADE, decided=DECIDED + timedelta(hours=1), confirmed=CONFIRMED + timedelta(hours=1))
    now = LATER
    unseen = done(conn, "waiver_claim", {"add": "C", "add_name": "Collins", "drop": None},
                  decided=now - timedelta(hours=3), confirmed=now - timedelta(hours=2))  # fmt: skip
    run(conn, view({"E": ME, "A": ME, "B": OTHER}, as_of=now), now)
    graded = outcomes.grade_decisions(conn, POOL, NINE_CAT, date(2026, 10, 30), lookback_days=10_000)
    assert [g["ref"] for g in graded] == [good.ref]
    assert graded[0]["verification"]["verification"] == "verified"
    ungraded = {u["ref"]: u for u in outcomes.ungraded_moves(conn, 10_000)}
    assert set(ungraded) == {bad.ref, unseen.ref}
    assert ungraded[bad.ref]["verification"] == "not_executed" and ungraded[bad.ref]["status"] == "not_executed"
    assert "Zion" in ungraded[bad.ref]["reason"] and ungraded[unseen.ref]["verification"] == "pending"
