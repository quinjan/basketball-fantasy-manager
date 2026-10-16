from types import SimpleNamespace

import pandas as pd
import pytest

from fbm.analytics import draft as adraft
from fbm.gm import services as svc
from fbm.store import db
from fbm.yahoo import xmlparse
from fbm.yahoo.draft import (
    apply_results,
    draft_params,
    fetch_adp,
    parse_draft_analysis,
    parse_draftresults,
    save_adp,
    yahoo_adp_map,
)
from fbm.yahoo.ids import EspnPlayer, IdMapper

NS = 'xmlns="http://fantasysports.yahooapis.com/fantasy/v2/base.rng"'
T1, T2 = "466.l.1.t.1", "466.l.1.t.2"


def _player(pid: str, name: str, team: str) -> str:
    return (
        f"<player><player_key>466.p.{pid}</player_key><player_id>{pid}</player_id>"
        f"<name><full>{name}</full></name><editorial_team_abbr>{team}</editorial_team_abbr></player>"
    )


def _result(pick: int, rnd: int, team: str, pid: str | None = None, name: str = "", nba: str = "") -> str:
    inner = f"<player_key>466.p.{pid}</player_key>{_player(pid, name, nba)}" if pid else ""
    return f"<draft_result><pick>{pick}</pick><round>{rnd}</round><team_key>{team}</team_key>{inner}</draft_result>"


def draftresults_xml(n_picked: int) -> str:
    # 2 teams, 2 rounds, snake: T1, T2, T2, T1
    order = [(1, 1, T1), (2, 1, T2), (3, 2, T2), (4, 2, T1)]
    players = [
        ("1", "Nikola Jokic", "Den"),
        ("2", "Luka Doncic", "LAL"),
        ("3", "Mystery Rookie", "SA"),
        ("4", "Trae Young", "Atl"),
    ]
    rows = "".join(
        _result(p, r, t, *(players[i] if i < n_picked else (None,)))  # type: ignore[arg-type]
        for i, (p, r, t) in enumerate(order)
    )
    return (
        f'<?xml version="1.0"?><fantasy_content {NS}><league><league_key>466.l.1</league_key>'
        f'<draft_results count="4">{rows}</draft_results></league></fantasy_content>'
    )


def analysis_xml(rows: list[tuple[str, str, str, str]]) -> str:
    body = "".join(
        f"<player><player_key>466.p.{pid}</player_key><player_id>{pid}</player_id><name><full>{name}</full></name>"
        f"<editorial_team_abbr>{team}</editorial_team_abbr><draft_analysis><average_pick>{ap}</average_pick>"
        f"<average_round>1.2</average_round><average_cost>-</average_cost><percent_drafted>1.00</percent_drafted>"
        f"</draft_analysis></player>"
        for pid, name, team, ap in rows
    )
    return (
        f'<?xml version="1.0"?><fantasy_content {NS}><league><league_key>466.l.1</league_key>'
        f'<players count="{len(rows)}">{body}</players></league></fantasy_content>'
    )


MAPPER = IdMapper(
    [
        EspnPlayer("e1", "Nikola Jokić", "DEN"),
        EspnPlayer("e2", "Luka Dončić", "LAL"),
        EspnPlayer("e4", "Trae Young", "ATL"),
    ]
)


def test_parse_draftresults_handles_unpicked_slots():
    res = parse_draftresults(xmlparse.parse(draftresults_xml(2)))
    assert [r.pick for r in res] == [1, 2, 3, 4]
    assert res[0].player_id == "1" and res[0].name == "Nikola Jokic" and res[0].team_key == T1
    assert res[2].player_key == "" and res[2].player_id == ""
    assert draft_params(res, T2) == (2, 2, 2)


def test_parse_draft_analysis_and_mapping(tmp_path):
    rows = parse_draft_analysis(
        xmlparse.parse(analysis_xml([("1", "Nikola Jokic", "Den", "1.4"), ("9", "Nobody", "SA", "40.2")]))
    )
    assert rows[0].average_pick == 1.4 and rows[0].average_cost is None and rows[0].percent_drafted == 100.0
    conn = db.connect(tmp_path / "t.db")
    stats = save_adp(conn, rows, MAPPER)
    assert stats == {"players": 2, "mapped": 1, "unmapped": 1}
    assert yahoo_adp_map(conn) == {"e1": 1.4}


def test_fetch_adp_pages_until_short_batch():
    paths = []

    class Client:
        def get(self, path):
            paths.append(path)
            n = 2 if "start=0" in path else 1
            return xmlparse.parse(analysis_xml([(str(i), f"P{i}", "SA", str(i)) for i in range(n)]))

    out = fetch_adp(Client(), "466.l.1", limit=10, page=2)
    assert len(out) == 3
    assert paths[0] == "league/466.l.1/players;sort=AR;start=0;count=2/draft_analysis"
    assert "start=2" in paths[1]


def _resolve(r):
    m = MAPPER.match(r.player_id, r.name, r.nba_team)
    return (m.espn_id, r.name) if m.espn_id else (f"yahoo:{r.player_id}", r.name)


def test_apply_results_is_idempotent_and_keeps_order():
    state = adraft.DraftState(teams=2, my_slot=2, rounds=2)
    new = apply_results(state, parse_draftresults(xmlparse.parse(draftresults_xml(2))), _resolve, T2)
    assert [p["id"] for p in new] == ["e1", "e2"] and [p["mine"] for p in new] == [False, True]
    assert apply_results(state, parse_draftresults(xmlparse.parse(draftresults_xml(2))), _resolve, T2) == []
    new = apply_results(state, parse_draftresults(xmlparse.parse(draftresults_xml(4))), _resolve, T2)
    assert [p["id"] for p in new] == ["yahoo:3", "e4"] and not new[0]["mapped"]
    assert state.done and [p.team_slot for p in state.picks] == [1, 2, 2, 1]


@pytest.fixture
def live(tmp_path, monkeypatch):
    path = tmp_path / "draft_state.json"
    monkeypatch.setattr(adraft.DraftState.load.__func__, "__defaults__", (path,))
    monkeypatch.setattr(adraft.DraftState.save, "__defaults__", (path,))
    xml = {"n": 1}

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return None

        def get(self, p):
            assert p == "league/466.l.1/draftresults/players"
            return xmlparse.parse(draftresults_xml(xml["n"]))

    cfg = SimpleNamespace(league_key="466.l.1", team_key=T2)
    monkeypatch.setattr(svc, "_yahoo_draft_client", lambda: (cfg, MAPPER, Client()))
    names = pd.Series({"e1": "Nikola Jokic", "e2": "Luka Doncic", "e4": "Trae Young"})
    monkeypatch.setattr(svc, "ctx", lambda: SimpleNamespace(projections=pd.DataFrame({"name": names})))
    monkeypatch.setattr(svc, "draft_recommend", lambda top=8: {"stub": True})
    return path, xml


def test_draft_sync_yahoo_auto_starts_and_is_idempotent(live):
    path, xml = live
    r = svc.draft_sync_yahoo()
    assert [p["player"] for p in r["new_picks"]] == ["Nikola Jokic"]
    assert r["my_turn"] and r["recommendations"] == {"stub": True} and path.exists()
    assert svc.draft_sync_yahoo()["new_picks"] == []
    xml["n"] = 3
    r = svc.draft_sync_yahoo()
    assert [p["overall"] for p in r["new_picks"]] == [2, 3]
    assert not r["my_turn"] and "recommendations" not in r
    assert adraft.DraftState.load(path).picks[2].pid == "yahoo:3"


def test_draft_sync_yahoo_without_access_raises_clear_error(monkeypatch):
    def boom():
        raise svc.GMError(svc._YAHOO_PENDING.format(err="403"))

    monkeypatch.setattr(svc, "_yahoo_draft_client", boom)
    with pytest.raises(svc.GMError, match="pending"):
        svc.draft_sync_yahoo()
    monkeypatch.setattr(svc, "_draft_adp", lambda: ({}, "espn"))
    r = svc.draft_adp_refresh()
    assert r["refreshed"] is False and r["adp_source"] == "espn" and "pending" in r["message"]


def test_adp_source_prefers_yahoo_when_cached(tmp_path, monkeypatch):
    conn = db.connect(tmp_path / "t.db")
    monkeypatch.setattr(svc, "_conn", lambda: conn)
    monkeypatch.setattr(svc, "ctx", lambda: SimpleNamespace(season=2027))
    conn.execute(
        "INSERT INTO player_meta(espn_id, season, adp, eligible, updated_at) VALUES ('e1', 2027, 3.0, '[]', 'x')"
    )
    real_c = svc.C
    monkeypatch.setattr(svc, "C", lambda k: 2 if k == "draft.yahoo_adp_min_players" else real_c(k))
    assert svc._draft_adp() == ({"e1": 3.0}, "espn")
    rows = parse_draft_analysis(
        xmlparse.parse(analysis_xml([("1", "Nikola Jokic", "Den", "1.5"), ("2", "Luka Doncic", "LAL", "2.5")]))
    )
    save_adp(conn, rows, MAPPER)
    assert svc._draft_adp() == ({"e1": 1.5, "e2": 2.5}, "yahoo")
