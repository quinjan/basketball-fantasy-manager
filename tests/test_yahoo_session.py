"""Yahoo web-session access: credential chunking, cookie handling, the session transport, and `fbm yahoo check`.

Nothing touches the network or the real OS keyring."""

from datetime import UTC, date, datetime

import httpx
import pytest

from fbm import credentials
from fbm.yahoo import coverage, xmlparse
from fbm.yahoo import session as ws
from fbm.yahoo.client import BASE_URL, YahooAPIError, YahooClient
from tests.test_yahoo_league import SCOREBOARD_XML, STANDINGS_XML, TEAMS_XML, TRANSACTIONS_XML, WAIVERS_XML, roster_xml
from tests.test_yahoo_services import AVAILABLE_XML, ME, PENDING_XML

OK_XML = '<?xml version="1.0"?><fantasy_content><users count="0"/></fantasy_content>'


@pytest.fixture
def vault(monkeypatch):
    """In-memory keyring that enforces the Windows Credential Manager 1280-character limit."""
    store: dict[str, str] = {}

    def put(name: str, value: str) -> None:
        if len(value) > 1280:
            raise OSError("The stub received bad data")
        store[name] = value

    monkeypatch.setattr(credentials, "get", lambda name: store.get(name))
    monkeypatch.setattr(credentials, "put", put)
    monkeypatch.setattr(credentials, "delete", lambda name: store.pop(name, None))
    return store


def _session(n: int = 3, value: str = "v") -> ws.WebSession:
    cookies = [{"name": f"C{i}", "value": value, "domain": ".yahoo.com", "path": "/"} for i in range(n)]
    return ws.WebSession(cookies, "Mozilla/5.0 Test", "2026-10-15T00:00:00+00:00", "browser")


def _client(session: ws.WebSession, handler) -> YahooClient:
    client = YahooClient(session=session)
    client._interval = 0.0
    client._http = httpx.Client(
        base_url=client._http.base_url, headers=client._http.headers, transport=httpx.MockTransport(handler)
    )
    return client


# ------------------------------------------------------------------ credentials


def test_large_secret_round_trips_in_chunks(vault):
    value = "x" * 3500
    credentials.put_large("big", value)
    assert vault["big"] == "chunks:4" and all(len(vault[f"big.{i}"]) <= 1280 for i in range(4))
    assert credentials.get_large("big") == value
    credentials.put_large("big", "short")
    assert credentials.get_large("big") == "short" and "big.1" not in vault
    credentials.delete_large("big")
    assert vault == {}


def test_large_secret_missing_chunk_reads_as_none(vault):
    credentials.put_large("big", "y" * 2500)
    del vault["big.1"]
    assert credentials.get_large("big") is None


# ------------------------------------------------------------------ cookies


def test_cookie_applies_matches_browser_rules():
    assert ws.cookie_applies(".yahoo.com") and ws.cookie_applies(".fantasysports.yahoo.com")
    assert ws.cookie_applies(ws.SESSION_HOST)
    assert not ws.cookie_applies("login.yahoo.com") and not ws.cookie_applies(".login.yahoo.com")
    assert not ws.cookie_applies(".notyahoo.com")


def test_from_cookie_header_parses_devtools_value():
    s = ws.from_cookie_header("Cookie: A1=abc; T=z=x=1&y=2;  Y=v ;junk")
    assert [(c["name"], c["value"]) for c in s.cookies] == [("A1", "abc"), ("T", "z=x=1&y=2"), ("Y", "v")]
    assert s.source == "paste" and s.cookie_header() == "A1=abc; T=z=x=1&y=2; Y=v"
    with pytest.raises(ws.YahooSessionError):
        ws.from_cookie_header("   ")


def test_from_browser_cookies_keeps_only_cookies_sent_to_the_api_host():
    cookies = [
        {"name": "T", "value": "1", "domain": ".yahoo.com", "path": "/"},
        {"name": "AS", "value": "2", "domain": "login.yahoo.com", "path": "/"},
        {"name": "H", "value": "3", "domain": ws.SESSION_HOST, "path": "/"},
    ]
    s = ws.from_browser_cookies(cookies, "UA")
    assert [c["name"] for c in s.cookies] == ["T", "H"] and s.user_agent == "UA" and s.saved_at
    with pytest.raises(ws.YahooSessionError):
        ws.from_browser_cookies([cookies[1]], "UA")


def test_session_saved_in_credential_store_survives_the_size_limit(vault):
    big = _session(n=30, value="q" * 120)
    ws.save(big)
    assert int(vault[credentials.YAHOO_WEB_SESSION].split(":")[1]) > 1
    loaded = ws.load()
    assert loaded is not None and loaded.cookies == big.cookies and loaded.user_agent == "Mozilla/5.0 Test"
    assert loaded.age_days(datetime(2026, 10, 17, tzinfo=UTC)) == pytest.approx(2.0)
    ws.clear()
    assert ws.load() is None and vault == {}


# ------------------------------------------------------------------ client transport


def test_web_session_client_reads_the_read_only_host_with_cookies():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, text=OK_XML)

    with _client(_session(), handler) as client:
        assert client.auth_mode == "web_session"
        client.get("users;use_login=1/games")
    req = seen[0]
    assert str(req.url).startswith(ws.SESSION_BASE_URL)
    assert req.headers["cookie"] == "C0=v; C1=v; C2=v"
    assert "authorization" not in req.headers
    assert req.headers["origin"] == ws.WEB_ORIGIN and req.headers["user-agent"] == "Mozilla/5.0 Test"


def test_web_session_client_never_writes():
    with _client(_session(), lambda r: pytest.fail("no request expected")) as client:
        for call in (client.put_xml, client.post_xml):
            with pytest.raises(YahooAPIError) as err:
                call("team/1/roster", "<x/>")
            assert err.value.status == 405


def test_expired_web_session_says_how_to_fix():
    body = '<?xml version="1.0"?><error><description>Unauthorized access.</description></error>'
    with _client(_session(), lambda r: httpx.Response(401, text=body)) as client:
        with pytest.raises(YahooAPIError) as err:
            client.get("league/1/teams")
    assert err.value.status == 401 and "fbm yahoo login" in err.value.description


def test_rotated_cookies_are_saved_back(monkeypatch):
    saved: list[ws.WebSession] = []
    monkeypatch.setattr(ws, "load", lambda: _session())
    monkeypatch.setattr(ws, "save", saved.append)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=OK_XML, headers={"set-cookie": "C1=fresh; Domain=.yahoo.com; Path=/"})

    client = YahooClient()
    client._interval = 0.0
    client._http = httpx.Client(base_url=client._http.base_url, transport=httpx.MockTransport(handler))
    client.get("users;use_login=1/games")
    client.close()
    assert len(saved) == 1 and {c["name"]: c["value"] for c in saved[0].cookies}["C1"] == "fresh"


def test_without_a_session_the_official_api_is_used(monkeypatch):
    monkeypatch.setattr(ws, "load", lambda: None)
    client = YahooClient()
    assert client.auth_mode == "oauth" and str(client._http.base_url) == BASE_URL
    client.close()


# ------------------------------------------------------------------ fbm yahoo check


class PathClient:
    """Answers by the first matching path fragment; anything else fails like Yahoo would."""

    def __init__(self, routes: list[tuple[str, str]]):
        self.routes = routes

    def get(self, path: str):
        for fragment, xml in self.routes:
            if fragment in path:
                return xmlparse.parse(xml)
        raise YahooAPIError(404, "not in fixture", path)


def test_check_reports_every_feed_and_its_failures():
    day = date(2026, 10, 28)
    client = PathClient(
        [
            ("roster;date=", roster_xml(ME, [("10", "Shai Gilgeous-Alexander", "OKC", "", ["PG", "Util"], "PG")])),
            ("/scoreboard", SCOREBOARD_XML),
            ("/standings", STANDINGS_XML),
            ("status=W", WAIVERS_XML),
            ("status=A", AVAILABLE_XML),
            ("types=waiver,pending_trade", PENDING_XML),
            ("/transactions;", TRANSACTIONS_XML),
            ("league/466.l.1/teams", TEAMS_XML),
        ]
    )
    results = {r.name: r for r in coverage.run(client, "466.l.1", ME, day)}
    assert len(results) == len(coverage.CHECKS)
    for name in ("Teams", "Your roster (today)", "Scoreboard", "Standings", "Waiver list", "Transactions"):
        assert results[name].ok, results[name].detail
    assert results["Pending trades & claims"].detail == "2 pending trade(s), 1 pending waiver claim(s)"
    assert "1 players on 2026-10-28" in results["Your roster (today)"].detail
    assert not results["League settings"].ok and "not in fixture" in results["League settings"].detail
    assert results["Matchup history"].kind == coverage.EXTRA and not results["Matchup history"].ok


def test_check_without_a_league_only_runs_discovery():
    results = coverage.run(PathClient([("users;use_login=1", OK_XML)]), None, None, date(2026, 10, 28))
    assert results[0].ok and results[0].detail == "0 NBA leagues this season"
    assert all(not r.ok and r.detail.startswith("skipped") for r in results[1:])
