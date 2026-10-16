import numpy as np
import pandas as pd
import pytest

from fbm.analytics import draft
from fbm.analytics.categories import NINE_CAT
from fbm.data.espn_fantasy import eligibility_from_slots, parse_players

KEYS = [c.key for c in NINE_CAT]


def values(n=60, seed=0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows = {}
    for i in range(n):
        base = 3.0 - i * 0.1
        rows[f"p{i}"] = {
            "name": f"Player {i}",
            "team": "TM",
            "position": "C" if i % 5 == 0 else "G",
            **{f"g_{k}": base / 9 + rng.normal(0, 0.2) for k in KEYS},
        }
    df = pd.DataFrame(rows).T
    for k in KEYS:
        df[f"g_{k}"] = df[f"g_{k}"].astype(float)
    df["g_total"] = df[[f"g_{k}" for k in KEYS]].sum(axis=1)
    return df


def test_snake_order():
    assert [draft.team_on_clock(n, 12) for n in (1, 12, 13, 24, 25)] == [1, 12, 12, 1, 1]
    assert draft.my_pick_numbers(5, 12, 3) == [5, 20, 29]


def test_board_tiers_adp_and_punts():
    v = values()
    board = draft.build_board(v, punts=("tov",), adp={"p0": 2.0, "p1": 1.0})
    assert board.iloc[0]["rank"] == 1 and list(board["tier"]) == sorted(board["tier"])
    assert board.at["p0", "value"] == pytest.approx(v.loc["p0", [f"g_{k}" for k in KEYS if k != "tov"]].sum())
    assert board.at["p0", "eligible"] == ("C", "UTIL")
    assert np.isnan(board.at["p5", "adp"])


def test_state_record_undo_and_roundtrip(tmp_path):
    s = draft.DraftState(teams=4, my_slot=2, rounds=2, punts=["ft_pct"])
    s.record("p0", "Player 0")
    assert s.on_clock() == 2 and s.next_overall == 2
    with pytest.raises(ValueError):
        s.record("p0", "Player 0")
    s.record("p1", "Player 1")
    assert [p.team_slot for p in s.picks] == [1, 2] and s.my_next_picks() == [7]
    path = tmp_path / "d.json"
    s.save(path)
    loaded = draft.DraftState.load(path)
    assert loaded.picks[1].pid == "p1" and loaded.punts == ["ft_pct"]
    assert loaded.undo().pid == "p1" and loaded.next_overall == 2


def test_recommend_excludes_taken_and_flags_availability():
    board = draft.build_board(values(), adp={f"p{i}": float(i + 1) for i in range(60)})
    s = draft.DraftState(teams=4, my_slot=2, rounds=3)
    s.record("p0", "Player 0")
    rec = draft.recommend(s, board)
    assert rec["is_my_pick"] and rec["on_clock"] == 2
    ids = [r["id"] for r in rec["recommendations"]]
    assert "p0" not in ids and len(ids) == 8
    assert rec["my_next_picks"] == [2, 7, 10]
    assert all(0 <= r["p_available_next_pick"] <= 1 for r in rec["recommendations"])
    assert len(rec["projected_best_at_my_following_pick"]) == 5


def test_position_need_bonus_late():
    board = draft.build_board(values())
    s = draft.DraftState(teams=2, my_slot=1, rounds=3)
    for pid in ("p1", "p2", "p3", "p4"):  # all guards, no C
        s.record(pid, pid)
    rec = draft.recommend(s, board)
    assert "C" in rec["position_needs"]
    assert "C" in rec["recommendations"][0]["positions"]


def test_mock_draft_completes():
    board = draft.build_board(values(), adp={f"p{i}": float(i + 1) for i in range(60)})
    s = draft.mock_draft(board, draft.DraftState(teams=4, my_slot=3, rounds=5), seed=1)
    assert s.done and len(s.picks) == 20 and len(s.my_picks()) == 5
    rosters = draft.rosters_from_state(s)
    assert sorted(len(v) for v in rosters.values()) == [5, 5, 5, 5]


def test_espn_fantasy_parsing():
    assert eligibility_from_slots([4, 9, 10, 11, 12, 13]) == ("C", "UTIL")
    assert eligibility_from_slots([0, 1, 5, 11]) == ("PG", "SG", "G", "UTIL")
    payload = {
        "players": [
            {
                "id": 3112335,
                "player": {
                    "fullName": "Nikola Jokic",
                    "eligibleSlots": [4, 9, 11],
                    "ownership": {"averageDraftPosition": 1.42},
                    "draftRanksByRankType": {"STANDARD": {"rank": 1}, "ROTO": {"rank": 1}},
                },
            }
        ]
    }
    (m,) = parse_players(payload)
    assert (m.espn_id, m.adp, m.rank_roto, m.eligible) == ("3112335", 1.42, 1, ("C", "UTIL"))
