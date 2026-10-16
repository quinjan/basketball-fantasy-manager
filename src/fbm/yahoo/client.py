"""Thin Yahoo Fantasy Sports API client (reads via GET, writes via XML PUT/POST)."""

from __future__ import annotations

import time
from typing import Any

import httpx

from fbm.yahoo import xmlparse
from fbm.yahoo.auth import TokenManager

BASE_URL = "https://fantasysports.yahooapis.com/fantasy/v2/"
RETRYABLE = {429, 500, 502, 503, 504, 999}
SCOPE_ERROR_MARKERS = ("scope", "permission")


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


class YahooClient:
    def __init__(self, tokens: TokenManager | None = None, timeout: float = 30.0, max_attempts: int = 4):
        self._tokens = tokens or TokenManager()
        self._http = httpx.Client(base_url=BASE_URL, timeout=timeout, headers={"User-Agent": "fbm/0.1"})
        self._max_attempts = max_attempts

    def close(self) -> None:
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

    def _request(self, method: str, path: str, content: str | None = None) -> httpx.Response:
        refreshed = False
        resp: httpx.Response | None = None
        for attempt in range(self._max_attempts):
            headers = {"Authorization": f"Bearer {self._tokens.access_token()}"}
            if content is not None:
                headers["Content-Type"] = "application/xml"
            resp = self._http.request(method, path, content=content, headers=headers)
            if resp.status_code == 401 and not refreshed and self._looks_like_expired_token(resp):
                self._tokens.force_refresh()
                refreshed = True
                continue
            if resp.status_code in RETRYABLE and attempt < self._max_attempts - 1:
                time.sleep(min(2**attempt, 8))
                continue
            break
        assert resp is not None
        if resp.status_code >= 400:
            raise YahooAPIError(resp.status_code, xmlparse.error_description(resp.text), path)
        return resp

    @staticmethod
    def _looks_like_expired_token(resp: httpx.Response) -> bool:
        blob = (resp.headers.get("www-authenticate", "") + resp.text).lower()
        return "token_expired" in blob or "invalid_token" in blob or "expired" in blob
