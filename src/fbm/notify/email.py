"""Email notifications via Gmail SMTP (App Password). Every message is also logged to the outbox table."""

from __future__ import annotations

import smtplib
import sqlite3
import ssl
from datetime import datetime, time
from email.message import EmailMessage

import markdown as md

from fbm import credentials
from fbm.settings import Settings, load_settings
from fbm.store import db

URGENT_KINDS = ("approval", "alert")
_STYLE = (
    "body{font-family:Segoe UI,Arial,sans-serif;font-size:14px;color:#1f2328;max-width:760px}"
    "table{border-collapse:collapse;margin:8px 0}th,td{border:1px solid #d0d7de;padding:4px 8px;text-align:left}"
    "th{background:#f6f8fa}code{background:#f6f8fa;padding:1px 4px;border-radius:4px}"
    "h1,h2,h3{margin:12px 0 6px}.foot{color:#656d76;font-size:12px;margin-top:16px}"
)


class NotifyError(RuntimeError):
    pass


def in_quiet_hours(window: str, now: datetime | None = None) -> bool:
    """window like '23:00-07:00' (local time); wraps midnight."""
    if not window or "-" not in window:
        return False
    start_s, end_s = (w.strip() for w in window.split("-", 1))
    start, end = time.fromisoformat(start_s), time.fromisoformat(end_s)
    current = (now or datetime.now()).time()
    return start <= current < end if start < end else current >= start or current < end


def render_html(markdown_body: str) -> str:
    body = md.markdown(markdown_body, extensions=["tables", "sane_lists"])
    return (
        f"<html><head><style>{_STYLE}</style></head><body>{body}"
        "<p class='foot'>Sent by FBM — your fantasy front office. Fantasy data provided by Yahoo Fantasy.</p>"
        "</body></html>"
    )


def _log(conn: sqlite3.Connection, subject: str, body: str, status: str, detail: str = "") -> int:
    cur = conn.execute(
        "INSERT INTO outbox(created_at, channel, subject, body, status, detail) VALUES (?,?,?,?,?,?)",
        (db.now_iso(), "email", subject, body, status, detail),
    )
    conn.commit()
    return cur.lastrowid


def send_email(
    subject: str,
    markdown_body: str,
    kind: str = "report",
    conn: sqlite3.Connection | None = None,
    settings: Settings | None = None,
    smtp_factory=smtplib.SMTP_SSL,
) -> dict[str, str]:
    """kind: report | approval | alert | retro. Non-urgent kinds are held during quiet hours (logged as 'held')."""
    settings = settings or load_settings()
    conn = conn or db.connect()
    subject = f"[FBM] {subject}"
    if not settings.email_enabled:
        _log(conn, subject, markdown_body, "disabled")
        return {"status": "disabled"}
    if kind not in URGENT_KINDS and in_quiet_hours(settings.quiet_hours):
        _log(conn, subject, markdown_body, "held", "quiet hours")
        return {"status": "held", "detail": "quiet hours"}
    sender = credentials.get(credentials.GMAIL_ADDRESS)
    password = credentials.get(credentials.GMAIL_APP_PASSWORD)
    if not sender or not password:
        _log(conn, subject, markdown_body, "not_configured")
        raise NotifyError("Gmail not configured. Run `fbm notify setup`.")
    recipients = list(settings.recipients) or [sender]
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, sender, ", ".join(recipients)
    msg.set_content(markdown_body)
    msg.add_alternative(render_html(markdown_body), subtype="html")
    try:
        with smtp_factory("smtp.gmail.com", 465, context=ssl.create_default_context(), timeout=30) as smtp:
            smtp.login(sender, password)
            smtp.send_message(msg)
    except (smtplib.SMTPException, OSError) as err:
        _log(conn, subject, markdown_body, "failed", str(err)[:300])
        raise NotifyError(f"Email send failed: {err}") from err
    _log(conn, subject, markdown_body, "sent", ", ".join(recipients))
    return {"status": "sent", "to": ", ".join(recipients)}
