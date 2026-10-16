"""Parse Yahoo Fantasy XML responses into plain dicts with predictable list shapes.

Yahoo's XML is far more regular than its JSON. Plural container elements (players, teams, stats, ...)
always yield lists of their singular children, so callers never have to special-case "one vs many".
"""

from __future__ import annotations

from typing import Any

import xmltodict

_SPECIAL_LIST_PARENTS = {
    "eligible_positions": "position",
    "eligible_positions_to_add": "position",
}


def _force_list(path: list[tuple[str, Any]] | None, key: str, _value: Any) -> bool:
    if not path:
        return False
    parent = path[-1][0]
    if _SPECIAL_LIST_PARENTS.get(parent) == key:
        return True
    return parent == f"{key}s"


def parse(xml_text: str) -> dict[str, Any]:
    doc = xmltodict.parse(xml_text, force_list=_force_list)
    if "fantasy_content" in doc:
        return doc["fantasy_content"]
    return doc


def as_list(value: Any) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def text(value: Any, default: str | None = None) -> str | None:
    """Return element text whether xmltodict produced a str or a dict with '#text'."""
    if value is None:
        return default
    if isinstance(value, dict):
        return value.get("#text", default)
    return str(value)


def error_description(xml_text: str) -> str:
    try:
        doc = xmltodict.parse(xml_text)
    except Exception:
        return xml_text[:300]
    for root_key in ("error", "yahoo:error"):
        err = doc.get(root_key)
        if isinstance(err, dict):
            desc = err.get("description") or err.get("yahoo:description")
            if desc:
                return text(desc) or ""
    return xml_text[:300]
