"""Issue #13: official HT-mode weekly odds are banked + simulated rest of the week, never HT's planner AFTER totals
(which counted days already played). Synthetic fixtures only (no real HT data)."""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
from types import SimpleNamespace

import numpy as np
import pytest

from fbm.analytics import ht_odds
from fbm.analytics.categories import NINE_CAT
from fbm.analytics.simulation import COL, STATS, TeamWeek
from fbm.gm import health
from fbm.gm import services as svc
from fbm.hashtag import importer
from fbm.hashtag import parsers as P
from fbm.hashtag.dom import parse_html

LINE = {"pts": 20.0, "tpm": 2.0, "reb": 6.0, "ast": 4.0, "stl": 1.0, "blk": 0.5, "tov": 2.0, "fgm": 7.0, "fga": 15.0,
        "ftm": 3.0, "fta": 4.0}  # fmt: skip
CATS = [c.key for c in NINE_CAT]


def sim_totals(banked: dict[str, float], games: int, n: int = 600, seed: int = 1) -> np.ndarray:
    """Simulated week totals: banked stats + `games` player-games at LINE with some game-to-game noise."""
    rng = np.random.default_rng(seed)
    out = np.zeros((n, len(STATS)))
    for s, v in banked.items():
        out[:, COL[s]] += v
    for s, v in LINE.items():
        out[:, COL[s]] += games * v + rng.normal(0.0, 1.5 * max(games, 0) ** 0.5, n) * (1 if games else 0)
    return out


ME_BANKED = {"pts": 554.0, "tpm": 49.0, "reb": 200.0, "ast": 120.0, "stl": 30.0, "blk": 15.0, "tov": 60.0,
             "fgm": 200.0, "fga": 420.0, "ftm": 90.0, "fta": 100.0}  # fmt: skip
OPP_BANKED = {"pts": 417.0, "tpm": 46.0, "reb": 190.0, "ast": 110.0, "stl": 28.0, "blk": 14.0, "tov": 55.0,
              "fgm": 160.0, "fga": 360.0, "ftm": 70.0, "fta": 100.0}  # fmt: skip


def planner_row(me_gap: float, opp_gap: float) -> dict:
    """HT planner rows: every counting category's AFTER = BEFORE + a gap scaled to pts (pct categories left flat)."""
    out = {}
    for c in NINE_CAT:
        if c.kind == "pct":
            out[c.key] = {"me_before": 0.45, "opp_before": 0.45, "me_after": 0.46, "opp_after": 0.45, "ht_share": 0.5}
            continue
        f = LINE[c.stat] / LINE["pts"]
        mb, ob = ME_BANKED[c.stat], OPP_BANKED[c.stat]
        out[c.key] = {"me_before": mb, "opp_before": ob, "me_after": mb + me_gap * f, "opp_after": ob + opp_gap * f,
                      "ht_share": 0.5}  # fmt: skip
    return out


@pytest.fixture
def odds_for(monkeypatch):
    """_ht_week_odds with the DB/sim plumbing replaced: `games` = (me, opp) player-games still to play."""

    def run(games: tuple[int, int], ht_gap: tuple[float, float], me_banked=None, opp_banked=None):
        me_b, opp_b = me_banked or ME_BANKED, opp_banked or OPP_BANKED

        def fake_matchup(league, me, opp, start_day, n, view=None, totals=None):
            totals["me"], totals["opp"] = sim_totals(me_b, games[0], seed=3), sim_totals(opp_b, games[1], seed=4)
            mw, ow = TeamWeek("t1", [], dict(me_b)), TeamWeek("t2", [], dict(opp_b))
            return None, mw, ow, date(2026, 11, 7), date(2026, 11, 8), {}, [], []

        monkeypatch.setattr(svc, "_conn", lambda: object())
        monkeypatch.setattr(
            svc._policy,
            "all_values",
            lambda c: {"policy.view.weekly_odds": "STREAM7", "policy.odds_spread_scale": 1.0},
        )
        monkeypatch.setattr(
            "fbm.hashtag.store.matchup_totals",
            lambda *a, **k: {"categories": planner_row(*ht_gap), "pulled_at": "x"},
        )
        monkeypatch.setattr(svc, "_matchup", fake_matchup)
        monkeypatch.setattr(svc, "_ht_injury_adjustment", lambda *a, **k: {"me": {}, "opp": {}, "players": []})
        league = SimpleNamespace(week=3, categories=list(NINE_CAT), my_team=SimpleNamespace(team_key="t1"))
        return svc._ht_week_odds(league, SimpleNamespace(team_key="t2", name="Opp"), 600)

    return run


def test_mid_week_means_are_banked_plus_rest_only(odds_for):
    # three days left (12 / 10 player-games); HT's AFTER still counts the whole week (+800 PTS)
    w = odds_for((12, 10), (800.0, 780.0))
    assert w["means"]["pts"][0] == pytest.approx(554 + 12 * 20, abs=4)
    assert w["means"]["pts"][1] == pytest.approx(417 + 10 * 20, abs=4)
    assert w["means"]["fg_pct"][0] == pytest.approx((200 + 12 * 7) / (420 + 12 * 15), abs=0.003)
    assert w["ht_means"]["pts"][0] == pytest.approx(554 + 800)  # HT's grid kept for reference only
    assert w["means"]["pts"][0] < w["ht_means"]["pts"][0] - 400
    assert not w["planner_check"]["ok"]


def test_leading_a_category_late_in_the_week_is_not_a_coin_flip(odds_for):
    # banked 3PM 49 vs 46 with 6 games each to go: we lead, the old full-week AFTER put us behind
    w = odds_for((6, 6), (800.0, 800.0))
    assert w["p_win"]["tpm"] > 0.6
    assert w["p_win"]["pts"] > 0.95  # 554 vs 417 banked


def test_last_day_before_tip_gap_is_the_rest_of_the_day(odds_for):
    w = odds_for((3, 3), (800.0, 780.0))
    gap_me = w["means"]["pts"][0] - ME_BANKED["pts"]
    assert gap_me == pytest.approx(3 * 20, abs=3)
    flags = {(f["cat"], f["side"]) for f in w["planner_check"]["flags"]}
    assert ("pts", "me") in flags and ("pts", "opp") in flags


def test_last_day_all_final_means_equal_banked_and_odds_are_binary(odds_for):
    tie_b = dict(OPP_BANKED, blk=ME_BANKED["blk"])
    w = odds_for((0, 0), (0.0, 0.0), opp_banked=tie_b)
    for k in ("pts", "tpm", "reb", "ast", "stl", "blk", "tov"):
        stat = k
        assert w["means"][k] == (ME_BANKED[stat], tie_b[stat])
    assert w["means"]["fg_pct"][0] == pytest.approx(200 / 420)
    assert w["p_win"]["pts"] == 1.0 and w["p_win"]["tpm"] == 1.0
    assert w["p_win"]["tov"] == 0.0  # 60 vs 55 turnovers: fewer is better
    assert w["p_win"]["blk"] == 0.5  # equal banked totals are a tie, not a win
    assert w["planner_check"]["ok"]


def test_planner_check_quiet_when_ht_gap_is_the_rest_of_the_week(odds_for):
    w = odds_for((12, 10), (12 * 20.0, 10 * 20.0))
    assert w["planner_check"]["ok"] and w["planner_check"]["flags"] == []


def test_opponent_shrink_off_by_default_and_banked_untouched_when_on(odds_for, monkeypatch):
    base = odds_for((12, 10), (800.0, 780.0))
    assert base["shrink"]["factors"] == {} and base["means"] == base["means_raw"]
    from fbm.gm import odds_reliability

    monkeypatch.setattr(
        odds_reliability, "live_shrink", lambda *a, **k: {"enabled": True, "n_weeks": 4, "factors": {"pts": 0.8}}
    )
    w = odds_for((12, 10), (800.0, 780.0))
    rest = base["means"]["pts"][1] - OPP_BANKED["pts"]
    assert w["means"]["pts"][1] == pytest.approx(OPP_BANKED["pts"] + 0.8 * rest)
    assert w["means"]["pts"][0] == base["means"]["pts"][0]  # our side is never shrunk
    assert w["means_raw"]["pts"] == base["means"]["pts"]  # the unshrunk projection is kept for the ratios
    assert w["means"]["reb"] == base["means"]["reb"]  # no factor, no change


def test_planner_check_function_pure():
    gap = {"pts": (800.0, 780.0)}
    rest = {"pts": (60.0, 60.0)}
    sd = {"pts": (12.0, 12.0)}
    bad = ht_odds.planner_check(gap, rest, sd)
    assert not bad["ok"] and {f["side"] for f in bad["flags"]} == {"me", "opp"}
    assert "Days Played" in bad["message"]
    assert ht_odds.planner_check({"pts": (70.0, 55.0)}, rest, sd)["ok"]
    assert ht_odds.planner_check({"pts": (0.0, 0.0)}, {"pts": (0.0, 0.0)}, {"pts": (0.0, 0.0)})["ok"]
    assert not ht_odds.planner_check({"pts": (100.0, 0.0)}, {"pts": (0.0, 0.0)}, {"pts": (0.0, 0.0)})["ok"]


def test_means_from_totals_uses_made_over_attempted():
    me = np.zeros((2, len(STATS)))
    me[:, COL["fgm"]], me[:, COL["fga"]], me[:, COL["pts"]] = (4.0, 6.0), (10.0, 10.0), (10.0, 20.0)
    m = ht_odds.means_from_totals(me, me, ["fg_pct", "pts"])
    assert m["fg_pct"][0] == pytest.approx(0.5) and m["pts"][0] == 15.0


# ------------------------------------------------------------------ importer: Days Played


def test_parse_days_played_and_fields_from_fixture():
    from pathlib import Path

    doc = parse_html((Path(__file__).parent / "fixtures" / "hashtag" / "h2h_planner.html").read_text("utf-8"))
    boxes = P.parse_days_played(doc)
    assert list(boxes) == ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]
    assert not any(on for _, on in boxes.values())
    thu = importer.played_days_fields(doc, date(2026, 11, 2), date(2026, 11, 8), date(2026, 11, 5))
    assert [d for d, (n, _) in boxes.items() if thu[n]] == ["MON", "TUE", "WED"]
    assert sum(v is None for v in thu.values()) == 4
    monday = importer.played_days_fields(doc, date(2026, 11, 2), date(2026, 11, 8), date(2026, 11, 2))
    assert all(v is None for v in monday.values())
    last = importer.played_days_fields(doc, date(2026, 11, 2), date(2026, 11, 8), date(2026, 11, 9))
    assert all(v == "on" for v in last.values())
    assert importer.played_days_fields(doc, date(2026, 10, 19), date(2026, 11, 1), date(2026, 10, 25)) == {}
    assert importer.played_days_fields(doc, None, None, date(2026, 11, 5)) == {}


def test_played_days_fields_safe_without_checkboxes():
    doc = parse_html("<html><body><form></form></body></html>")
    assert P.parse_days_played(doc) == {}
    assert importer.played_days_fields(doc, date(2026, 11, 2), date(2026, 11, 8), date(2026, 11, 5)) == {}


def test_importer_posts_past_days_ticked(tmp_path, monkeypatch):
    from fbm.hashtag import ids as ht_ids
    from fbm.store import db
    from tests.test_hashtag_importer import FakeHT, make_client, make_league

    monkeypatch.setattr(ht_ids, "OVERRIDES_PATH", tmp_path / "none.toml")
    conn = db.connect(tmp_path / "fbm.db")
    league = make_league()
    league.week_start, league.week_end = date(2026, 10, 26), date(2026, 11, 1)
    fake = FakeHT()
    now = datetime(2026, 10, 29, 16, 0, tzinfo=UTC)  # Thursday ET
    report = importer.run_import(
        conn, league=league, client=make_client(fake), now=now, league_id="11111", pages=["h2h_planner"]
    )
    assert report["pages"]["h2h_planner:STREAM7"]["status"] == "ok", report["pages"]
    assert "days played ticked: MON,TUE,WED" in report["pages"]["h2h_planner:STREAM7"]["detail"]
    posts = [f for m, p, f in fake.requests if m == "POST" and "ctl00$ContentPlaceHolder1$DDDURATION" in f]
    first = posts[0]
    ticked = {re.sub(r".*\$", "", k) for k in first if "$FVDAYS$CBM" in k}
    assert ticked == {"CBM1", "CBM2", "CBM3"}
    conn.close()


# ------------------------------------------------------------------ health


def _pulls(first_gap: float, last_gap: float, last_before: float) -> list[dict]:
    rows = []
    for stamp, day, before, gap in (
        ("2026-11-02T09:00", "2026-11-02", 0.0, first_gap),
        ("2026-11-07T09:00", "2026-11-07", last_before, last_gap),
    ):
        for cat in ("pts", "reb"):
            rows.append({"pulled_at": stamp, "et_date": day, "category": cat, "me_before": before,
                         "opp_before": before, "me_after": before + gap, "opp_after": before + gap})  # fmt: skip
    return rows


def test_health_flags_planner_that_counts_banked_days():
    bad = health.check_ht_planner(_pulls(800.0, 790.0, 514.0))  # the issue's pattern
    assert bad["status"] == health.WARN and "Days Played" in bad["fix"]
    assert health.check_ht_planner(_pulls(800.0, 286.0, 514.0))["status"] == health.OK
    assert health.check_ht_planner(_pulls(800.0, 790.0, 20.0))["status"] == health.OK  # too early to judge
    assert health.check_ht_planner(_pulls(800.0, 800.0, 0.0)[:2])["status"] == health.OK  # single pull
