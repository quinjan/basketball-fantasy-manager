"""Yahoo web session: read the league with the owner's own Yahoo sign-in instead of an approved API app.

Yahoo's website reads the same Fantasy Sports v2 resources (same paths, same XML) from `pub-api-ro.fantasysports.
yahoo.com`, authenticated by the signed-in browser's cookies. `fbm yahoo login` signs in once in a browser window and
keeps only the cookies the browser would send to that read-only host, in the OS credential store (never on disk, never
printed). `YahooClient` then uses them for GETs. This route is undocumented by Yahoo and can change without notice; the
official API (`fbm auth`) stays the supported path and is used again after `fbm yahoo logout`.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote, urlparse

import httpx

from fbm import credentials

SESSION_HOST = "pub-api-ro.fantasysports.yahoo.com"
SESSION_BASE_URL = f"https://{SESSION_HOST}/fantasy/v2/"
WEB_ORIGIN = "https://basketball.fantasysports.yahoo.com"
LOGIN_URL = f"https://login.yahoo.com/?done={quote(WEB_ORIGIN + '/nba', safe='')}"
PROBE_PATH = "users;use_login=1/games;game_keys=nba"
DEFAULT_USER_AGENT = "fbm/0.1"
SIGN_IN_HOSTS = ("login.yahoo.com", "guce.yahoo.com", "consent.yahoo.com")
BROWSERS = ("msedge", "chrome", "chromium")


class YahooSessionError(RuntimeError):
    pass


def cookie_applies(domain: str, host: str = SESSION_HOST) -> bool:
    """Would a browser send a cookie with this domain to `host`? (".x.com" = x.com and subdomains; else host-only.)"""
    domain = (domain or "").lower()
    if domain.startswith("."):
        bare = domain[1:]
        return host == bare or host.endswith("." + bare)
    return host == domain


@dataclass
class WebSession:
    cookies: list[dict[str, Any]]  # {name, value, domain, path, expires}
    user_agent: str = DEFAULT_USER_AGENT
    saved_at: str = ""  # ISO UTC
    source: str = "browser"  # browser | paste
    _changed: bool = field(default=False, repr=False, compare=False)

    def cookie_header(self) -> str:
        return "; ".join(f"{c['name']}={c['value']}" for c in self.cookies)

    def headers(self) -> dict[str, str]:
        return {"User-Agent": self.user_agent or DEFAULT_USER_AGENT, "Origin": WEB_ORIGIN, "Referer": WEB_ORIGIN + "/"}

    @property
    def changed(self) -> bool:
        return self._changed

    def absorb(self, resp: httpx.Response) -> bool:
        """Keep cookies Yahoo rotates via Set-Cookie (so the session lasts as long as the browser's would)."""
        changed = False
        for c in resp.cookies.jar:
            if not cookie_applies(c.domain or SESSION_HOST):
                continue
            current = next((x for x in self.cookies if x["name"] == c.name), None)
            if current is None:
                self.cookies.append(
                    {"name": c.name, "value": c.value or "", "domain": c.domain, "path": c.path, "expires": c.expires}
                )
                changed = True
            elif current["value"] != (c.value or ""):
                current.update(value=c.value or "", expires=c.expires)
                changed = True
        self._changed = self._changed or changed
        return changed

    def age_days(self, now: datetime | None = None) -> float | None:
        if not self.saved_at:
            return None
        return ((now or datetime.now(UTC)) - datetime.fromisoformat(self.saved_at)).total_seconds() / 86400

    def to_json(self) -> str:
        data = asdict(self)
        data.pop("_changed", None)
        return json.dumps(data, separators=(",", ":"))

    @classmethod
    def from_json(cls, raw: str) -> WebSession:
        data = json.loads(raw)
        return cls(
            cookies=[dict(c) for c in data.get("cookies", []) if c.get("name")],
            user_agent=data.get("user_agent") or DEFAULT_USER_AGENT,
            saved_at=data.get("saved_at", ""),
            source=data.get("source", "browser"),
        )


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def from_browser_cookies(cookies: list[dict[str, Any]], user_agent: str) -> WebSession:
    kept = [
        {
            "name": c["name"],
            "value": c.get("value", ""),
            "domain": c.get("domain", ""),
            "path": c.get("path", "/"),
            "expires": c.get("expires"),
        }
        for c in cookies
        if c.get("name") and cookie_applies(c.get("domain", ""))
    ]
    if not kept:
        raise YahooSessionError("The browser has no Yahoo sign-in cookies for the Fantasy API host.")
    return WebSession(kept, user_agent or DEFAULT_USER_AGENT, _now_iso(), "browser")


def from_cookie_header(header: str, user_agent: str = "") -> WebSession:
    """Parse a pasted `Cookie:` request-header value ("a=b; c=d"), as copied from the browser's DevTools."""
    text = header.strip()
    if text.lower().startswith("cookie:"):
        text = text[len("cookie:") :].strip()
    cookies = []
    for part in text.split(";"):
        name, sep, value = part.strip().partition("=")
        if sep and name.strip():
            cookies.append({"name": name.strip(), "value": value.strip(), "domain": ".yahoo.com", "path": "/"})
    if not cookies:
        raise YahooSessionError("No cookies found. Copy the whole value of the `cookie` request header.")
    return WebSession(cookies, user_agent.strip() or DEFAULT_USER_AGENT, _now_iso(), "paste")


# ------------------------------------------------------------------ storage (OS credential store)


def load() -> WebSession | None:
    try:
        raw = credentials.get_large(credentials.YAHOO_WEB_SESSION)
    except Exception:
        return None
    if not raw:
        return None
    try:
        session = WebSession.from_json(raw)
    except (ValueError, TypeError, AttributeError):
        return None
    return session if session.cookies else None


def save(session: WebSession) -> None:
    credentials.put_large(credentials.YAHOO_WEB_SESSION, session.to_json())
    session._changed = False


def clear() -> None:
    credentials.delete_large(credentials.YAHOO_WEB_SESSION)


# ------------------------------------------------------------------ one-time browser sign-in


def _launch(playwright: Any, browser: str | None) -> Any:
    errors = []
    for name in [browser] if browser else BROWSERS:
        try:
            if name == "chromium":
                return playwright.chromium.launch(headless=False)
            return playwright.chromium.launch(channel=name, headless=False)
        except Exception as err:
            errors.append(f"{name}: {str(err).strip().splitlines()[0][:120]}")
    raise YahooSessionError(
        "Couldn't start a browser (" + "; ".join(errors) + "). Install Microsoft Edge or Google Chrome, or run "
        "`uv run playwright install chromium`, or use `fbm yahoo login --paste`."
    )


def _on_sign_in_page(url: str) -> bool:
    host = urlparse(url).hostname or ""
    return not host or host in SIGN_IN_HOSTS


def browser_login(
    timeout_s: float = 600, browser: str | None = None, echo: Callable[[str], None] = print
) -> WebSession:
    """Open a fresh browser window on Yahoo's sign-in page and wait until Yahoo accepts the session for reads.

    Nothing is stored on disk: the window uses a throwaway profile, and only the cookies a browser would send to the
    read-only Fantasy host are returned.
    """
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except ImportError as err:
        raise YahooSessionError("Playwright isn't installed: run `uv sync`, or use `fbm yahoo login --paste`.") from err

    with sync_playwright() as p:
        instance = _launch(p, browser)
        try:
            context = instance.new_context()
            page = context.new_page()
            page.goto(LOGIN_URL, wait_until="domcontentloaded")
            echo("Sign in to Yahoo in the browser window (tick 'Stay signed in'). FBM continues automatically.")
            deadline = time.monotonic() + timeout_s
            last_probe = 0.0
            while time.monotonic() < deadline:
                pages = [pg for pg in context.pages if not pg.is_closed()]
                if not pages or not instance.is_connected():
                    raise YahooSessionError("The browser window was closed before sign-in finished.")
                if not all(_on_sign_in_page(pg.url) for pg in pages) and time.monotonic() - last_probe >= 4:
                    last_probe = time.monotonic()
                    resp = context.request.get(SESSION_BASE_URL + PROBE_PATH, fail_on_status_code=False)
                    if resp.status == 200:
                        user_agent = pages[0].evaluate("navigator.userAgent")
                        return from_browser_cookies(context.cookies([SESSION_BASE_URL]), user_agent)
                pages[0].wait_for_timeout(1500)
            raise YahooSessionError(f"Timed out after {timeout_s:.0f}s waiting for the Yahoo sign-in.")
        except PlaywrightError as err:
            raise YahooSessionError(f"Browser error: {str(err).strip().splitlines()[0][:200]}") from err
        finally:
            try:
                instance.close()
            except Exception:
                pass
