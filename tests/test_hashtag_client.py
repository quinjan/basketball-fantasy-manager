"""HT client: postback form building, read-only guard, cookie rotation and session-expiry detection."""

from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest

from fbm import credentials
from fbm.hashtag import session as ht_session
from fbm.hashtag.client import HashtagClient, Page, build_postback, form_fields, postback_targets
from fbm.hashtag.dom import parse_html
from fbm.hashtag.errors import HashtagError, HashtagSessionError

FIX = Path(__file__).parent / "fixtures" / "hashtag"


def fx(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


def _session() -> ht_session.HTSession:
    return ht_session.HTSession([{"name": ".ASPXAUTH", "value": "secret", "domain": "hashtagbasketball.com"}])


def _client(handler) -> HashtagClient:
    return HashtagClient(_session(), transport=httpx.MockTransport(handler), min_interval=0, save_rotated=False)


def test_form_fields_hidden_checked_and_selected():
    doc = parse_html(fx("waiver_wire.html"))
    data = dict(form_fields(doc))
    assert data["__VIEWSTATE"] == "dummyviewstate" and "__EVENTVALIDATION" in data
    assert data["ctl00$ContentPlaceHolder1$DDAST"] == "1.25"
    assert data["ctl00$ContentPlaceHolder1$CBAST"] == "on"
    assert "ctl00$ContentPlaceHolder1$CBFGM" not in data  # unticked
    assert "ctl00$ContentPlaceHolder1$DDPTS" in data


def test_build_postback_overrides_event_target_and_button():
    doc = parse_html(fx("trade_finder_default.html"))
    data = build_postback(doc, fields={"DDAST": "2.00", "CBTO": None}, button="BtnFind")
    d = {}
    for k, v in data:
        d.setdefault(k, []).append(v)
    assert d["ctl00$ContentPlaceHolder1$DDAST"] == ["2.00"]
    assert "ctl00$ContentPlaceHolder1$CBTO" not in d
    assert d["ctl00$ContentPlaceHolder1$BtnFind"] == ["FIND TRADES"]
    assert d["__EVENTTARGET"] == [""]
    data = dict(build_postback(doc, fields={"DDFAIR": "fair"}, event_target="DDFAIR"))
    assert data["__EVENTTARGET"] == "ctl00$ContentPlaceHolder1$DDFAIR"


def test_postback_targets_find_link_buttons():
    doc = parse_html(fx("h2h_planner.html"))
    targets = postback_targets(doc)
    assert targets["UPDATE SCOREBOARD"] == ("event", "ctl00$ContentPlaceHolder1$Button101")
    assert targets["UPDATE ROSTER"][0] == "button"


def test_refuses_save_or_logout_postbacks():
    doc = parse_html(fx("h2h_planner.html"))
    with pytest.raises(HashtagError):
        build_postback(doc, event_target="ctl00$LoginStatus2$ctl00")
    with pytest.raises(HashtagError):
        build_postback(doc, event_target="ctl00$ContentPlaceHolder1$RECREP$ctl01$lnkAdd")


def test_postback_posts_to_form_action_and_absorbs_rotated_cookie():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["cookie"] = request.headers.get("cookie")
        seen["form"] = parse_qs(request.content.decode())
        return httpx.Response(200, text=fx("scouting_report.html"),
                              headers={"set-cookie": ".ASPXAUTH=rotated; path=/; HttpOnly"})  # fmt: skip

    client = _client(handler)
    page = Page("https://hashtagbasketball.com/import-v4/scouting-cat-yahoo.aspx?lid=11111", fx("scouting_report.html"))
    client.postback(page, fields={"DDDURATION": "777"}, event_target="DDDURATION")
    assert seen["url"] == "https://hashtagbasketball.com/import-v4/scouting-cat-yahoo.aspx?lid=11111"
    assert seen["cookie"] == ".ASPXAUTH=secret"
    assert seen["form"]["ctl00$ContentPlaceHolder1$DDDURATION"] == ["777"]
    assert client.session.changed and client.session.cookies[0]["value"] == "rotated"


def test_redirect_to_login_is_session_expired():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/premium/login"):
            return httpx.Response(200, text=fx("anon_login.html"))
        return httpx.Response(302, headers={"location": "/premium/login?ReturnUrl=%2fimport-v4%2fplan"})

    with pytest.raises(HashtagSessionError):
        _client(handler).get("/import-v4/plan-cat-yahoo.aspx?lid=11111")


def test_login_form_page_is_session_expired():
    with pytest.raises(HashtagSessionError):
        _client(lambda r: httpx.Response(200, text=fx("anon_login.html"))).get("/fantasy-basketball-projections")


def test_select_skips_request_when_already_selected():
    calls = []
    client = _client(lambda r: calls.append(r) or httpx.Response(200, text=fx("scouting_report.html")))
    page = Page("https://hashtagbasketball.com/import-v4/scouting-cat-yahoo.aspx?lid=1", fx("scouting_report.html"))
    assert client.select(page, "DDDURATION", value="0") is page
    assert calls == []


def test_session_storage_roundtrip(monkeypatch):
    store = {}
    monkeypatch.setattr(credentials, "put_large", lambda n, v: store.__setitem__(n, v))
    monkeypatch.setattr(credentials, "get_large", lambda n: store.get(n))
    monkeypatch.setattr(credentials, "delete_large", lambda n: store.pop(n, None))
    s = ht_session.from_cookie_header("Cookie: .ASPXAUTH=abc; ASP.NET_SessionId=def")
    assert [c["name"] for c in s.cookies] == [".ASPXAUTH", "ASP.NET_SessionId"] and s.source == "paste"
    ht_session.save(s)
    assert ht_session.load().cookie_header() == ".ASPXAUTH=abc; ASP.NET_SessionId=def"
    ht_session.clear()
    assert ht_session.load() is None
    with pytest.raises(HashtagSessionError):
        ht_session.from_cookie_header("   ")


def test_browser_cookies_filtered_to_hashtag():
    s = ht_session.from_browser_cookies(
        [{"name": "a", "value": "1", "domain": ".hashtagbasketball.com"}, {"name": "b", "value": "2",
                                                                            "domain": ".google.com"}]
    )  # fmt: skip
    assert [c["name"] for c in s.cookies] == ["a"]
    assert ht_session.is_signed_in(fx("notebook.html")) and not ht_session.is_signed_in(fx("anon_login.html"))
