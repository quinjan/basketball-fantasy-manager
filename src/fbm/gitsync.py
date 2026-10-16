"""Commit + push memory/reports/config to the private repo as the owner's personal GitHub account.

The Copilot app injects a credential helper bound to a different (work) account via GIT_CONFIG_PARAMETERS, so pushes
override the helper with `gh auth git-credential` and the personal account's token.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass

from fbm.config import PROJECT_ROOT

SYNC_PATHS = ("memory", "reports", "config")
TRAILER = "Co-authored-by: Copilot App <223556219+Copilot@users.noreply.github.com>"


@dataclass(frozen=True)
class SyncOutcome:
    committed: bool
    pushed: bool
    detail: str


def _git(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=PROJECT_ROOT, capture_output=True, text=True, env=env, timeout=120)


def sync_memory(message: str, account: str, push: bool = True) -> SyncOutcome:
    _git("add", "--", *SYNC_PATHS)
    if _git("diff", "--cached", "--quiet").returncode == 0:
        return SyncOutcome(False, False, "nothing to commit")
    commit = _git("commit", "-q", "-m", message, "-m", TRAILER)
    if commit.returncode != 0:
        return SyncOutcome(False, False, commit.stderr.strip()[:300])
    if not push:
        return SyncOutcome(True, False, "committed (push skipped)")
    gh = shutil.which("gh")
    if not gh:
        return SyncOutcome(True, False, "committed; gh CLI not found, push skipped")
    token = subprocess.run([gh, "auth", "token", "--user", account], capture_output=True, text=True, timeout=30)
    if token.returncode != 0 or not token.stdout.strip():
        return SyncOutcome(True, False, f"committed; no gh token for '{account}', push skipped")
    env = {**os.environ, "GH_TOKEN": token.stdout.strip()}
    helper = f"!'{gh.replace(os.sep, '/')}' auth git-credential"
    result = _git(
        "-c",
        "credential.https://github.com.helper=",
        "-c",
        f"credential.https://github.com.helper={helper}",
        "push",
        "-q",
        env=env,
    )
    if result.returncode != 0:
        return SyncOutcome(True, False, f"committed; push failed: {result.stderr.strip()[:300]}")
    return SyncOutcome(True, True, "committed and pushed")
