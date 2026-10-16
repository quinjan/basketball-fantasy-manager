"""Secret storage backed by the OS keyring (Windows Credential Manager on Windows).

Secrets never touch the repo, logs, or reports.
"""

from __future__ import annotations

import keyring
from keyring.errors import PasswordDeleteError

SERVICE = "fbm"

YAHOO_CLIENT_ID = "yahoo_client_id"
YAHOO_CLIENT_SECRET = "yahoo_client_secret"
YAHOO_REFRESH_TOKEN = "yahoo_refresh_token"
GMAIL_ADDRESS = "gmail_address"
GMAIL_APP_PASSWORD = "gmail_app_password"


class MissingCredentialError(RuntimeError):
    pass


def get(name: str) -> str | None:
    return keyring.get_password(SERVICE, name)


def put(name: str, value: str) -> None:
    keyring.set_password(SERVICE, name, value)


def delete(name: str) -> None:
    try:
        keyring.delete_password(SERVICE, name)
    except PasswordDeleteError:
        pass


def require(name: str) -> str:
    value = get(name)
    if not value:
        raise MissingCredentialError(f"Missing credential '{name}'. Run `fbm auth` first.")
    return value
