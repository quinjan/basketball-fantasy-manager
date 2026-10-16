from datetime import date

import pandas as pd
import pytest

from fbm.analytics import players as player_tables
from fbm.store import db

STATS = ("minutes", "pts", "reb", "ast", "stl", "blk", "tov", "tpm", "tpa", "fgm", "fga", "ftm", "fta")


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    yield c
    c.close()


def _avg(conn, season, pid, gp, **over):
    vals = dict.fromkeys(STATS, 1.0) | {"minutes": 30.0, "pts": 15.0, "fga": 10.0, "fgm": 5.0} | over
    conn.execute(
        f"INSERT INTO season_averages(season, espn_id, name, team, position, gp, {', '.join(STATS)}, updated_at) "
        f"VALUES (?,?,?,?,?,?,{','.join('?' * len(STATS))},'x')",
        (season, pid, pid, "TM", "G", gp, *[vals[s] for s in STATS]),
    )


def _lines(conn, pid, n, **stats):
    vals = dict.fromkeys(STATS, 1.0) | {"minutes": 30.0, "pts": 15.0, "fga": 10.0, "fgm": 5.0} | stats
    for i in range(n):
        conn.execute(
            f"INSERT INTO box_lines(event_id, espn_id, et_date, season, team, starter, played, {', '.join(STATS)}) "
            f"VALUES (?,?,?,2027,'TM',1,1,{','.join('?' * len(STATS))})",
            (f"e{pid}{i}", pid, (date(2026, 11, 1) + pd.Timedelta(days=i)).isoformat(), *[vals[s] for s in STATS]),
        )


def test_hot_scorer_blends_per_minute_rate_conservatively(conn):
    _avg(conn, 2026, "hot", 70)
    _avg(conn, 2027, "hot", 10)
    _lines(conn, "hot", 10, pts=24.0)  # same minutes, 0.8 pts/min vs 0.5
    proj = player_tables.projection_table(conn, 2027, 2026)
    r = proj.loc["hot"]
    w = min(0.25, 10 / (10 + 30))
    assert r["form_weight"] == pytest.approx(w)
    assert r["minutes"] == pytest.approx(30.0)
    assert r["pts"] == pytest.approx(((1 - w) * 0.5 + w * 0.8) * 30.0)
    assert 15.0 < r["pts"] < 24.0
    assert r["fgm"] / r["fga"] == pytest.approx(0.5)


def test_players_without_recent_games_unchanged(conn):
    _avg(conn, 2026, "idle", 70, pts=20.0)
    proj = player_tables.projection_table(conn, 2027, 2026)
    assert proj.loc["idle", "pts"] == pytest.approx(20.0)
    assert proj.loc["idle", "form_weight"] == 0.0


def test_form_weight_shrinks_with_few_games(conn):
    _avg(conn, 2026, "few", 70)
    _avg(conn, 2027, "few", 2)
    _lines(conn, "few", 2, pts=30.0)
    proj = player_tables.projection_table(conn, 2027, 2026)
    assert proj.loc["few", "form_weight"] == pytest.approx(2 / 32, abs=1e-3)


def test_recent_form_rates(conn):
    _lines(conn, "a", 3, minutes=20.0, pts=10.0)
    form = player_tables.recent_form(conn, 2027, 10)
    assert form.loc["a", "pts"] == pytest.approx(0.5)
    assert form.loc["a", "form_games"] == 3
