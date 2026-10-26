import pytest

from fbm.analytics import category_weights as cw
from fbm.gm import policy

P = {k: p.default for k, p in policy.REGISTRY.items()}


def test_snap_and_keys():
    assert cw.snap(1.1) == 1.0 and cw.snap(1.2) == 1.25 and cw.snap(9) == 3.0 and cw.snap(0) == 0.25
    assert cw.cat_key("FT%") == "ft_pct" and cw.cat_key("to") == "tov" and cw.cat_key("blk") == "blk"
    with pytest.raises(ValueError):
        cw.cat_key("XYZ")


def test_season_from_strategy_and_override():
    m = cw.season_multipliers({"punts": ["FT%"], "targets": ["AST", "BLK"], "notes": "soft 3PM"}, P)
    assert "ft_pct" not in m and m["ast"] == 1.25 and m["blk"] == 1.25 and m["tpm"] == 1.0 and len(m) == 8
    m2 = cw.season_multipliers({"punts": ["FT%"], "targets": ["AST"]}, {**P, "policy.season_weights": {"STL": 1.6}})
    assert m2["stl"] == 1.5 and m2["ast"] == 1.0 and "ft_pct" not in m2


def test_week_mapping_and_combine():
    odds = {"reb": 0.5, "ast": 0.1, "stl": 0.9, "blk": 0.72, "tov": 0.5}
    w = cw.week_multipliers(odds, P, punts=["TO"])
    assert w == {"reb": 1.5, "ast": 0.25, "stl": 0.5, "blk": 1.0}
    assert cw.combine({"reb": 1.25, "ast": 1.0, "pts": 1.0}, w) == {"reb": 2.0, "ast": 0.25}
