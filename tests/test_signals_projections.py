"""ESPN projection baseline, ESPN blend + calibration, and early signals."""

from datetime import UTC, date, datetime, timedelta

import pandas as pd
import pytest

from fbm import constants
from fbm.analytics import players as player_tables
from fbm.analytics import projection_calibration as calib
from fbm.analytics import signals as sig
from fbm.analytics.valuation import ValuationConfig, value_players
from fbm.data import espn_fantasy
from fbm.gm import signals as feed
from fbm.store import db

STATS = ("minutes", "pts", "reb", "ast", "stl", "blk", "tov", "tpm", "tpa", "fgm", "fga", "ftm", "fta")


@pytest.fixture(autouse=True)
def no_overrides(tmp_path, monkeypatch):
    monkeypatch.setattr(constants, "OVERRIDES_PATH", tmp_path / "none.toml")
    monkeypatch.setattr(constants, "_cache", {"mtime": None, "values": {}})


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    c.execute("INSERT INTO nba_teams(espn_id, abbr, name) VALUES ('7', 'DEN', 'Denver'), ('6', 'DAL', 'Dallas')")
    yield c
    c.close()


def _line(**over):
    return dict.fromkeys(STATS, 1.0) | {"minutes": 30.0, "pts": 15.0, "fga": 10.0, "fgm": 5.0, "fta": 2.0} | over


def _avg(conn, season, pid, gp, team="TM", **over):
    vals = _line(**over)
    conn.execute(
        f"INSERT INTO season_averages(season, espn_id, name, team, position, gp, {', '.join(STATS)}, updated_at) "
        f"VALUES (?,?,?,?,?,?,{','.join('?' * len(STATS))},'x')",
        (season, pid, pid, team, "G", gp, *[vals[s] for s in STATS]),
    )


def _snap(conn, season, day, pid, team="DEN", **over):
    vals = _line(**over)
    stats = {"gp": 70.0, **{s: vals[s] for s in STATS}}
    espn_fantasy.store_projections(conn, season, day, [espn_fantasy.ProjectionLine(pid, f"P{pid}", team, "PG", stats)])


def _opening(conn, season, day):
    conn.execute(
        "INSERT INTO games(event_id, season, season_type, start_utc, et_date, home, away, state, completed, neutral, "
        "time_valid, updated_at) VALUES (?,?,2,?,?,'DEN','DAL','pre',0,0,1,'x')",
        (f"g{day}", season, f"{day}T23:00:00+00:00", day),
    )


# ------------------------------------------------------------------ ESPN projections


def _payload(season):
    proj = {"seasonId": season, "statSourceId": 1, "statSplitTypeId": 0, "averageStats": {"42": 72.0, "40": 32.2}}
    proj["averageStats"] |= {"0": 20.1, "6": 4.9, "3": 2.8, "2": 1.0, "1": 0.8, "11": 1.9, "13": 7.3, "14": 15.3}
    proj["averageStats"] |= {"15": 4.8, "16": 5.9, "17": 0.7, "18": 2.1}
    return {
        "players": [
            {"id": 1, "player": {"fullName": "Rookie A", "proTeamId": 6, "defaultPositionId": 3, "stats": [proj]}},
            {"id": 2, "player": {"fullName": "No Proj", "proTeamId": 7, "defaultPositionId": 5, "stats": []}},
            {"id": 3, "player": {"fullName": "Old Year", "proTeamId": 0, "stats": [proj | {"seasonId": season - 1}]}},
        ]
    }


def test_parse_projections_maps_stats_team_position():
    lines = espn_fantasy.parse_projections(_payload(2027), 2027, {"6": "DAL", "7": "DEN"})
    assert [p.espn_id for p in lines] == ["1"]
    p = lines[0]
    assert (p.name, p.team, p.position) == ("Rookie A", "DAL", "SF")
    assert p.stats["pts"] == 20.1 and p.stats["reb"] == 4.9 and p.stats["minutes"] == 32.2 and p.stats["gp"] == 72


def test_baseline_is_last_preseason_snapshot_and_frozen(conn):
    _opening(conn, 2027, "2026-10-20")
    _snap(conn, 2027, "2026-10-10", "a", pts=10.0)
    _snap(conn, 2027, "2026-10-18", "a", pts=12.0)
    _snap(conn, 2027, "2026-11-05", "a", pts=20.0)  # in-season revision: not the baseline
    _snap(conn, 2027, "2026-11-01", "late", pts=8.0)  # first projected after opening night
    _snap(conn, 2027, "2026-11-05", "late", pts=9.0)
    base = player_tables.espn_baseline_table(conn, 2027)
    assert base.loc["a", "pts"] == 12.0
    assert base.loc["late", "pts"] == 8.0
    latest = player_tables.espn_snapshot_table(conn, 2027)
    assert latest.loc["a", "pts"] == 20.0
    week_ago = player_tables.espn_snapshot_table(conn, 2027, on_or_before="2026-11-02")
    assert week_ago.loc["a", "pts"] == 12.0


def test_prior_prefers_espn_then_last_season_and_values_rookies(conn):
    _snap(conn, 2027, "2026-10-15", "rookie", pts=20.0)
    _snap(conn, 2027, "2026-10-15", "vet", pts=25.0)
    _avg(conn, 2026, "vet", 70, pts=18.0)
    _avg(conn, 2026, "other", 70, pts=12.0)
    prior = player_tables.prior_table(conn, 2027, 2026)
    assert prior.loc["vet", "pts"] == 25.0 and prior.loc["vet", "prior_source"] == "espn_preseason"
    assert prior.loc["other", "prior_source"] == "last_season"
    proj = player_tables.engine_projection_table(conn, 2027, 2026)
    assert proj.loc["rookie", "pts"] == pytest.approx(20.0)
    assert proj.loc["rookie", "prior_source"] == "espn_preseason"


def test_current_season_shrinks_toward_espn_baseline(conn):
    _snap(conn, 2027, "2026-10-15", "p", pts=20.0)
    _avg(conn, 2027, "p", 15, pts=30.0)
    proj = player_tables.engine_projection_table(conn, 2027, 2026)
    assert proj.loc["p", "weight_current"] == pytest.approx(0.5)
    assert proj.loc["p", "pts"] == pytest.approx(25.0)


def test_blend_espn_weights_only_shared_players():
    engine = pd.DataFrame({s: [10.0, 10.0] for s in STATS}, index=["a", "b"])
    espn = pd.DataFrame({s: [20.0] for s in STATS}, index=["a"])
    out = player_tables.blend_espn(engine, espn, 0.25)
    assert out.loc["a", "pts"] == pytest.approx(12.5)
    assert out.loc["b", "pts"] == 10.0
    assert out.loc["a", "espn_weight"] == 0.25 and out.loc["b", "espn_weight"] == 0.0


def test_espn_weight_is_constant_or_override(conn, tmp_path, monkeypatch):
    conn.execute("INSERT INTO model_params(key, value, updated_at) VALUES ('projection.espn_weight', 0.7, 'x')")
    assert calib.resolve_espn_weight(conn) == (0.5, "default")  # measuring is retired
    path = tmp_path / "c.toml"
    path.write_text('"projection.espn_weight" = 0.2\n', encoding="utf-8")
    monkeypatch.setattr(constants, "OVERRIDES_PATH", path)
    assert calib.resolve_espn_weight(conn)[0] == 0.2


# ------------------------------------------------------------------ box-score helpers


def _box(conn, pid, day, minutes, pts, season=2027, team="TM", starter=1, event=None):
    vals = _line(minutes=minutes, pts=pts)
    conn.execute(
        f"INSERT INTO box_lines(event_id, espn_id, et_date, season, team, starter, played, {', '.join(STATS)}) "
        f"VALUES (?,?,?,?,?,?,1,{','.join('?' * len(STATS))})",
        (event or f"{pid}{day}", pid, day, season, team, starter, *[vals[s] for s in STATS]),
    )


# ------------------------------------------------------------------ signals


def _pool_table(n=40):
    rows = {f"p{i}": _line(pts=10.0 + i * 0.5, reb=3.0 + (i % 7), ast=2.0 + (i % 5)) | {"gp": 20} for i in range(n)}
    df = pd.DataFrame.from_dict(rows, orient="index")
    df["name"], df["team"], df["position"] = df.index, "TM", "G"
    return df


def test_explain_tags_luck_vs_role():
    expected = pd.Series(_line(fgm=5.0, fga=10.0, minutes=30.0))
    cold = pd.Series(_line(fgm=4.0, fga=10.0, minutes=30.0) | {"gp": 20})
    reasons, ev = sig.explain(cold, expected, [], "")
    assert "fg_cold" in reasons and "minutes_down" not in reasons
    assert sig._buy_sell_confidence("buy_low", reasons) == "high"
    benched = pd.Series(_line(minutes=22.0) | {"gp": 20})
    reasons, _ = sig.explain(benched, expected, [], "")
    assert "minutes_down" in reasons
    assert sig._buy_sell_confidence("buy_low", reasons) == "low"
    hot_role = pd.Series(_line(minutes=36.0) | {"gp": 20})
    reasons, _ = sig.explain(hot_role, expected, ["Star"], "")
    assert {"minutes_up", "teammate_out"} <= set(reasons)
    assert sig._buy_sell_confidence("sell_high", reasons) == "high"  # temporary: teammate out
    reasons, _ = sig.explain(hot_role, expected, [], "")
    assert sig._buy_sell_confidence("sell_high", reasons) == "low"  # role change may stick


def test_buy_sell_flags_large_gaps_only():
    ros = _pool_table()
    values, pool = value_players(ros, ValuationConfig(pool_size=30, min_gp=1))
    actual = ros.copy()
    actual.loc["p35", ["pts", "fgm"]] = [4.0, 2.0]  # slumping good player
    actual.loc["p5", ["pts", "reb", "ast", "fgm"]] = [30.0, 10.0, 8.0, 8.0]  # breakout
    found = {s.espn_id: s for s in sig.buy_sell(actual, ros, ros, pool, ValuationConfig(pool_size=30))}
    assert found["p35"].kind == "buy_low" and found["p35"].direction == "up"
    assert found["p5"].kind == "sell_high"
    assert set(found) == {"p35", "p5"}
    ev = found["p35"].evidence
    assert ev["metric"] == "value" and ev["predicted"] > ev["reference"]


def _role_lines(rows):
    return pd.DataFrame(rows, columns=["event_id", "espn_id", "name", "team", "et_date", "starter", "minutes", "fga"])


def test_role_change_new_starter_and_lost_job():
    today = date(2026, 12, 1)
    rows = []
    for i in range(13):
        day = (today - timedelta(days=13 - i)).isoformat()
        new = i >= 10  # last 3 games
        rows.append((f"e{i}", "bench", "Bench Guy", "TM", day, int(new), 32.0 if new else 15.0, 8))
        rows.append((f"e{i}", "starter", "Old Starter", "TM", day, int(not new), 18.0 if new else 30.0, 8))
    found = {s.espn_id: s for s in sig.role_changes(_role_lines(rows), today)}
    assert found["bench"].direction == "up" and "new_starter" in found["bench"].reasons
    assert found["bench"].confidence == "high" and found["bench"].evidence["reference"] == pytest.approx(15.0)
    assert found["starter"].direction == "down" and "lost_starting_job" in found["starter"].reasons


def test_opportunity_with_without_split_one_signal_per_beneficiary():
    rows = []
    for i in range(12):
        day = f"2026-11-{i + 1:02d}"
        x_plays = i < 6
        if x_plays:
            rows.append((f"e{i}", "x", "Star", "TM", day, 2027, 34.0, 18))
        rows.append((f"e{i}", "y", "Backup", "TM", day, 2027, 26.0 if not x_plays else 16.0, 9))
        rows.append((f"e{i}", "z", "Deep", "TM", day, 2027, 15.0 if not x_plays else 8.0, 4))
        rows.append((f"e{i}", "hurt", "Also Out", "TM", day, 2027, 30.0 if not x_plays else 20.0, 9))
    lines = pd.DataFrame(rows, columns=["event_id", "espn_id", "name", "team", "et_date", "season", "minutes", "fga"])
    ros = pd.DataFrame(
        {"name": ["Star", "Backup", "Deep", "Also Out"], "team": "TM", "position": "G", "minutes": [34, 18, 9, 22]},
        index=["x", "y", "z", "hurt"],
    )
    injured = [{"espn_id": "x", "name": "Star", "team": "TM", "position": "G", "minutes": 34.0}]
    found = sig.opportunities(injured, lines, ros, 2027, {"hurt": "O"})
    assert [s.espn_id for s in found] == ["y"]  # z below the minutes floor, hurt is out himself
    s = found[0]
    assert s.evidence["minutes_without"] == 26.0 and s.evidence["reference"] == 16.0
    assert s.confidence == "medium" and "with_without_split" in s.reasons
    assert s.key == "opportunity:y:up:x"


def test_espn_revision_detects_value_change():
    ros = _pool_table()
    _, pool = value_players(ros, ValuationConfig(pool_size=30, min_gp=1))
    then = ros.assign(snapshot_date="2026-11-01")
    now = then.copy().assign(snapshot_date="2026-11-08")
    now.loc["p20", ["pts", "reb", "fgm", "minutes"]] = [25.0, 9.0, 8.0, 36.0]
    found = sig.espn_revisions(now, then, pool, ValuationConfig(pool_size=30))
    assert [(s.espn_id, s.direction) for s in found] == [("p20", "up")]


def test_action_hints_depend_on_owner():
    assert "trade target" in sig.action_hint("buy_low", "up", "other")
    assert "sell-high" in sig.action_hint("sell_high", "down", "mine")
    assert "pickup" in sig.action_hint("opportunity", "up", "fa")


# ------------------------------------------------------------------ feed: dedup, filter, grading


def _signal(pid="y", conf="medium", kind="role_change", direction="up", **ev):
    return sig.Signal(kind, direction, pid, "Backup", "TM", 5.0, conf, ["x"], "note", {"metric": "minutes", **ev})


def test_record_dedups_and_filters_by_confidence(conn):
    t0 = datetime(2026, 11, 10, 15, tzinfo=UTC)
    assert len(feed.record(conn, 2027, [_signal(reference=16.0)], now=t0)) == 1
    assert feed.record(conn, 2027, [_signal(reference=17.0)], now=t0 + timedelta(hours=20)) == []
    feed.record(conn, 2027, [_signal(pid="low", conf="low", reference=10.0)], now=t0)
    rows = feed.active(conn, now=t0 + timedelta(hours=21))
    assert [r["id"] for r in rows] == ["y"] and rows[0]["evidence"]["reference"] == 17.0
    assert {r["id"] for r in feed.active(conn, now=t0 + timedelta(hours=21), min_confidence="low")} == {"y", "low"}
    assert feed.active(conn, now=t0 + timedelta(days=5)) == []
    # a new episode after the dedup window creates a new signal
    assert len(feed.record(conn, 2027, [_signal(reference=16.0)], now=t0 + timedelta(days=9))) == 1


def test_grading_uses_basis_frozen_at_first_sight(conn):
    t0 = datetime(2026, 11, 1, 15, tzinfo=UTC)
    feed.record(conn, 2027, [_signal("y", reference=16.0)], now=t0)
    feed.record(conn, 2027, [_signal("y", reference=25.0)], now=t0 + timedelta(days=5))  # look-ahead evidence
    for d in range(2, 8):
        _box(conn, "y", f"2026-11-{d:02d}", 22.0, 10.0)
    _, pool = value_players(_pool_table(), ValuationConfig(pool_size=30, min_gp=1))
    (g,) = feed.grade_pending(conn, pool, ValuationConfig(), date(2026, 12, 1))
    assert g["reference"] == 16.0 and g["outcome"] == "hit"


def test_confidence_upgrade_starts_new_episode(conn):
    t0 = datetime(2026, 11, 1, 15, tzinfo=UTC)
    feed.record(conn, 2027, [_signal("y", conf="low", reference=16.0)], now=t0)
    new = feed.record(conn, 2027, [_signal("y", conf="high", reference=18.0)], now=t0 + timedelta(days=2))
    assert len(new) == 1
    rows = feed.active(conn, now=t0 + timedelta(days=2), min_confidence="low")
    assert [(r["confidence"], r["first_seen_et"]) for r in rows] == [("high", "2026-11-03")]
    assert conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0] == 2


def test_grade_signals_by_direction_and_prediction(conn):
    t0 = datetime(2026, 11, 1, 15, tzinfo=UTC)
    feed.record(conn, 2027, [_signal("up", reference=16.0), _signal("flat", reference=16.0)], now=t0)
    for d in range(2, 8):
        _box(conn, "up", f"2026-11-{d:02d}", 26.0, 10.0)
        _box(conn, "flat", f"2026-11-{d:02d}", 17.0, 10.0)
    ros = _pool_table()
    _, pool = value_players(ros, ValuationConfig(pool_size=30, min_gp=1))
    graded = feed.grade_pending(conn, pool, ValuationConfig(), date(2026, 12, 1))
    outcomes = sorted(g["outcome"] for g in graded)
    assert outcomes == ["hit", "miss"]
    summ = feed.summary(conn, 2027)["by_kind"]["role_change"]
    assert summ["hit"] == 1 and summ["miss"] == 1 and summ["hit_rate"] == 0.5
    assert feed.grade_pending(conn, pool, ValuationConfig(), date(2026, 12, 1)) == []
