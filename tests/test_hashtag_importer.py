"""HT daily import end to end against a fake Hashtag server serving sanitized fixtures (no network)."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from fbm.hashtag import VIEWS, health, importer, store, store_extra
from fbm.hashtag import ids as ht_ids
from fbm.hashtag import session as ht_session
from fbm.hashtag.client import HashtagClient
from fbm.league.model import FantasyTeam, LeagueState, RosterPlayer
from fbm.store import db

FIX = Path(__file__).parent / "fixtures" / "hashtag"
NOW = datetime(2026, 10, 27, 16, 0, tzinfo=UTC)  # Tuesday ET
PLANNER_ROSTER = re.findall(r'HFpid_\d+" value="(\d+)"', (FIX / "h2h_planner.html").read_text(encoding="utf-8"))


def fixture(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


def apply_form(html: str, form: dict[str, list[str]]) -> str:
    """Echo a postback into the page the way ASP.NET would re-render it: selects, checkboxes, radios."""
    if not form:
        return html
    for name, vals in form.items():
        if name.startswith("__"):
            continue
        m = re.search(rf'(<select name="{re.escape(name)}"[^>]*>)(.*?)(</select>)', html, re.S)
        if m:
            body = m.group(2).replace(' selected="selected"', "")
            body = body.replace(f'<option value="{vals[0]}"', f'<option selected="selected" value="{vals[0]}"', 1)
            html = html[: m.start(2)] + body + html[m.end(2) :]

    def box(mm: re.Match) -> str:
        tag = mm.group(0)
        name = re.search(r'name="([^"]+)"', tag)
        if not name:
            return tag
        value = re.search(r'value="([^"]*)"', tag)
        tag = tag.replace(' checked="checked"', "")
        posted = form.get(name.group(1))
        if posted is not None and ('type="checkbox"' in tag or (value and value.group(1) in posted)):
            tag = tag.replace("<input ", '<input checked="checked" ', 1)
        return tag

    return re.sub(r'<input[^>]*type="(?:checkbox|radio)"[^>]*>', box, html)


class FakeHT:
    def __init__(self, expired_on: str | None = None):
        self.requests: list[tuple[str, str, dict]] = []
        self.expired_on = expired_on

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = urlparse(str(request.url))
        form = parse_qs(request.content.decode(), keep_blank_values=True) if request.method == "POST" else {}
        self.requests.append((request.method, url.path, form))
        path = url.path
        if self.expired_on and self.expired_on in path:
            return httpx.Response(302, headers={"location": "/premium/login?ReturnUrl=" + path})
        if path.startswith("/premium/login"):
            return httpx.Response(200, text=fixture("anon_login.html"))
        if path == "/premium/":
            return httpx.Response(200, text=fixture("premium_home.html"))
        if "refresh-yahoo" in path:
            return httpx.Response(302, headers={"location": "/premium/", "set-cookie": "ASP.NET_SessionId=rot; path=/"})
        page = {
            "/fantasy-basketball-projections": "projections_ros.html",
            "/fantasy-basketball-rankings": "season_rankings.html",
            "/import-v4/plan-cat-yahoo.aspx": "h2h_planner.html",
            "/import-v4/waiver-cat-yahoo.aspx": "waiver_wire.html",
            "/import-v4/scouting-cat-yahoo.aspx": "scouting_report.html",
            "/import-v4/trade-finder-cat-yahoo.aspx": "trade_finder_default.html",
            "/import-v4/notebook": "notebook.html",
            "/next-man-up": "next_man_up.html",
        }.get(path)
        if page is None:
            return httpx.Response(404, text="not found")
        if page == "projections_ros.html" and form.get("ctl00$ContentPlaceHolder1$DDDURATION") == ["777"]:
            page = "projections_stream7.html"
        if page == "trade_finder_default.html" and "ctl00$ContentPlaceHolder1$BtnFind" in form:
            page = "trade_finder_results.html"
        if page == "next_man_up.html" and form.get("ctl00$ContentPlaceHolder1$DDPLAYER") == ["120223"]:
            page = "next_man_up_with_data.html"
            form = {}
        return httpx.Response(200, text=apply_form(fixture(page), form))


def make_client(fake: FakeHT) -> HashtagClient:
    sess = ht_session.HTSession([{"name": ".ASPXAUTH", "value": "x", "domain": "hashtagbasketball.com"}])
    return HashtagClient(sess, transport=httpx.MockTransport(fake), min_interval=0, save_rotated=False)


def make_league(drop_one: bool = False) -> LeagueState:
    roster = [RosterPlayer(f"e{y}", f"Player {y}", "XXX", ("UTIL",), yahoo_key=f"466.p.{y}") for y in PLANNER_ROSTER]
    mine = roster[1:] if drop_one else roster
    names = ["My Team", "Opponent A"] + [f"Team {c}" for c in "BCDEFGHIJKLM"]
    teams = [FantasyTeam(f"t.{i}", n, players=list(mine if i == 0 else roster if i == 1 else [])) for i, n in
             enumerate(names)]  # fmt: skip
    return LeagueState("466.l.11111", "Test League", 2026, "t.0", teams, week=2, opponent_key="t.1")


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setattr(ht_ids, "OVERRIDES_PATH", tmp_path / "none.toml")
    c = db.connect(tmp_path / "fbm.db")
    c.executemany(
        "INSERT INTO players (espn_id, name, team, updated_at) VALUES (?, ?, ?, '2026-10-01')",
        [("3112335", "Nikola Jokic", "DEN"), ("3059318", "Victor Wembanyama", "SA"), ("4433255", "Asa Newell", "ATL"),
         ("3945274", "Luka Doncic", "LAL")],
    )  # fmt: skip
    c.commit()
    yield c
    c.close()


def test_full_import_writes_tables_and_becomes_healthy(conn):
    fake = FakeHT()
    w = {"fg_pct": 1, "ft_pct": 1, "tpm": 0.5, "pts": 1, "reb": 1, "ast": 1.25, "stl": 1, "blk": 1, "tov": 0.25}
    report = importer.run_import(
        conn, league=make_league(), client=make_client(fake), now=NOW, league_id="11111", weights=w
    )
    statuses = {k: v["status"] for k, v in report["pages"].items()}
    assert all(s == "ok" for s in statuses.values()), report["pages"]
    assert {f"projections:{v}" for v in VIEWS} <= set(statuses)
    assert "h2h_planner:STREAM7" in statuses and "h2h_planner:ROS" not in statuses  # Tuesday: policy view only
    assert report["healthy"] is True

    ros = store.projections(conn, "ROS")
    assert ros.loc["3112335", "name"] == "Nikola Jokic" and ros.loc["3112335", "z_tov"] < 0
    assert store.projections(conn, "STREAM7").attrs["view"] == "STREAM7"
    mt = store.matchup_totals(conn, week=2)
    assert mt["opponent_key"] == "t.1" and mt["view"] == "STREAM7"
    assert mt["categories"]["fg_pct"]["me_after"] == pytest.approx(0.508)
    w = store.waiver(conn, view="STREAM7")
    assert len(w) == 7 and w["data"][0]["pts"] is not None
    assert len(store.team_ranks(conn, "ROS")) == 14 * 10
    assert len(store.notes(conn)) == 4
    assert not store_extra.season_stats(conn).empty
    log = conn.execute("SELECT COUNT(DISTINCT run_id) FROM hashtag_import_log").fetchone()[0]
    assert log == 1
    # read-only: never pressed anything that saves; UPDATE SCOREBOARD was posted once
    targets = [f.get("__EVENTTARGET", [""])[0] for _, _, f in fake.requests]
    assert targets.count("ctl00$ContentPlaceHolder1$Button101") == 1
    assert not any(re.search("save|lnkAdd", t, re.I) for t in targets)
    st = health.status(conn, enabled=True, now=NOW, check_session=False)
    assert st.healthy, st.reasons


def test_monday_pulls_every_planner_view(conn):
    monday = datetime(2026, 10, 26, 16, 0, tzinfo=UTC)
    report = importer.run_import(
        conn, league=make_league(), client=make_client(FakeHT()), now=monday, league_id="11111",
        pages=["h2h_planner"],
    )  # fmt: skip
    assert {f"h2h_planner:{v}" for v in VIEWS} == set(report["pages"])


def test_league_mismatch_is_logged_and_unhealthy(conn):
    report = importer.run_import(
        conn, league=make_league(drop_one=True), client=make_client(FakeHT()), now=NOW, league_id="11111",
        pages=["h2h_planner", "league_sync"],
    )  # fmt: skip
    assert report["pages"]["league_sync"]["status"] == "league_mismatch"
    assert "on HT not Yahoo" in report["pages"]["league_sync"]["detail"]
    assert report["healthy"] is False


def test_session_expiry_stops_the_run_without_partial_data(conn):
    report = importer.run_import(
        conn, league=make_league(), client=make_client(FakeHT(expired_on="projections")), now=NOW, league_id="11111"
    )
    assert list(report["pages"]) == ["projections:ROS"]
    assert report["pages"]["projections:ROS"]["status"] == "session_expired"
    assert conn.execute("SELECT COUNT(*) FROM hashtag_projections").fetchone()[0] == 0
    st = health.status(conn, enabled=True, now=NOW, check_session=False)
    assert not st.healthy and any("session_expired" in r for r in st.reasons)


def test_layout_change_writes_nothing(conn, monkeypatch):
    broken = fixture("scouting_report.html").replace(">TOTAL<", ">SUM<")
    real = FakeHT()

    def handler(request: httpx.Request) -> httpx.Response:
        if "scouting" in request.url.path:
            return httpx.Response(200, text=broken)
        return real(request)

    sess = ht_session.HTSession([{"name": "a", "value": "b", "domain": "hashtagbasketball.com"}])
    client = HashtagClient(sess, transport=httpx.MockTransport(handler), min_interval=0, save_rotated=False)
    report = importer.run_import(conn, league=make_league(), client=client, now=NOW, league_id="11111",
                                 pages=["team_ranks"])  # fmt: skip
    assert report["pages"]["team_ranks:ROS"]["status"] == "layout_changed"
    assert conn.execute("SELECT COUNT(*) FROM hashtag_team_ranks").fetchone()[0] == 0


def test_trade_finder_and_next_man_up(conn):
    fake = FakeHT()
    client = make_client(fake)
    ideas = importer.run_trade_finder(
        conn, weights={"ast": 1.25, "stl": 1, "blk": 1, "fg_pct": 1, "ft_pct": 1, "tpm": 1, "pts": 1, "reb": 1,
                       "tov": 0.25},
        client=client, league=make_league(), now=NOW, league_id="11111",
    )  # fmt: skip
    assert len(ideas) == 2 and ideas[0]["give"] and ideas[0]["get"]
    assert store.trade_ideas(conn)[0]["partner_name"].startswith("Team")
    posted = [f for m, p, f in fake.requests if m == "POST" and "trade-finder" in p][0]
    assert posted["ctl00$ContentPlaceHolder1$DDAST"] == ["1.25"]
    assert posted["ctl00$ContentPlaceHolder1$BtnFind"] == ["FIND TRADES"]

    conn.execute(
        "INSERT INTO hashtag_players (ht_id, espn_id, name, team, match_method, updated_at) "
        "VALUES ('120223', '4433255', 'Asa Newell', 'ATL', 'exact', '2026-10-27')"
    )
    conn.commit()
    n = importer.run_next_man_up(conn, ["4433255"], client=client, now=NOW)
    assert n == 7
    rows = store.next_man_up(conn, "4433255")
    assert rows and rows[0]["min_without"] is not None and "stats" in rows[0]["data"]


def test_no_session_logs_session_expired(conn, monkeypatch):
    monkeypatch.setattr(ht_session, "load", lambda: None)
    report = importer.run_import(conn, league=make_league(), now=NOW, league_id="11111", pages=["projections"])
    assert report["pages"]["projections"]["status"] == "session_expired"


def test_health_check_and_status_service(conn):
    from fbm.gm import hashtag_services
    from fbm.gm import health as gm_health

    assert gm_health.check_hashtag(None)[0]["status"] == "OK"
    assert gm_health.check_hashtag({"enabled": False})[0]["status"] == "OK"
    bad = health.status(conn, enabled=True, now=NOW, check_session=False).as_dict()
    failed = gm_health.check_hashtag(bad)[0]
    assert failed["status"] == "FAIL" and "fbm hashtag import" in failed["fix"]
    importer.run_import(conn, league=make_league(), client=make_client(FakeHT()), now=NOW, league_id="11111")
    good = health.status(conn, enabled=True, now=NOW, check_session=False).as_dict()
    assert gm_health.check_hashtag(good)[0]["status"] == "OK"
    r = hashtag_services.hashtag_status(conn, enabled=True)
    assert {row["page"] for row in r["last_imports"]} >= {"projections", "h2h_planner", "league_sync", "waiver"}
    assert "cookies" not in str(r["session"].get("value", ""))
