"""HTTP client for Hashtag Basketball: GET + ASP.NET WebForms postbacks, with the owner's stored session.

Read-only by design: only GETs and postbacks that change the VIEW (dropdowns, "UPDATE SCOREBOARD", "FIND TRADES").
Postbacks that would save anything on HT (or log out) are refused. Requests are paced by `hashtag.min_interval_s`.
A redirect to /premium/login or HT's sign-in form raises `HashtagSessionError`.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

from fbm.hashtag import session as ht_session
from fbm.hashtag.dom import Node, parse_html
from fbm.hashtag.errors import HashtagError, HashtagSessionError

BASE_URL = ht_session.BASE_URL
_FORBIDDEN = re.compile(r"save|delete|remove|lnkadd|logout|loginstatus|import|draft", re.I)
_DOPOSTBACK = re.compile(r"__doPostBack\(\s*(?:'|&#39;|\\')([^'&\\]+)")


def _min_interval() -> float:
    from fbm.constants import C

    return float(C("hashtag.min_interval_s"))


@dataclass
class Page:
    url: str
    html: str
    _doc: Node | None = field(default=None, repr=False)

    @property
    def doc(self) -> Node:
        if self._doc is None:
            self._doc = parse_html(self.html)
        return self._doc


# ------------------------------------------------------------------ form helpers (pure)


def full_name(doc: Node, short: str) -> str:
    """'DDTEAM2' -> 'ctl00$ContentPlaceHolder1$DDTEAM2' (exact names pass through)."""
    if "$" in short:
        return short
    for n in doc.iter():
        name = n.attrs.get("name") if n.tag in ("input", "select", "textarea") else None
        if name and (name == short or name.endswith("$" + short)):
            return name
    raise HashtagError(f"Form field {short} not found on the page.")


def select_options(doc: Node, name: str) -> list[tuple[str, str, bool]]:
    """[(value, text, selected)] of a <select>."""
    sel = doc.find("select", name=full_name(doc, name))
    if sel is None:
        return []
    return [(o.get("value", o.text()) or "", o.text(), "selected" in o.attrs) for o in sel.find_all("option")]


def selected_text(doc: Node, name: str) -> str | None:
    opts = select_options(doc, name)
    for _, text, sel in opts:
        if sel:
            return text
    return opts[0][1] if opts else None


def option_value(doc: Node, name: str, match: str | Callable[[str], bool]) -> str:
    """Value of the option whose text equals `match` (case/space-insensitive) or satisfies it."""
    norm = lambda s: " ".join(s.split()).casefold()  # noqa: E731
    pred = match if callable(match) else (lambda t: norm(t) == norm(match))
    for value, text, _ in select_options(doc, name):
        if pred(text):
            return value
    raise HashtagError(f"No option matching {match!r} in {name}.")


def form_fields(doc: Node) -> list[tuple[str, str]]:
    """What a browser would post for the page's form (no submit buttons): hidden/text inputs, checked boxes and
    radios, the selected option of each enabled select."""
    form = doc.find("form") or doc
    out: list[tuple[str, str]] = []
    for n in form.iter():
        name = n.attrs.get("name")
        if not name or "disabled" in n.attrs:
            continue
        if n.tag == "input":
            typ = (n.attrs.get("type") or "text").lower()
            if typ in ("submit", "button", "image", "reset", "file"):
                continue
            if typ in ("checkbox", "radio"):
                if "checked" in n.attrs:
                    out.append((name, n.attrs.get("value") or "on"))
                continue
            out.append((name, n.attrs.get("value") or ""))
        elif n.tag == "select":
            opts = n.find_all("option")
            chosen = [o for o in opts if "selected" in o.attrs] or opts[:1]
            for o in chosen[:1]:
                out.append((name, o.attrs.get("value", o.text())))
        elif n.tag == "textarea":
            out.append((name, n.text()))
    return out


def postback_targets(doc: Node) -> dict[str, tuple[str, str]]:
    """Clickable controls: visible text (upper-cased) -> ("event", __doPostBack target) for LinkButtons and
    `<button onclick=__doPostBack(...)>`, or ("button", name) for submit inputs. Postback links win over submits."""
    out: dict[str, tuple[str, str]] = {}
    for n in doc.iter():
        script = n.attrs.get("href") if n.tag == "a" else n.attrs.get("onclick") if n.tag == "button" else None
        m = _DOPOSTBACK.search(script or "")
        if m:
            out.setdefault(n.text().upper(), ("event", m.group(1)))
    for i in doc.find_all("input"):
        if (i.attrs.get("type") or "").lower() == "submit" and i.attrs.get("name"):
            out.setdefault((i.attrs.get("value") or "").strip().upper(), ("button", i.attrs["name"]))
    return out


def build_postback(
    doc: Node,
    *,
    fields: dict[str, str | None] | None = None,
    event_target: str | None = None,
    button: str | None = None,
) -> list[tuple[str, str]]:
    """Form data for a postback. `fields` override (None = remove, e.g. untick a checkbox); `event_target` is a
    dropdown autopostback or LinkButton; `button` a submit button name (its value is sent as a browser would)."""
    data = form_fields(doc)
    overrides = {full_name(doc, k): v for k, v in (fields or {}).items()}
    data = [(k, v) for k, v in data if k not in overrides]
    for k, v in overrides.items():
        if v is not None:
            data.append((k, v))
    target = full_name(doc, event_target) if event_target and "$" not in event_target else event_target
    for name in (target, button):
        if name and _FORBIDDEN.search(name):
            raise HashtagError(f"Refusing postback {name}: FBM never changes anything on Hashtag.")
    data = [(k, v) for k, v in data if k not in ("__EVENTTARGET", "__EVENTARGUMENT")]
    data += [("__EVENTTARGET", target or ""), ("__EVENTARGUMENT", "")]
    if button:
        bname = full_name(doc, button)
        node = doc.find("input", name=bname)
        data.append((bname, (node.attrs.get("value") if node is not None else "") or ""))
    return data


# ------------------------------------------------------------------ client


class HashtagClient:
    def __init__(
        self,
        session: ht_session.HTSession | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
        min_interval: float | None = None,
        base_url: str = BASE_URL,
        save_rotated: bool = True,
        timeout: float = 60.0,
    ):
        self.session = session if session is not None else ht_session.load()
        if self.session is None:
            raise HashtagSessionError("No Hashtag sign-in stored: run `fbm hashtag login`.")
        self.base_url = base_url.rstrip("/")
        self.min_interval = _min_interval() if min_interval is None else min_interval
        self.save_rotated = save_rotated
        self._http = httpx.Client(transport=transport, timeout=timeout, follow_redirects=False)
        self._last = 0.0
        self.requests = 0

    def close(self) -> None:
        try:
            if self.save_rotated and self.session.changed:
                ht_session.save(self.session)
        except Exception:  # noqa: BLE001 - keyring trouble must not break an import
            pass
        self._http.close()

    def __enter__(self) -> HashtagClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def url(self, path: str) -> str:
        return path if path.startswith("http") else self.base_url + "/" + path.lstrip("/")

    def _pace(self) -> None:
        wait = self.min_interval - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()

    def _send(self, method: str, url: str, data: list[tuple[str, str]] | None = None) -> Page:
        for _ in range(6):
            host = urlparse(url).hostname or ""
            if not host.endswith(ht_session.HOST) and not url.startswith(self.base_url):
                raise HashtagError(f"Refusing to send the Hashtag session to {host}.")
            self._pace()
            headers = dict(self.session.headers())
            if data is not None:
                headers["Content-Type"] = "application/x-www-form-urlencoded"
                headers["Referer"] = url
            try:
                resp = self._http.request(
                    method, url, headers=headers, content=_encode(data) if data is not None else None
                )
            except httpx.HTTPError as err:
                raise HashtagError(f"Network error talking to Hashtag: {type(err).__name__}") from err
            self.requests += 1
            self.session.absorb(resp)
            if resp.is_redirect:
                url = urljoin(url, resp.headers.get("location", ""))
                if ht_session.on_login_page(url):
                    raise HashtagSessionError("Hashtag redirected to its sign-in page: run `fbm hashtag login`.")
                method, data = "GET", None
                continue
            if resp.status_code in (401, 403):
                raise HashtagSessionError(f"Hashtag refused the session (HTTP {resp.status_code}).")
            if resp.status_code >= 400:
                raise HashtagError(f"Hashtag returned HTTP {resp.status_code} for {urlparse(url).path}.")
            html = resp.text
            if ht_session.on_login_page(str(resp.url)) or not ht_session.is_signed_in(html):
                raise HashtagSessionError("Hashtag served its sign-in page: run `fbm hashtag login`.")
            return Page(url, html)
        raise HashtagError("Too many redirects from Hashtag.")

    def get(self, path: str) -> Page:
        return self._send("GET", self.url(path))

    def postback(
        self,
        page: Page,
        *,
        fields: dict[str, Any] | None = None,
        event_target: str | None = None,
        button: str | None = None,
    ) -> Page:
        form = page.doc.find("form")
        action = urljoin(page.url, (form.attrs.get("action") if form is not None else "") or page.url)
        data = build_postback(
            page.doc,
            fields={k: (None if v is None else str(v)) for k, v in (fields or {}).items()},
            event_target=event_target,
            button=button,
        )
        return self._send("POST", action, data)

    def click(self, page: Page, text: str, *, fields: dict[str, Any] | None = None) -> Page:
        """Press a control by its visible text (LinkButton, __doPostBack button or submit input)."""
        targets = postback_targets(page.doc)
        hit = targets.get(text.strip().upper())
        if hit is None:
            raise HashtagError(f"No '{text}' control on the page.")
        kind, name = hit
        if kind == "event":
            return self.postback(page, fields=fields, event_target=name)
        return self.postback(page, fields=fields, button=name)

    def select(self, page: Page, name: str, *, value: str | None = None, text: str | None = None) -> Page:
        """Change one autopostback dropdown (by option value or visible text); no request if already selected."""
        if value is None:
            value = option_value(page.doc, name, text or "")
        current = [v for v, _, s in select_options(page.doc, name) if s]
        if current and current[0] == value:
            return page
        return self.postback(page, fields={name: value}, event_target=name)


def _encode(data: list[tuple[str, str]]) -> bytes:
    from urllib.parse import urlencode

    return urlencode(data).encode()
