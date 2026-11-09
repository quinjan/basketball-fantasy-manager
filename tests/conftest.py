"""Suite-wide isolation."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _hashtag_off_by_default(monkeypatch):
    """The owner's config/settings.toml may enable Hashtag mode; tests opt in explicitly instead."""
    monkeypatch.setenv("FBM_HASHTAG_ENABLED", "0")


@pytest.fixture(autouse=True)
def _fresh_yahoo_week_cache():
    """Yahoo scoreboard-by-week lookups are cached per process; tests must not share them."""
    from fbm.gm import services

    services._WEEK_MATCHUPS.clear()
    yield
    services._WEEK_MATCHUPS.clear()
