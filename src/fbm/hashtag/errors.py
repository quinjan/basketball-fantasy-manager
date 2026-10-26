"""Hashtag Basketball errors. Each maps to a `hashtag_import_log` status (see `importer`)."""

from __future__ import annotations


class HashtagError(RuntimeError):
    """Any failure talking to or reading Hashtag Basketball."""

    status = "error"


class HashtagSessionError(HashtagError):
    """Not signed in (HT redirected to /premium/login, or served its sign-in form): `fbm hashtag login`."""

    status = "session_expired"


class HashtagLayoutChanged(HashtagError):
    """A page no longer has the structure the parser expects. Never write partial data when this is raised."""

    status = "layout_changed"

    def __init__(self, page: str, detail: str):
        super().__init__(f"{page}: {detail}")
        self.page = page
        self.detail = detail
