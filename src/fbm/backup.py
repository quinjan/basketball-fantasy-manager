"""Local backups of personal state. Memory, reports, and settings are local-only (gitignored), never pushed.

`fbm backup` zips memory/, reports/, config/settings.toml, config/league.toml and data/fbm.db into a local folder.
Secrets are never included: they live in the OS credential store, not in any of these paths.
"""

from __future__ import annotations

import zipfile
from datetime import datetime
from pathlib import Path

from fbm.config import PROJECT_ROOT

BACKUP_PATHS = ("memory", "reports", "config/settings.toml", "config/league.toml", "data/fbm.db")
_EXCLUDE_NAMES = (".env",)
_EXCLUDE_SUFFIXES = (".env", ".key", ".pem", ".token")

LOCAL_ONLY_MESSAGE = (
    "Memory is local-only now: memory/, reports/ and config/settings.toml are gitignored and never pushed. "
    "Use `fbm backup [--dest DIR]` to save a local zip."
)


def backup_files(root: Path = PROJECT_ROOT) -> list[Path]:
    out: list[Path] = []
    for rel in BACKUP_PATHS:
        path = root / rel
        if path.is_file():
            out.append(path)
        elif path.is_dir():
            out.extend(p for p in sorted(path.rglob("*")) if p.is_file())
    return [p for p in out if p.name.lower() not in _EXCLUDE_NAMES and not p.name.lower().endswith(_EXCLUDE_SUFFIXES)]


def backup(dest: Path | None = None, root: Path = PROJECT_ROOT, now: datetime | None = None) -> Path:
    """Zip the personal state under `root` into `dest` (default <root>/data/backups, gitignored). Returns the zip."""
    dest = dest or root / "data" / "backups"
    dest.mkdir(parents=True, exist_ok=True)
    target = dest / f"fbm-backup-{(now or datetime.now()).strftime('%Y%m%d-%H%M%S')}.zip"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in backup_files(root):
            zf.write(path, path.relative_to(root).as_posix())
    return target
