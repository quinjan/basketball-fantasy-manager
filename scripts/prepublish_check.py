"""Pre-publish scanner: flag secrets and personal data in git-tracked files. Exits 1 on findings."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

PATTERNS: dict[str, re.Pattern[str]] = {
    "private-key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "client-secret": re.compile(r"(?i)(client[_-]?secret|consumer[_-]?secret)\s*[=:]\s*['\"]?([0-9a-f]{32,})"),
    "refresh-token": re.compile(r"(?i)(refresh[_-]?token|access[_-]?token)\s*[=:]\s*['\"]?([A-Za-z0-9._~+/=-]{24,})"),
    "app-password": re.compile(r"(?i)(app[_-]?password|password)\s*[=:]\s*['\"]([^'\"\s]{8,})['\"]"),
    "github-token": re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})\b"),
    "email": re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    "user-path": re.compile(r"(?i)\b[A-Z]:\\\\?Users\\\\?[^\\\s'\"]+|/(?:home|Users)/[a-z0-9._-]+/"),
}
ALLOWED_EMAIL = re.compile(r"(?i)(@users\.noreply\.github\.com|@example\.(com|org)|@localhost)$")
# "user@FILE.md"-style matches are file references (e.g. "@AGENTS.md" imports), not addresses.
FILE_LIKE_DOMAIN = re.compile(r"(?i)\.(md|mdc|py|toml|json|txt|ya?ml)$")
IGNORE_FILE_MARKER = "prepublish: ignore-file"  # put in a file's first lines (e.g. this scanner's own tests)
ALLOWED_USERS = {"example", "user", "you", "<you>", "name", "runner", "<user>"}
PERSONAL_PATHS = re.compile(
    r"^(memory/(?!README)|reports/.+(?<!\.gitkeep)$|data/(?!\.gitkeep)|config/(settings|league|constants|yahoo_id_overrides)\.toml$)"
)
SKIP_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".lock"}


def _mask(value: str) -> str:
    return value[:3] + "***" if len(value) > 6 else "***"


def scan_text(rel: str, text: str) -> list[str]:
    findings = []
    for lineno, line in enumerate(text.splitlines(), 1):
        for name, pattern in PATTERNS.items():
            for m in pattern.finditer(line):
                hit = m.group(m.lastindex or 0)
                if name in ("app-password", "refresh-token") and re.fullmatch(r"[a-z_]+", hit):
                    continue
                if name == "email" and (ALLOWED_EMAIL.search(hit) or FILE_LIKE_DOMAIN.search(hit)):
                    continue
                if name == "user-path":
                    user = re.split(r"[\\/]+", hit.rstrip("/\\"))[-1].lower()
                    if user in ALLOWED_USERS or user.startswith(("<", "{", "$", "%")):
                        continue
                findings.append(f"{rel}:{lineno}: {name}: {_mask(hit)}")
    return findings


def scan_files(root: Path, files: list[str]) -> list[str]:
    findings = []
    for rel in files:
        rel = rel.replace("\\", "/")
        if PERSONAL_PATHS.match(rel):
            findings.append(f"{rel}: personal-state file is tracked")
            continue
        path = root / rel
        if path.suffix.lower() in SKIP_SUFFIXES or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if IGNORE_FILE_MARKER in "\n".join(text.splitlines()[:5]):
            continue
        findings.extend(scan_text(rel, text))
    return findings


def tracked_files(root: Path) -> list[str]:
    """Tracked files plus new files that would be committed (untracked and not ignored)."""
    cmd = ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"]
    out = subprocess.run(cmd, cwd=root, capture_output=True, text=True, check=True).stdout
    return sorted({f for f in out.split("\0") if f})


def main(argv: list[str] | None = None) -> int:
    root = Path(argv[0]) if argv else Path(__file__).resolve().parents[1]
    findings = scan_files(root, tracked_files(root))
    for f in findings:
        print(f)
    print(f"{len(findings)} finding(s)" if findings else "prepublish check: clean")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
