from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from fbm.timeutil import nba_today
from fbm.yahoo.auth import YahooAuthError, authorize_url, extract_code


def test_extract_code_from_url():
    assert extract_code("https://localhost:8080/?code=abc123&state=x") == "abc123"


def test_extract_code_raw():
    assert extract_code("  abc123 ") == "abc123"


def test_extract_code_rejects_garbage():
    with pytest.raises(YahooAuthError):
        extract_code("not a code here")


def test_authorize_url_contains_redirect():
    url = authorize_url("cid")
    assert "client_id=cid" in url and "redirect_uri=https%3A%2F%2Flocalhost%3A8080" in url
    assert "scope=" not in url
    assert "scope=fspt-r" in authorize_url("cid", scope="fspt-r")


def test_nba_today_uses_eastern_date():
    # 09:00 in Manila on Oct 21 is still Oct 20 in New York (21:00 EDT).
    manila = datetime(2026, 10, 21, 9, 0, tzinfo=ZoneInfo("Asia/Manila"))
    assert nba_today(manila) == date(2026, 10, 20)
    with pytest.raises(ValueError):
        nba_today(datetime(2026, 10, 21, 9, 0))
