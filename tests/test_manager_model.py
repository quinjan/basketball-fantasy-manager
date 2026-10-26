"""Empirical-Bayes manager behavior model: estimators, shrinkage, recency, evidence, and the consumers."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from fbm.analytics import manager_model as mm
from fbm.analytics import opponents as op
from fbm.gm import services as svc
from fbm.yahoo.league_reads import Transaction, TxPlayer

A, B, Z = "466.l.1.t.1", "466.l.1.t.2", "466.l.1.t.3"
TEAMS = {A: "Alpha", B: "Bravo", Z: "Zulu"}
NOW = datetime(2026, 12, 1, 12, tzinfo=UTC)


def add(key: str, days_ago: float, team: str, pid: str, source: str = "freeagents", status: str = "successful"):
    p = TxPlayer(f"p.{pid}", pid, f"P{pid}", "BOS", "add", source, "", "team", team)
    return Transaction(key, "add", status, NOW - timedelta(days=days_ago), (p,))


def drop(key: str, days_ago: float, team: str, pid: str):
    p = TxPlayer(f"p.{pid}", pid, f"P{pid}", "BOS", "drop", "team", team, "waivers", "")
    return Transaction(key, "drop", "successful", NOW - timedelta(days=days_ago), (p,))


def busy_league() -> list[Transaction]:
    """Alpha adds ~3 a week (some claims, some quick drops); Bravo one add; Zulu nothing, over four weeks."""
    out = []
    for i in range(12):
        out.append(add(f"a{i}", 27 - i * 2.2, A, str(100 + i), "waivers" if i % 3 == 0 else "freeagents"))
    out.append(drop("d0", 27 - 0.5, A, "100"))  # 100 was added 27 days ago: a stream
    out.append(add("b0", 10, B, "200"))
    return out


def model(txs, **kw):
    return mm.estimate_managers(txs, TEAMS, NOW, **kw)


def test_rate_estimate_math():
    e = mm.rate_estimate(count=6, exposure=3, prior_mean=2, strength=2)
    assert e.posterior == pytest.approx((2 * 2 + 6) / (2 + 3)) and e.observed == 2.0
    assert e.evidence_share == pytest.approx(3 / 5) and e.ess == 3
    none = mm.rate_estimate(0, 0, 1.5, 2)
    assert none.posterior == 1.5 and none.observed is None and none.evidence_share == 0


def test_prob_estimate_math():
    e = mm.prob_estimate(successes=3, trials=4, prior_mean=0.5, strength=4)
    assert e.posterior == pytest.approx(5 / 8) and e.observed == 0.75 and e.evidence_share == 0.5
    assert 0 < e.sd < 0.5
    assert mm.prob_estimate(0, 0, 0.3, 3).posterior == pytest.approx(0.3)


def test_shrinks_toward_league_prior():
    m = model(busy_league(), max_weekly_adds=4)
    a, b, z = (m[k].adds_per_week for k in (A, B, Z))
    assert a.prior == b.prior == z.prior  # one league-wide prior
    assert a.posterior > a.prior > z.posterior  # busy above, idle below the league
    assert z.observed == 0 and z.posterior > 0  # an idle manager is shrunk, not zeroed
    assert a.prior < a.posterior < a.observed  # pulled back toward the prior
    assert 0 < a.evidence_share < 1 and a.ess == m[Z].adds_per_week.ess == m[A].exposure_weeks
    assert m[A].claim_participation.posterior > m[Z].claim_participation.posterior
    assert m[A].claims_per_week.observed > 0 and m[B].claims_per_week.observed == 0


def test_more_exposure_means_more_evidence():
    short = model(busy_league())[Z].adds_per_week
    now_later = NOW + timedelta(days=28)
    late = mm.estimate_managers(busy_league(), TEAMS, now_later)[Z].adds_per_week
    assert late.ess > short.ess * 0.5  # exposure keeps accruing (recency decay aside)
    long_idle = [add("x", 80, A, "1")]
    idle = mm.estimate_managers(long_idle, TEAMS, NOW)[Z].adds_per_week
    assert idle.evidence_share > short.evidence_share
    assert idle.posterior < short.posterior  # longer silence -> lower posterior


def test_recency_weighting():
    recent = model([add(f"r{i}", 3 + i * 0.1, A, str(i)) for i in range(4)] + [add("x", 20, B, "9")])
    old = model([add(f"r{i}", 40 + i * 0.1, A, str(i)) for i in range(4)] + [add("x", 60, B, "9")])
    assert recent[A].adds_per_week.posterior > old[A].adds_per_week.posterior


def test_no_league_history_uses_constants():
    m = model([])
    a = m[A]
    assert not a.league_history and a.adds_per_week.posterior == pytest.approx(float(svc.C("sim.opponent_stream_adds")))
    assert a.claim_participation.posterior == pytest.approx(float(svc.C("waiver.manager_activity")))
    assert a.adds_per_week.observed is None and a.near_limit_rate is None


def test_lost_claims_count_as_participation_not_adds():
    base = model(busy_league())[B]
    lost = model([*busy_league(), add("l1", 2, B, "300", "waivers", status="failed")])[B]
    assert lost.claims_lost == 1 and lost.adds_per_week.observed == base.adds_per_week.observed
    assert lost.claim_participation.posterior > base.claim_participation.posterior
    assert lost.claims_per_week.observed > base.claims_per_week.observed


def test_near_weekly_limit_is_streaming_signal():
    heavy = [add(f"h{i}", 2 + i * 0.1, A, str(i)) for i in range(3)]  # 3 adds in a week of a 4-add limit
    m = model([*heavy, add("b", 2, B, "9")], max_weekly_adds=4)
    assert m[A].near_limit_rate.observed > 0 and m[B].near_limit_rate.observed == 0
    assert m[A].near_limit_rate.posterior > m[B].near_limit_rate.posterior
    assert model(heavy)[A].near_limit_rate is None  # no add limit in this league


def test_stream_fraction_counts_quick_drops():
    m = model(busy_league())
    assert m[A].stream_fraction.observed > 0
    assert m[B].stream_fraction.observed == 0
    assert m[Z].stream_fraction.observed is None and m[Z].stream_fraction.evidence_share == 0


def test_preseason_activity_is_downweighted():
    txs = [add(f"p{i}", 14 + i * 0.1, A, str(i)) for i in range(4)] + [add("r", 1, B, "9")]
    start = NOW - timedelta(days=7)
    flat, damped = model(txs)[A], model(txs, league_start=start)[A]
    assert damped.pre_season_adds == 4 and flat.pre_season_adds == 0
    assert damped.adds_per_week.observed is not None and flat.adds_per_week.observed is not None
    assert damped.exposure_weeks < flat.exposure_weeks and damped.adds_per_week.posterior < flat.adds_per_week.posterior


def test_trades_are_counted():
    trade = Transaction("t", "trade", "successful", NOW - timedelta(days=2), (), trader_team_key=A, tradee_team_key=B)
    m = model([*busy_league(), trade])
    assert m[A].trade_events_per_week.observed > 0 and m[B].trade_events_per_week.observed > 0
    assert m[Z].trade_events_per_week.observed == 0 and m[Z].trade_events_per_week.posterior > 0


def test_claim_probability_weights_by_gain():
    a = model(busy_league())[A]
    small, big = mm.claim_probability(a, 0.1), mm.claim_probability(a, 5.0)
    assert small < big <= a.claim_participation.posterior
    assert mm.claim_probability(a, 0) == float(svc.C("intel.activity_floor"))


def test_expected_adds_caps_and_used():
    a = model(busy_league())[A]
    mean = a.adds_per_week.posterior
    assert mm.expected_adds(a) == int(mean + 0.5)
    assert mm.expected_adds(a, used=1) == max(int(mean - 1 + 0.5), 0)
    assert mm.expected_adds(a, used=3, limit=4) <= 1
    assert mm.expected_adds(a, limit=0) == 0


def test_claim_activity_feed_needs_league_history():
    profiles = op.profile_teams(busy_league(), TEAMS, NOW, None)
    gains = {A: 3.0, B: 0.2}
    act = op.claim_activity(profiles, gains)
    assert set(act) == {A, B} and act[A] > act[B]  # Zulu has no gain -> not listed (waiver default applies)
    bare = op.claim_activity(profiles)
    assert set(bare) == set(TEAMS) and bare[A] == pytest.approx(profiles[A].model.claim_participation.posterior)
    assert op.claim_activity(op.profile_teams([], TEAMS, NOW, None), gains) == {}


def test_profile_dict_and_markdown_show_prior_observed_posterior():
    p = op.profile_teams(busy_league(), TEAMS, NOW, None, max_weekly_adds=4)[A]
    m = p.to_dict()["model"]
    assert set(m["adds_per_week"]) == {"prior", "observed", "posterior", "sd", "ess", "evidence_share"}
    md = op.profile_markdown(p, "now")
    assert "Modeled behavior" in md and "prior" in md and "posterior" in md and "Claim participation" in md


def test_silent_manager_is_not_inactive_before_enough_observation():
    young = op.profile_teams([add("a", 1, A, "1")], TEAMS, NOW, None)
    assert young[Z].tier == "casual"  # 1 day watched, no move: read the shrunk rate, not "inactive"
    old = op.profile_teams([add("a", 30, A, "1"), add("b", 0.5, A, "2")], TEAMS, NOW, None)
    assert old[Z].tier == "inactive"


def league(max_adds=None, used=None):
    return SimpleNamespace(
        teams=[SimpleNamespace(team_key=k, name=v) for k, v in TEAMS.items()],
        max_weekly_adds=max_adds,
        adds_used=used or {},
        source="demo",
    )


def test_opponent_adds_comes_from_the_manager_model(monkeypatch):
    monkeypatch.setattr(svc, "_tx_history", busy_league)
    monkeypatch.setattr(svc, "datetime", SimpleNamespace(now=lambda tz=None: NOW))
    lg = league()
    profiles = svc._manager_profiles(lg)
    for key in (A, Z):
        adds, source = svc._opponent_adds(lg, SimpleNamespace(team_key=key), None)
        assert adds == mm.expected_adds(profiles[key].model) and source.startswith("manager model")
    # A modeled busy manager streams more than an idle one; neither is the flat constant by construction
    assert (
        svc._opponent_adds(lg, SimpleNamespace(team_key=A), None)[0]
        > svc._opponent_adds(lg, SimpleNamespace(team_key=Z), None)[0]
    )
    assert "league prior" in svc._opponent_adds(lg, SimpleNamespace(team_key=Z), None)[1]


def test_opponent_adds_caps_used_and_caller_override(monkeypatch):
    monkeypatch.setattr(svc, "_tx_history", busy_league)
    monkeypatch.setattr(svc, "datetime", SimpleNamespace(now=lambda tz=None: NOW))
    free = svc._opponent_adds(league(), SimpleNamespace(team_key=A), None)[0]
    assert free >= 2
    lg = league(max_adds=4, used={A: 3})
    adds, source = svc._opponent_adds(lg, SimpleNamespace(team_key=A), None)
    assert adds <= 1 and "3 already used" in source  # their adds left, and what they've used counts
    assert svc._opponent_adds(lg, SimpleNamespace(team_key=A), None, week_in_progress=False)[0] == free
    assert svc._opponent_adds(lg, SimpleNamespace(team_key=A), 3) == (1, "caller")  # still capped by adds left
    assert svc._opponent_adds(league(), SimpleNamespace(team_key=A), 0) == (0, "caller")


def test_opponent_adds_constant_when_league_has_no_history(monkeypatch):
    monkeypatch.setattr(svc, "_tx_history", lambda: [])
    adds, source = svc._opponent_adds(league(), SimpleNamespace(team_key=A), None)
    assert adds == svc.C("sim.opponent_stream_adds") and source.startswith("constant")


def test_intel_row_and_claim_basis_expose_the_model(monkeypatch):
    monkeypatch.setattr(svc, "_tx_history", busy_league)
    monkeypatch.setattr(svc, "datetime", SimpleNamespace(now=lambda tz=None: NOW))
    profiles = svc._manager_profiles(league())
    row = svc._intel_row(profiles[A])
    assert row["model"]["league_history"] and row["model"]["adds_per_week"] > row["model"]["stream_fraction"]
    basis = svc._claim_basis(profiles[A])
    assert "prior" in basis and "observed" in basis and "posterior" in basis and "evidence" in basis
    assert svc._claim_basis(None) == ""
