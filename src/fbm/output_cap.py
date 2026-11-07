"""Output budget for `fbm tool`: keep agent context small without losing data.

A tool result whose compact JSON exceeds the budget is shrunk (long lists and strings are cut, with markers) and the
full result is saved to `data/tool_out/<tool>.json` (local-only). The agent reads the file, narrows the tool's
arguments, or re-runs with `--full` only when it really needs everything.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fbm.config import DATA_DIR

DEFAULT_MAX_CHARS = 12000  # ~3K tokens
OUT_DIR = DATA_DIR / "tool_out"

# Action-critical outputs are never cut: the owner's exact steps, lineups and proposal records.
UNCAPPED = frozenset(
    {
        "optimize_lineup",
        "lineup_steps",
        "proposal_steps",
        "get_proposal",
        "list_proposals",
        "create_proposal",
        "confirm_done",
        "record_owner_decision",
        "record_review",
        "context_pack",  # budgets itself (notes_chars, memory_chars, journal, signals_top)
    }
)

# (max list items, max string chars), tried in order until the result fits.
_LEVELS = ((30, 800), (15, 400), (8, 240), (5, 160), (3, 100), (2, 60))


def _size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str))


def _shrink(value: Any, items: int, chars: int) -> Any:
    if isinstance(value, str):
        return value if len(value) <= chars else value[:chars] + f"…[+{len(value) - chars} chars]"
    if isinstance(value, list):
        kept = [_shrink(v, items, chars) for v in value[:items]]
        if len(value) > items:
            kept.append(f"…[+{len(value) - items} more items]")
        return kept
    if isinstance(value, dict):
        return {k: _shrink(v, items, chars) for k, v in value.items()}
    return value


def _outline(value: Any) -> Any:
    """Last resort: top-level keys with their sizes, so the agent knows what to ask for."""
    if isinstance(value, dict):
        return {k: f"<{_size(v)} chars>" for k, v in value.items()}
    return f"<{_size(value)} chars>"


def save_full(name: str, result: Any, out_dir: Path | None = None) -> Path:
    path = (out_dir or OUT_DIR) / f"{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return path


def cap(name: str, result: Any, max_chars: int = DEFAULT_MAX_CHARS, out_dir: Path | None = None) -> Any:
    """Return `result` unchanged when it fits (or max_chars <= 0, or the tool is uncapped / errored); otherwise a
    shrunk copy carrying `_truncated` with the original size and the file holding the full result."""
    if max_chars <= 0 or name in UNCAPPED or (isinstance(result, dict) and "error" in result):
        return result
    original = _size(result)
    if original <= max_chars:
        return result
    path = save_full(name, result, out_dir)
    note = {
        "original_chars": original,
        "full_output": str(path),
        "how": "Narrow the tool's arguments, read the file for the cut parts, or re-run with --full.",
    }
    for items, chars in _LEVELS:
        shrunk = _shrink(result, items, chars)
        wrapped = {**shrunk, "_truncated": note} if isinstance(shrunk, dict) else {"result": shrunk, "_truncated": note}
        if _size(wrapped) <= max_chars:
            return wrapped
    return {"outline": _outline(result), "_truncated": note}
