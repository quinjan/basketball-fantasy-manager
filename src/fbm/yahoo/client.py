"""Thin Yahoo Fantasy Sports API client (reads via GET, writes via XML PUT/POST).

Two ways in, same v2 resources and XML: the owner's Yahoo web session when one is saved (`fbm yahoo login`, read-only
host, GET only; see fbm.yahoo.session), else the official API with an approved OAuth app (`fbm auth`).
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from fbm.yahoo import session as web_session
from fbm.yahoo import xmlparse
from fbm.yahoo.auth import TokenManager

BASE_URL = "https://fantasysports.yahooapis.com/fantasy/v2/"
RETRYABLE = {429, 500, 502, 503, 504, 999}
SCOPE_ERROR_MARKERS = ("scope", "permission")
SESSION_EXPIRED = "Yahoo web session expired or signed out: run `fbm yahoo login`"


class YahooAPIError(RuntimeError):
    def __init__(self, status: int, description: str, path: str):
        super().__init__(f"Yahoo API {status} on {path}: {description}")
        self.status = status
        self.description = description
        self.path = path

    @property
    def is_scope_error(self) -> bool:
        lowered = self.description.lower()
        return self.status in (401, 403) and any(m in lowered for m in SCOPE_ERROR_MARKERS)


def _min_interval() -> float:
    from fbm.constants import C

    return float(C("yahoo.session_min_interval_s"))


class YahooClient:
    def __init__(
        self,
        tokens: TokenManager | None = None,
        timeout: float = 30.0,
        max_attempts: int = 4,
        session: web_session.WebSession | None = None,
    ):
        saved = session is None and tokens is None
        if saved:
            session = web_session.load()
        self._session = session
        self._persist_session = saved and session is not None
        self._tokens = None if session is not None else (tokens or TokenManager())
        if session is not None:
            base, headers, self._interval = web_session.SESSION_BASE_URL, session.headers(), _min_interval()
        else:
            base, headers, self._interval = BASE_URL, {"User-Agent": "fbm/0.1"}, 0.0
        self._http = httpx.Client(base_url=base, timeout=timeout, headers=headers)
        self._max_attempts = max_attempts
        self._last_request = 0.0

    @property
    def auth_mode(self) -> str:
        return "web_session" if self._session is not None else "oauth"

    def close(self) -> None:
        if self._persist_session and self._session is not None and self._session.changed:
            try:
                web_session.save(self._session)
            except Exception:
                pass
        self._http.close()

    def __enter__(self) -> YahooClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def get(self, path: str) -> dict[str, Any]:
        resp = self._request("GET", path)
        return xmlparse.parse(resp.text)

    def put_xml(self, path: str, xml_body: str) -> dict[str, Any]:
        resp = self._request("PUT", path, content=xml_body)
        return xmlparse.parse(resp.text) if resp.text.strip() else {}

    def post_xml(self, path: str, xml_body: str) -> dict[str, Any]:
        resp = self._request("POST", path, content=xml_body)
        return xmlparse.parse(resp.text) if resp.text.strip() else {}

    def _pace(self) -> None:
        if self._interval:
            wait = self._last_request + self._interval - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._last_request = time.monotonic()

    def _auth_headers(self) -> dict[str, str]:
        if self._session is not None:
            return {"Cookie": self._session.cookie_header()}
        assert self._tokens is not None
        return {"Authorization": f"Bearer {self._tokens.access_token()}"}

    def _request(self, method: str, path: str, content: str | None = None) -> httpx.Response:
        if self._session is not None and method != "GET":
            raise YahooAPIError(405, "the Yahoo web session is read-only (FBM never writes to Yahoo)", path)
        refreshed = False
        resp: httpx.Response | None = None
        for attempt in range(self._max_attempts):
            headers = self._auth_headers()
            if content is not None:
                headers["Content-Type"] = "application/xml"
            self._pace()
            resp = self._http.request(method, path, content=content, headers=headers)
            if self._session is not None:
                self._session.absorb(resp)
            if (
                resp.status_code == 401
                and self._tokens is not None
                and not refreshed
                and self._looks_like_expired_token(resp)
            ):
                self._tokens.force_refresh()
                refreshed = True
                continue
            if resp.status_code in RETRYABLE and attempt < self._max_attempts - 1:
                time.sleep(min(2**attempt, 8))
                continue
            break
        assert resp is not None
        if resp.status_code >= 400:
            description = xmlparse.error_description(resp.text)
            if self._session is not None and resp.status_code == 401:
                description = f"{SESSION_EXPIRED} ({description})"
            raise YahooAPIError(resp.status_code, description, path)
        return resp

    @staticmethod
    def _looks_like_expired_token(resp: httpx.Response) -> bool:
        blob = (resp.headers.get("www-authenticate", "") + resp.text).lower()
        return "token_expired" in blob or "invalid_token" in blob or "expired" in blob
