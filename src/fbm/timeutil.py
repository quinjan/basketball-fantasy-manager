"""Time helpers. Yahoo NBA game days are US/Eastern dates.

The owner's own time zone is `[owner] timezone` in config/settings.toml.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")


def now_et() -> datetime:
    return datetime.now(ET)


def nba_today(now: datetime | None = None) -> date:
    """Current NBA game day (Eastern date)."""
    moment = now if now is not None else datetime.now(ET)
    if moment.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    return moment.astimezone(ET).date()


def nba_tomorrow(now: datetime | None = None) -> date:
    return nba_today(now) + timedelta(days=1)


def parse_date(value: str) -> date:
    return date.fromisoformat(value)
