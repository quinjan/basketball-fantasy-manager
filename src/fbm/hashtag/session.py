"""Hashtag Basketball Premium sign-in: the owner's own browser session, kept in the OS credential store.

`fbm hashtag login` opens a browser window on HT's sign-in page; the owner signs in themselves (FBM never sees the
password). Once HT serves a signed-in premium page, FBM keeps only the cookies a browser would send to
hashtagbasketball.com, in the OS credential store (`credentials.HASHTAG_WEB_SESSION`, never on disk, never printed).
`fbm hashtag login --paste` accepts a copied `Cookie:` request header instead.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

import httpx

from fbm import credentials
from fbm.hashtag.errors import HashtagSessionError

HOST = "hashtagbasketball.com"
BASE_URL = f"https://{HOST}"
LOGIN_PATH = "/premium/login"
LOGIN_URL = BASE_URL + LOGIN_PATH
PREMIUM_HOME = BASE_URL + "/premium/"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)
BROWSERS = ("msedge", "chrome", "chromium")


def cookie_applies(domain: str, host: str = HOST) -> bool:
    domain = (domain or "").lower().lstrip(".")
    return bool(domain) and (host == domain or host.endswith("." + domain) or domain.endswith("." + host))


@dataclass
class HTSession:
    cookies: list[dict[str, Any]]  # {name, value, domain, path, expires}
    user_agent: str = DEFAULT_USER_AGENT
    saved_at: str = ""
    source: str = "browser"  # browser | paste
    _changed: bool = field(default=False, repr=False, compare=False)

    def cookie_header(self) -> str:
        return "; ".join(f"{c['name']}={c['value']}" for c in self.cookies)

    def headers(self) -> dict[str, str]:
        return {"User-Agent": self.user_agent or DEFAULT_USER_AGENT, "Cookie": self.cookie_header()}

    @property
    def changed(self) -> bool:
        return self._changed

    def absorb(self, resp: httpx.Response) -> bool:
        """Keep cookies HT rotates via Set-Cookie (ASP.NET auth/session cookies get refreshed)."""
        changed = False
        for header in resp.headers.get_list("set-cookie"):
            first = header.split(";", 1)[0]
            name, sep, value = first.partition("=")
            name, value = name.strip(), value.strip()
            if not sep or not name:
                continue
            attrs = {k.strip().lower(): v.strip() for k, _, v in (p.partition("=") for p in header.split(";")[1:])}
            domain = attrs.get("domain") or HOST
            if not cookie_applies(domain):
                continue
            current = next((c for c in self.cookies if c["name"] == name), None)
            if current is None:
                self.cookies.append({"name": name, "value": value, "domain": domain, "path": attrs.get("path", "/")})
                changed = True
            elif current["value"] != value:
                current["value"] = value
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
    def from_json(cls, raw: str) -> HTSession:
        data = json.loads(raw)
        return cls(
            cookies=[dict(c) for c in data.get("cookies", []) if c.get("name")],
            user_agent=data.get("user_agent") or DEFAULT_USER_AGENT,
            saved_at=data.get("saved_at", ""),
            source=data.get("source", "browser"),
        )


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def from_browser_cookies(cookies: list[dict[str, Any]], user_agent: str = "") -> HTSession:
    kept = [
        {
            "name": c["name"],
            "value": c.get("value", ""),
            "domain": c.get("domain", HOST),
            "path": c.get("path", "/"),
            "expires": c.get("expires"),
        }
        for c in cookies
        if c.get("name") and cookie_applies(c.get("domain", ""))
    ]
    if not kept:
        raise HashtagSessionError("The browser has no Hashtag Basketball sign-in cookies.")
    return HTSession(kept, user_agent or DEFAULT_USER_AGENT, _now_iso(), "browser")


def from_cookie_header(header: str, user_agent: str = "") -> HTSession:
    """Parse a pasted `Cookie:` request-header value ("a=b; c=d") copied from the browser's DevTools."""
    text = header.strip()
    if text.lower().startswith("cookie:"):
        text = text[len("cookie:") :].strip()
    cookies = []
    for part in text.split(";"):
        name, sep, value = part.strip().partition("=")
        if sep and name.strip():
            cookies.append({"name": name.strip(), "value": value.strip(), "domain": HOST, "path": "/"})
    if not cookies:
        raise HashtagSessionError("No cookies found. Copy the whole value of the `cookie` request header.")
    return HTSession(cookies, user_agent.strip() or DEFAULT_USER_AGENT, _now_iso(), "paste")


# ------------------------------------------------------------------ storage (OS credential store)


def load() -> HTSession | None:
    try:
        raw = credentials.get_large(credentials.HASHTAG_WEB_SESSION)
    except Exception:
        return None
    if not raw:
        return None
    try:
        s = HTSession.from_json(raw)
    except (ValueError, TypeError, AttributeError):
        return None
    return s if s.cookies else None


def save(session: HTSession) -> None:
    credentials.put_large(credentials.HASHTAG_WEB_SESSION, session.to_json())
    session._changed = False


def clear() -> None:
    credentials.delete_large(credentials.HASHTAG_WEB_SESSION)


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
    raise HashtagSessionError(
        "Couldn't start a browser (" + "; ".join(errors) + "). Install Microsoft Edge or Google Chrome, or run "
        "`uv run playwright install chromium`, or use `fbm hashtag login --paste`."
    )


def on_login_page(url: str) -> bool:
    p = urlparse(url)
    return (p.hostname or "").endswith(HOST) and p.path.lower().startswith(LOGIN_PATH)


def browser_login(timeout_s: float = 600, browser: str | None = None, echo: Callable[[str], None] = print) -> HTSession:
    """Open a fresh browser window on HT's sign-in page; return the session once HT serves a signed-in page."""
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except ImportError as err:
        raise HashtagSessionError(
            "Playwright isn't installed: run `uv sync`, or use `fbm hashtag login --paste`."
        ) from err

    with sync_playwright() as p:
        instance = _launch(p, browser)
        try:
            context = instance.new_context()
            page = context.new_page()
            page.goto(LOGIN_URL, wait_until="domcontentloaded")
            echo("Sign in to Hashtag Basketball in the browser window (tick 'Remember me'). FBM continues by itself.")
            deadline = time.monotonic() + timeout_s
            last_probe = 0.0
            while time.monotonic() < deadline:
                pages = [pg for pg in context.pages if not pg.is_closed()]
                if not pages or not instance.is_connected():
                    raise HashtagSessionError("The browser window was closed before sign-in finished.")
                if not all(on_login_page(pg.url) for pg in pages) and time.monotonic() - last_probe >= 4:
                    last_probe = time.monotonic()
                    resp = context.request.get(PREMIUM_HOME, fail_on_status_code=False)
                    if resp.status == 200 and not on_login_page(resp.url) and is_signed_in(resp.text()):
                        user_agent = pages[0].evaluate("navigator.userAgent")
                        return from_browser_cookies(context.cookies([BASE_URL]), user_agent)
                pages[0].wait_for_timeout(1500)
            raise HashtagSessionError(f"Timed out after {timeout_s:.0f}s waiting for the Hashtag sign-in.")
        except PlaywrightError as err:
            raise HashtagSessionError(f"Browser error: {str(err).strip().splitlines()[0][:200]}") from err
        finally:
            try:
                instance.close()
            except Exception:
                pass


def is_signed_in(html: str) -> bool:
    """HT's header link (`LoginStatus2`) reads 'Logout' when signed in; the sign-in form has myLogin$UserName."""
    if "myLogin$UserName" in html:
        return False
    i = html.find('id="LoginStatus2"')
    if i < 0:
        return True  # pages without the standard header: rely on the URL check
    j = html.find(">", i)
    return html[j + 1 : j + 20].strip().lower().startswith("logout")
