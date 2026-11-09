"""Issue #15: opponent realized/projected ratios, calibration bands, the policy-gated opponent shrink, and the
simulator's per-day startable-slot cap. Synthetic fixtures only."""

from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

from fbm.analytics import ht_odds
from fbm.analytics.categories import NINE_CAT
from fbm.analytics.week import plan_team_week
from fbm.gm import odds_reliability as orl
from fbm.gm import policy, policy_tuning
from fbm.league.model import FantasyTeam, RosterPlayer
from fbm.store import db
from fbm.yahoo import lineups

W0 = date(2026, 11, 2)  # a Monday


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    yield c
    c.close()


def add_week(conn, i, *, opp_ratio=0.7, post=True, opp_banked=0.0, start_offset=0, opp="op", final=True):
    """Week i (Mon-Sun from W0): we realize our projection, the opponent `opp_ratio` of theirs (rest of the week)."""
    start = W0 + timedelta(days=7 * i)
    end = start + timedelta(days=6)
    proj_opp = {"pts": 650.0, "reb": 260.0}
    final_opp = {k: opp_banked + opp_ratio * (v - opp_banked) for k, v in proj_opp.items()}
    lineups.store_final(
        conn,
        week=i + 3,
        team_key="me",
        opponent_key=f"{opp}{i % 2}",
        week_start=start,
        week_end=end,
        me={"pts": 600.0, "reb": 250.0},
        opp=final_opp,
        stat_winners={"pts": 1.0, "reb": 1.0},
        status="postevent" if final else "midevent",
    )
    payload = {
        "categories": {k: {"p_win": 0.1, "p_tie": 0.0} for k in ("pts", "reb")},
        "view": "STREAM7",
        "means": {"pts": {"me": 600.0, "opp": 650.0}, "reb": {"me": 250.0, "opp": 260.0}},
        "sd": {"pts": 40.0, "reb": 15.0},
        "spread_scale": 1.0,
        "banked_at": f"{start}T08:00:00+00:00",
    }
    if post:
        payload |= {
            "means_source": "banked + simulated rest (issue #13)",
            "banked": {"me": {"pts": 0.0, "reb": 0.0}, "opp": {"pts": opp_banked, "reb": opp_banked}},
        }
    day = start + timedelta(days=start_offset)
    conn.execute(
        "INSERT INTO predictions(created_at, kind, window_start, window_end, team_key, opponent_key, source, payload) "
        "VALUES ('x','matchup',?,?,'me',?,'ht_spread',?)",
        (str(day), str(end), f"{opp}{i % 2}", json.dumps(payload)),
    )
    return conn.execute("SELECT MAX(id) FROM predictions").fetchone()[0]


# ---------------------------------------------------------------- simulator: at most the startable slots per day


def _team(key: str, n: int) -> FantasyTeam:
    return FantasyTeam(
        key,
        key,
        players=[RosterPlayer(f"{key}{i}", f"P{i}", "BOS", ("PG", "G", "UTIL"), "BN") for i in range(n)],
    )


@pytest.mark.parametrize("key", ["me", "opp"])
def test_each_team_counts_at_most_its_startable_slots_per_day(key):
    days = [date(2026, 11, 3), date(2026, 11, 4)]
    playing = {d: {"BOS"} for d in days}
    value = {f"{key}{i}": float(i) for i in range(8)}
    _, plans = plan_team_week(_team(key, 8), days, playing, ["UTIL", "UTIL", "UTIL"], value)
    for p in plans:  # 8 healthy players all with a game, 3 slots: the 3 best start, nobody else is counted
        assert len(p.started) == 3
        assert set(p.started) == {f"{key}7", f"{key}6", f"{key}5"}


def test_injured_and_il_players_do_not_use_or_add_games():
    team = _team("opp", 4)
    team.players[0].selected = "IL"
    team.players[1].status = "O"
    days = [date(2026, 11, 3)]
    _, plans = plan_team_week(team, days, {days[0]: {"BOS"}}, ["UTIL", "UTIL"], {"opp2": 1.0, "opp3": 1.0})
    assert set(plans[0].started) == {"opp2", "opp3"}


# ---------------------------------------------------------------- ratios (pure)


def rec(i, basis="post_fix", opp_banked=0.0, mean_opp=650.0, final_opp=455.0, week=None, opp="op0", **kw):
    return {
        "id": i,
        "week": week if week is not None else i,
        "opponent_key": opp,
        "basis": basis,
        "means": {"pts": (600.0, mean_opp)},
        "banked": {"me": {}, "opp": {"pts": opp_banked}},
        "final_me": {"pts": 570.0},
        "final_opp": {"pts": final_opp},
    } | kw


def test_ratio_removes_banked_stats_from_both_sides():
    r = orl.realized_ratios([rec(1, opp_banked=250.0, mean_opp=650.0, final_opp=530.0)])
    # projected rest 400, realized rest 280
    assert r["opponents"]["pts"] == pytest.approx(0.7)
    assert r["us"]["pts"] == pytest.approx(0.95)
    assert r["n_weeks"] == 1 and r["forecast_ids"] == [1]


def test_ratio_one_forecast_per_week_earliest_wins_and_short_horizons_skipped():
    recs = [
        rec(5, week=1, final_opp=455.0),  # later forecast of the same week: ignored
        rec(2, week=1, final_opp=455.0),
        # only 23% (opp) / 8% (us) still projected: skipped
        rec(7, week=2, mean_opp=650.0, final_opp=640.0, banked={"me": {"pts": 550.0}, "opp": {"pts": 500.0}}),
    ]
    r = orl.realized_ratios(recs)
    assert r["forecast_ids"] == [2] and r["n_weeks"] == 1


def test_ratio_excludes_pre_fix_and_reports_day0_separately():
    recs = [rec(1), rec(2, basis="pre_fix_day0", final_opp=325.0)]
    assert orl.realized_ratios(recs)["opponents"]["pts"] == pytest.approx(0.7)
    assert orl.realized_ratios(recs, basis="pre_fix_day0")["opponents"]["pts"] == pytest.approx(0.5)


def test_ratio_pooled_volume_weighted_and_per_opponent():
    recs = [rec(1, opp="a", final_opp=325.0), rec(2, opp="b", final_opp=585.0)]
    r = orl.realized_ratios(recs)
    assert r["opponents"]["pts"] == pytest.approx((325 + 585) / 1300)
    assert r["by_opponent"]["a"]["opponents"]["pts"] == pytest.approx(0.5)
    assert r["by_opponent"]["b"]["weeks"] == 1


def test_shrink_factors_need_min_weeks_and_stay_within_bounds():
    ratios = {"n_weeks": 3, "opponents": {"pts": 0.7, "blk": 0.3, "ast": 1.2}}
    assert orl.shrink_factors(ratios, 4) == {}
    assert orl.shrink_factors({**ratios, "n_weeks": 4}, 4) == {"pts": 0.7, "blk": orl.SHRINK_FLOOR, "ast": 1.0}


def test_shrink_opponent_means_only_moves_the_rest_of_the_week():
    means = {"pts": (600.0, 650.0), "fg_pct": (0.5, 0.5), "tov": (80.0, 90.0)}
    out = ht_odds.shrink_opponent_means(means, {"pts": 250.0, "tov": 90.0}, {"pts": 0.5, "fg_pct": 0.5, "tov": 0.5},
                                        NINE_CAT)  # fmt: skip
    assert out["pts"] == (600.0, 450.0)  # 250 banked + 0.5 * 400
    assert out["fg_pct"] == (0.5, 0.5)  # percent categories untouched
    assert out["tov"] == (80.0, 90.0)  # nothing left to shrink


# ---------------------------------------------------------------- odds_reliability report


def test_report_shape_exclusions_and_calibration(conn):
    for i in range(4):
        add_week(conn, i, opp="op")
    legacy_day0 = add_week(conn, 4, post=False, opp_ratio=0.5)
    legacy_mid = add_week(conn, 5, post=False, start_offset=2)
    r = orl.report(conn)
    assert r["n_weeks"] == 6 and r["pre_fix_forecasts"] == 2
    ratio = r["opponent_ratio"]
    assert ratio["n_weeks"] == 4 and legacy_day0 not in ratio["forecast_ids"]
    assert ratio["opponents"]["pts"] == pytest.approx(0.7) and ratio["us"]["pts"] == pytest.approx(1.0)
    assert ratio["mean_opponents"] == pytest.approx(0.7)
    assert set(ratio["by_opponent"]) == {"op0", "op1"}
    assert ratio["by_opponent"]["op0"] == {"weeks": 2, "mean_ratio": pytest.approx(0.7)}  # compact by default
    assert ratio["excluded_forecasts"] == {"pre_fix_not_day0": 1}
    assert legacy_mid not in ratio["forecast_ids"]
    ref = ratio["pre_fix_day0_reference"]
    assert ref["forecast_ids"] == [legacy_day0] and ref["opponents"]["pts"] == pytest.approx(0.5)
    shrink = ratio["shrink"]
    assert shrink["enabled"] is False and shrink["active"] is False and shrink["graded_weeks"] == 4
    assert shrink["factors_if_enabled"]["pts"] == pytest.approx(0.7)
    assert r["by_basis"]["post_fix"]["n_weeks"] == 4 and r["by_basis"]["pre_fix"]["n_weeks"] == 2
    assert "calibration" in r["by_basis"]["post_fix"]
    for band in r["calibration"]:
        assert set(band) == {"band", "n", "mean_confidence", "favorite_won", "gap", "brier"}
    detail = orl.report(conn, detail=True)["opponent_ratio"]["by_opponent"]["op0"]
    assert detail["opponents"]["pts"] == pytest.approx(0.7)


def test_calibration_exposes_confident_favorites_that_lose():
    pairs = [("pts", 0.9, 1.0)] * 3 + [("pts", 0.65, 0.0)] * 3 + [("reb", 0.35, 1.0)] * 1 + [("reb", 0.5, 0.5)]
    bands = {b["band"]: b for b in orl._confidence_bands(pairs)}
    assert bands["90-100%"]["favorite_won"] == 1.0 and bands["90-100%"]["gap"] == pytest.approx(0.1)
    assert bands["60-70%"]["n"] == 4 and bands["60-70%"]["favorite_won"] == 0.0
    assert bands["60-70%"]["gap"] < -0.6  # mid-range calls missed
    assert bands["50-60%"]["n"] == 1 and bands["50-60%"]["mean_confidence"] == 0.5


def test_pending_weeks_and_means_without_banked_are_not_ratio_inputs(conn):
    add_week(conn, 0, final=False)
    pid = add_week(conn, 1)
    payload = json.loads(conn.execute("SELECT payload FROM predictions WHERE id=?", (pid,)).fetchone()[0])
    del payload["banked"]  # a post-#13 forecast stored before the banked stats were recorded
    conn.execute("UPDATE predictions SET payload=? WHERE id=?", (json.dumps(payload), pid))
    r = orl.report(conn)
    assert r["pending_weeks_not_final"] == 1
    assert r["opponent_ratio"]["n_weeks"] == 0 and r["opponent_ratio"]["excluded_forecasts"] == {"no_banked": 1}


# ---------------------------------------------------------------- the policy-gated shrink


def test_policy_default_is_no_shrink(conn):
    assert policy.get(conn, "policy.opponent_shrink") is False
    assert policy.REGISTRY["policy.opponent_shrink"].kind == "bool"
    assert "policy.opponent_shrink" in policy.all_values(conn)


def test_live_shrink_off_by_default_never_reads_forecasts(conn, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("forecasts must not be read while the shrink is off")

    monkeypatch.setattr(orl, "graded_forecasts", boom)
    assert orl.live_shrink(conn, policy.all_values(conn), "STREAM7") == {"enabled": False, "n_weeks": 0, "factors": {}}


def test_live_shrink_needs_four_graded_weeks_when_enabled(conn):
    on = {**policy.all_values(conn), "policy.opponent_shrink": True}
    for i in range(3):
        add_week(conn, i)
    assert orl.live_shrink(conn, on, "STREAM7") == {"enabled": True, "n_weeks": 3, "factors": {}}
    add_week(conn, 3)
    s = orl.live_shrink(conn, on, "STREAM7")
    assert s["n_weeks"] == 4 and s["factors"]["pts"] == pytest.approx(0.7) and s["factors"]["reb"] == pytest.approx(0.7)
    assert orl.live_shrink(conn, policy.all_values(conn), "STREAM7")["factors"] == {}  # still off with default policy


def test_policy_tuning_judges_shrink_by_leave_one_week_out_brier(conn):
    for i in range(3):
        add_week(conn, i)
    assert not [s for s in policy_tuning.suggest(conn) if s["key"] == "policy.opponent_shrink"]  # < min_weeks
    add_week(conn, 3)
    off, on = policy_tuning.evaluate(conn, "policy.opponent_shrink", [False, True])
    assert on["brier"] < off["brier"] and off["n_weeks"] == 4
    (sugg,) = [s for s in policy_tuning.suggest(conn) if s["key"] == "policy.opponent_shrink"]
    assert sugg["value"] is True and sugg["current"] is False and sugg["metric"] == "brier_reduction"
    payload = policy_tuning.draft_proposal_payload(sugg)
    assert payload["payload"]["changes"] == {"policy.opponent_shrink": True}
    assert policy.get(conn, "policy.opponent_shrink") is False  # suggestions never apply anything
