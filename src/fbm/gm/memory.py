"""File-based AI memory (local-only, gitignored; seeded from templates/memory).

memory/
  strategy.md        GM-maintained narrative of the current build (owner-approved strategy is authoritative)
  strategy.json      machine-readable approved strategy (punts/targets) — written only by an executed proposal
  playbook.md        lessons learned, each with evidence (updated by the weekly retro)
  league-settings.md auto-generated from Yahoo settings
  journal/YYYY-MM-DD.md  append-only decision journal (local date)
  managers/<team>.md     opponent manager notes
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from fbm.config import MEMORY_DIR, PROJECT_ROOT
from fbm.timeutil import ET

JOURNAL_DIR = MEMORY_DIR / "journal"
MANAGERS_DIR = MEMORY_DIR / "managers"
STRATEGY_JSON = MEMORY_DIR / "strategy.json"
JOURNAL_KINDS = ("decision", "observation", "recommendation", "proposal", "review", "retro", "owner", "system")


TEMPLATE_DIR = PROJECT_ROOT / "templates" / "memory"


def ensure_memory(memory_dir: Path = MEMORY_DIR, template_dir: Path = TEMPLATE_DIR) -> list[str]:
    """Copy any missing memory files from templates/memory (never overwrites). Returns the created paths."""
    created: list[str] = []
    if not template_dir.exists():
        return created
    for src in sorted(template_dir.rglob("*")):
        dest = memory_dir / src.relative_to(template_dir)
        if src.is_dir():
            dest.mkdir(parents=True, exist_ok=True)
        elif not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
            created.append(dest.relative_to(memory_dir).as_posix())
    return created


def _read(path: Path, limit: int) -> str:
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8")
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n…[truncated {len(text) - limit} chars; full file: {path.name}]"


def current_strategy() -> dict[str, Any]:
    if not STRATEGY_JSON.exists():
        return {"punts": [], "targets": [], "notes": "No approved strategy yet.", "approved_at": None}
    return json.loads(STRATEGY_JSON.read_text(encoding="utf-8"))


def write_strategy(punts: list[str], targets: list[str], notes: str, proposal_ref: str) -> dict[str, Any]:
    data = {
        "punts": punts,
        "targets": targets,
        "notes": notes,
        "approved_at": datetime.now().astimezone().isoformat(timespec="minutes"),
        "proposal": proposal_ref,
    }
    STRATEGY_JSON.parent.mkdir(parents=True, exist_ok=True)
    STRATEGY_JSON.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return data


def journal_append(kind: str, title: str, body: str, now: datetime | None = None) -> Path:
    if kind not in JOURNAL_KINDS:
        raise ValueError(f"kind must be one of {JOURNAL_KINDS}")
    moment = now or datetime.now().astimezone()
    path = JOURNAL_DIR / f"{moment.date().isoformat()}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    header = "" if path.exists() else f"# Journal — {moment.date().isoformat()}\n\n"
    stamp = f"{moment.strftime('%H:%M')} local / {moment.astimezone(ET).strftime('%a %H:%M')} ET"
    entry = f"## {stamp} · {kind} · {title.strip()}\n\n{body.strip()}\n\n"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(header + entry)
    return path


def recent_journal(days: int = 3, limit: int = 6000) -> str:
    if not JOURNAL_DIR.exists():
        return ""
    files = sorted(JOURNAL_DIR.glob("*.md"), reverse=True)[:days]
    text = "\n".join(f.read_text(encoding="utf-8") for f in reversed(files))
    return text if len(text) <= limit else "…[older entries truncated]\n" + text[-limit:]


def journal_digest(days: int = 3, limit: int = 1200) -> str:
    """Entry headers only (`## time · kind · title`, newest kept) for the last `days` journal files: what happened,
    without the bodies. Read memory/journal/<date>.md for the full text."""
    if not JOURNAL_DIR.exists():
        return ""
    lines: list[str] = []
    for f in sorted(JOURNAL_DIR.glob("*.md"), reverse=True)[:days][::-1]:
        lines += [
            f"{f.stem} {ln[3:].strip()}" for ln in f.read_text(encoding="utf-8").splitlines() if ln.startswith("## ")
        ]
    text = "\n".join(lines)
    return text if len(text) <= limit else "…[older entries omitted]\n" + text[-limit:]


def manager_notes(limit_each: int = 800) -> dict[str, str]:
    if not MANAGERS_DIR.exists():
        return {}
    return {f.stem: _read(f, limit_each) for f in sorted(MANAGERS_DIR.glob("*.md"))}


def manager_note_names() -> list[str]:
    return sorted(f.stem for f in MANAGERS_DIR.glob("*.md")) if MANAGERS_DIR.exists() else []


def memory_files(chars: int = 1000) -> dict[str, str]:
    """Strategy narrative, playbook (twice the budget: it holds the owner's rules) and the Yahoo settings summary,
    each cut to a budget with a pointer to the full file."""
    return {
        "strategy_md": _read(MEMORY_DIR / "strategy.md", chars),
        "playbook_md": _read(MEMORY_DIR / "playbook.md", chars * 2),
        "league_settings_md": _read(MEMORY_DIR / "league-settings.md", chars // 3),
    }
