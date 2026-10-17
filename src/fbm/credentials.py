"""Secret storage backed by the OS keyring (Windows Credential Manager / macOS Keychain / Linux Secret Service).

Secrets never touch the repo, logs, or reports.
"""

from __future__ import annotations

import keyring
from keyring.errors import PasswordDeleteError

SERVICE = "fbm"

YAHOO_CLIENT_ID = "yahoo_client_id"
YAHOO_CLIENT_SECRET = "yahoo_client_secret"
YAHOO_REFRESH_TOKEN = "yahoo_refresh_token"
YAHOO_WEB_SESSION = "yahoo_web_session"
GMAIL_ADDRESS = "gmail_address"
GMAIL_APP_PASSWORD = "gmail_app_password"

# Windows Credential Manager caps one secret at 1280 characters; larger values are split across numbered entries.
CHUNK_CHARS = 1000
_CHUNKS_PREFIX = "chunks:"


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


def _chunk_count(head: str | None) -> int:
    if head and head.startswith(_CHUNKS_PREFIX):
        try:
            return int(head[len(_CHUNKS_PREFIX) :])
        except ValueError:
            return 0
    return 0


def put_large(name: str, value: str) -> None:
    """Store a value of any length (split into `name.0`, `name.1`, ... with `name` holding the chunk count)."""
    delete_large(name)
    parts = [value[i : i + CHUNK_CHARS] for i in range(0, len(value), CHUNK_CHARS)] or [""]
    for i, part in enumerate(parts):
        put(f"{name}.{i}", part)
    put(name, f"{_CHUNKS_PREFIX}{len(parts)}")


def get_large(name: str) -> str | None:
    head = get(name)
    count = _chunk_count(head)
    if not count:
        return head
    parts = [get(f"{name}.{i}") for i in range(count)]
    if any(p is None for p in parts):
        return None
    return "".join(p or "" for p in parts)


def delete_large(name: str) -> None:
    for i in range(_chunk_count(get(name))):
        delete(f"{name}.{i}")
    delete(name)
