import numpy as np
import pandas as pd
import pytest

from fbm.analytics.categories import NINE_CAT, parse_category_list
from fbm.analytics.valuation import ValuationConfig, value_players


def make_table() -> pd.DataFrame:
    rows = {
        # id: pts reb ast stl blk tov tpm fgm fga ftm fta
        "big": (20, 12, 2, 0.8, 2.5, 2.0, 0.2, 9.0, 15.0, 2.0, 5.0),  # efficient big, bad FT on volume
        "guard": (25, 4, 8, 1.5, 0.3, 3.5, 3.0, 9.0, 20.0, 6.0, 6.6),
        "wing": (15, 6, 3, 1.2, 0.7, 1.5, 2.2, 5.5, 12.0, 2.0, 2.4),
        "bench": (8, 3, 2, 0.6, 0.3, 1.0, 1.0, 3.0, 7.0, 1.0, 1.3),
        "sniper": (12, 3, 2, 0.7, 0.2, 0.8, 3.5, 4.0, 10.0, 1.8, 2.0),
        "lowvol": (4, 2, 1, 0.3, 0.2, 0.4, 0.2, 2.0, 2.2, 0.5, 0.5),  # 91% FG on tiny volume
    }
    cols = ["pts", "reb", "ast", "stl", "blk", "tov", "tpm", "fgm", "fga", "ftm", "fta"]
    df = pd.DataFrame.from_dict(rows, orient="index", columns=cols)
    df["tpa"] = df["tpm"] * 2.7
    df["minutes"] = 28.0
    df["gp"] = 60
    df["name"] = df.index
    df.index.name = "espn_id"
    return df


CFG = ValuationConfig(pool_size=6, roster_size=13, min_gp=1, min_minutes=0, iterations=1)


def test_counting_z_matches_manual():
    result, stats = value_players(make_table(), CFG)
    pts = make_table()["pts"]
    expected = (pts - pts.mean()) / pts.std(ddof=0)
    assert np.allclose(result.loc[expected.index, "z_pts"], expected)


def test_turnovers_inverted():
    result, _ = value_players(make_table(), CFG)
    assert result.loc["lowvol", "z_tov"] > 0 > result.loc["guard", "z_tov"]


def test_fg_pct_is_volume_weighted():
    result, stats = value_players(make_table(), CFG)
    pool_pct = stats.pool_pct["fg_pct"]
    t = make_table()
    assert pool_pct == pytest.approx(t["fgm"].sum() / t["fga"].sum())
    # 91% on 2.2 FGA is worth less than 60% on 15 FGA.
    assert result.loc["big", "z_fg_pct"] > result.loc["lowvol", "z_fg_pct"] > 0
    # Bad FT shooter on volume hurts more than on low volume.
    assert result.loc["big", "z_ft_pct"] < 0


def test_g_equals_z_without_weekly_variance_and_shrinks_with_it():
    t = make_table()
    result, _ = value_players(t, CFG)
    assert np.allclose(result["g_total"], result["z_total"])

    weeks = []
    rng = np.random.default_rng(0)
    for pid, row in t.iterrows():
        for w in range(6):
            noisy = {
                c: max(0.0, row[c] + rng.normal(0, 0.3 * (row[c] + 0.5)))
                for c in t.columns
                if c in ("pts", "reb", "ast", "stl", "blk", "tov", "tpm", "tpa", "fgm", "fga", "ftm", "fta", "minutes")
            }
            noisy["fgm"] = min(noisy["fgm"], noisy["fga"])
            noisy["ftm"] = min(noisy["ftm"], noisy["fta"])
            weeks.append({"espn_id": pid, "week": f"w{w}", "games": 3, **noisy})
    g_result, stats = value_players(t, CFG, weekly=pd.DataFrame(weeks))
    assert all(stats.tau2[c.key] > 0 for c in NINE_CAT)
    assert (g_result["g_pts"].abs() < g_result["z_pts"].abs() + 1e-12).all()


def test_punts_excluded_from_totals():
    cfg = ValuationConfig(pool_size=6, min_gp=1, min_minutes=0, iterations=1, punts=("ft_pct", "tov"))
    result, _ = value_players(make_table(), cfg)
    cats = [c.key for c in NINE_CAT if c.key not in ("ft_pct", "tov")]
    assert np.allclose(result["z_total"], result[[f"z_{k}" for k in cats]].sum(axis=1))


def test_kappa_and_category_parsing():
    assert ValuationConfig(roster_size=13).kappa == pytest.approx(26 / 25)
    assert [c.key for c in parse_category_list("FT%, to")] == ["ft_pct", "tov"]
    with pytest.raises(ValueError):
        parse_category_list("dunks")
