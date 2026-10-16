"""Yahoo OAuth2 (authorization-code grant) with refresh-token persistence in the OS keyring."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from fbm import credentials

AUTH_URL = "https://api.login.yahoo.com/oauth2/request_auth"
TOKEN_URL = "https://api.login.yahoo.com/oauth2/get_token"
REDIRECT_URI = "https://localhost:8080"
EXPIRY_SAFETY_SECONDS = 120


class YahooAuthError(RuntimeError):
    pass


def authorize_url(client_id: str, redirect_uri: str = REDIRECT_URI) -> str:
    query = urlencode(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "language": "en-us",
        }
    )
    return f"{AUTH_URL}?{query}"


def extract_code(pasted: str) -> str:
    """Accept either the raw code or the full redirected URL containing ?code=..."""
    text = pasted.strip()
    if "code=" in text:
        parsed = urlparse(text if "://" in text else f"https://x/?{text.split('?', 1)[-1]}")
        codes = parse_qs(parsed.query).get("code")
        if codes and codes[0]:
            return codes[0].strip()
    if not text or " " in text:
        raise YahooAuthError("Could not find an authorization code in the pasted value.")
    return text


def _token_request(client_id: str, client_secret: str, data: dict[str, str]) -> dict:
    resp = httpx.post(TOKEN_URL, data=data, auth=(client_id, client_secret), timeout=30)
    if resp.status_code != 200:
        raise YahooAuthError(f"Token endpoint returned {resp.status_code}: {resp.text[:300]}")
    payload = resp.json()
    if "access_token" not in payload:
        raise YahooAuthError("Token endpoint response missing access_token.")
    return payload


def exchange_code(client_id: str, client_secret: str, code: str, redirect_uri: str = REDIRECT_URI) -> dict:
    return _token_request(
        client_id,
        client_secret,
        {"grant_type": "authorization_code", "redirect_uri": redirect_uri, "code": code},
    )


def refresh_access_token(client_id: str, client_secret: str, refresh_token: str) -> dict:
    return _token_request(
        client_id,
        client_secret,
        {"grant_type": "refresh_token", "redirect_uri": REDIRECT_URI, "refresh_token": refresh_token},
    )


def store_token_response(payload: dict) -> None:
    refresh = payload.get("refresh_token")
    if refresh:
        credentials.put(credentials.YAHOO_REFRESH_TOKEN, refresh)


@dataclass
class TokenManager:
    """Holds a short-lived access token in memory; refreshes from the keyring-stored refresh token."""

    _access_token: str | None = None
    _expires_at: float = 0.0
    _clock: Callable[[], float] = field(default=time.time, repr=False)

    def access_token(self) -> str:
        if self._access_token and self._clock() < self._expires_at - EXPIRY_SAFETY_SECONDS:
            return self._access_token
        return self.force_refresh()

    def force_refresh(self) -> str:
        client_id = credentials.require(credentials.YAHOO_CLIENT_ID)
        client_secret = credentials.require(credentials.YAHOO_CLIENT_SECRET)
        refresh_token = credentials.require(credentials.YAHOO_REFRESH_TOKEN)
        payload = refresh_access_token(client_id, client_secret, refresh_token)
        if payload.get("refresh_token") and payload["refresh_token"] != refresh_token:
            store_token_response(payload)
        self._access_token = payload["access_token"]
        self._expires_at = self._clock() + float(payload.get("expires_in", 3600))
        return self._access_token
