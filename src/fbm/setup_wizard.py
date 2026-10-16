"""`fbm setup` logic: settings file merging, timezone handling, and the idempotent setup steps.

Pure where possible; every external command goes through an injectable `runner` so tests never touch the network,
Yahoo, or the user's real config. Secrets are never handled here: Yahoo and Gmail credentials are entered by the
user in the `fbm auth` / `fbm notify setup` prompts and stored in the OS credential store.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tomllib
import zoneinfo
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fbm.config import DATA_DIR, MEMORY_DIR, PROJECT_ROOT
from fbm.settings import AGENTS, AUTOMATION_MODES, EXAMPLE_PATH, JOBS, SETTINGS_PATH

OK, SKIP, FAIL, PENDING = "OK", "SKIP", "FAIL", "PENDING"

# CLI each agent runs headless / is launched with (used for the prerequisite check only).
AGENT_CLIS: dict[str, tuple[str, ...]] = {
    "copilot": ("copilot",),
    "claude-code": ("claude",),
    "codex": ("codex",),
    "cursor": ("cursor-agent", "cursor"),
    "gemini": ("gemini",),
    "generic": (),
}

YAHOO_GUIDE = (
    "Apply for Yahoo Fantasy API access at https://sports.yahoo.com/developer/access/ (approval can take weeks).",
    "After the 'access enabled' email, create a NEW Yahoo app (API permissions: Fantasy Sports Read; "
    "redirect URI https://localhost:8080) at https://developer.yahoo.com/apps/create/.",
    "Submit that app's Client ID at https://sports.yahoo.com/developer/application-confirmation/.",
    "Run `fbm auth` and enter the Client ID/Secret in its prompts (stored in your OS credential store).",
    "Run `fbm league select`, then `fbm settings`.",
    "Run `fbm setup --league-source yahoo --yes`, then `fbm snapshot` and `fbm ids`.",
)

QUIET_HOURS_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d-([01]\d|2[0-3]):[0-5]\d$")


@dataclass
class StepResult:
    step: str
    status: str
    detail: str = ""


@dataclass
class SetupOptions:
    """Values the user chose. None = keep the existing value (or the example default on first run)."""

    agent: str | None = None
    gm_model: str | None = None
    reviewer_model: str | None = None
    routine_model: str | None = None
    timezone: str | None = None
    automation: str | None = None
    jobs: list[str] | None = None
    league_source: str | None = None
    email: bool | None = None
    quiet_hours: str | None = None
    expiry_hours: float | None = None
    git_account: str | None = None
    skip_data: bool = False
    yahoo_approved: bool = False

    def updates(self) -> dict[tuple[str, str], Any]:
        mapping = {
            ("agent", "name"): self.agent,
            ("agent", "gm_model"): self.gm_model,
            ("agent", "reviewer_model"): self.reviewer_model,
            ("agent", "routine_model"): self.routine_model,
            ("owner", "timezone"): self.timezone,
            ("automation", "mode"): self.automation,
            ("automation", "jobs"): self.jobs,
            ("league", "source"): self.league_source,
            ("notify", "email_enabled"): self.email,
            ("notify", "quiet_hours"): self.quiet_hours,
            ("autonomy", "default_expiry_hours"): self.expiry_hours,
            ("git", "account"): self.git_account,
        }
        return {k: v for k, v in mapping.items() if v is not None}


# ---------------------------------------------------------------- validation


def valid_timezone(name: str) -> bool:
    if not name or name.startswith(("/", ".")) or ".." in name:
        return False
    try:
        zoneinfo.ZoneInfo(name)
    except (zoneinfo.ZoneInfoNotFoundError, ValueError):
        return False
    return True


def detect_timezone() -> str:
    """Best-effort local IANA zone; "UTC" when it can't be determined."""
    env = os.environ.get("TZ", "")
    if valid_timezone(env):
        return env
    if sys.platform == "win32":
        shell = shutil.which("pwsh") or shutil.which("powershell")
        if shell:
            script = (
                "$x=$null; [void][System.TimeZoneInfo]::TryConvertWindowsIdToIanaId("
                "([System.TimeZoneInfo]::Local).Id, [ref]$x); $x"
            )
            try:
                out = subprocess.run(
                    [shell, "-NoProfile", "-Command", script], capture_output=True, text=True, timeout=15
                ).stdout.strip()
                if valid_timezone(out):
                    return out
            except (OSError, subprocess.SubprocessError):
                pass
    else:
        try:
            target = os.path.realpath("/etc/localtime")
            if "zoneinfo/" in target:
                name = target.split("zoneinfo/", 1)[1]
                if valid_timezone(name):
                    return name
        except OSError:
            pass
        tz_file = Path("/etc/timezone")
        if tz_file.exists():
            name = tz_file.read_text(encoding="utf-8").strip()
            if valid_timezone(name):
                return name
    return "UTC"


def validate(opts: SetupOptions) -> list[str]:
    errors: list[str] = []
    if opts.agent is not None and opts.agent not in AGENTS:
        errors.append(f"--agent must be one of {', '.join(AGENTS)}")
    if opts.timezone is not None and not valid_timezone(opts.timezone):
        errors.append(f"--timezone '{opts.timezone}' is not a valid IANA time zone (e.g. America/New_York)")
    if opts.automation is not None and opts.automation not in AUTOMATION_MODES:
        errors.append(f"--automation must be one of {', '.join(AUTOMATION_MODES)}")
    if opts.jobs is not None:
        bad = [j for j in opts.jobs if j not in JOBS]
        if bad:
            errors.append(f"unknown --jobs {', '.join(bad)} (choose from {', '.join(JOBS)})")
    if opts.league_source is not None and opts.league_source not in ("demo", "yahoo"):
        errors.append("--league-source must be demo or yahoo")
    if opts.quiet_hours and not QUIET_HOURS_RE.match(opts.quiet_hours):
        errors.append("--quiet-hours must look like 23:00-07:00 (or be empty)")
    if opts.expiry_hours is not None and not 1 <= opts.expiry_hours <= 168:
        errors.append("--expiry-hours must be between 1 and 168")
    return errors


def parse_jobs(text: str | None) -> list[str] | None:
    if text is None:
        return None
    return [j.strip() for j in text.split(",") if j.strip() and j.strip().lower() != "none"]


# ---------------------------------------------------------------- settings file


def toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return repr(value)
    if isinstance(value, list | tuple):
        return "[" + ", ".join(toml_value(v) for v in value) + "]"
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


_SECTION_RE = re.compile(r"^\s*\[([A-Za-z0-9_.-]+)\]\s*(#.*)?$")
_KEY_RE = re.compile(r'^(\s*)([A-Za-z0-9_-]+)(\s*=\s*)("(?:[^"\\]|\\.)*"|\[[^\]]*\]|[^\s#]+)(.*)$')


def render_settings(example_text: str, values: dict[str, dict[str, Any]]) -> str:
    """Fill the example (keeping its comments) with `values`; keys/sections not in the example are appended."""
    remaining = {s: dict(kv) for s, kv in values.items()}
    out: list[str] = []
    section = ""
    for line in example_text.splitlines():
        m = _SECTION_RE.match(line)
        if m:
            _flush(out, section, remaining)
            section = m.group(1)
            out.append(line)
            continue
        k = _KEY_RE.match(line)
        if k and section in remaining and k.group(2) in remaining[section]:
            value = remaining[section].pop(k.group(2))
            line = f"{k.group(1)}{k.group(2)}{k.group(3)}{toml_value(value)}{k.group(5)}"
        out.append(line)
    _flush(out, section, remaining)
    for sec, kv in remaining.items():
        if kv:
            out.extend(["", f"[{sec}]", *(f"{key} = {toml_value(v)}" for key, v in kv.items())])
    return "\n".join(out).rstrip("\n") + "\n"


def _flush(out: list[str], section: str, remaining: dict[str, dict[str, Any]]) -> None:
    """Insert keys from `remaining[section]` that the example doesn't have, before the section's trailing blanks."""
    kv = remaining.get(section, {})
    if not section or not kv:
        return
    blanks = 0
    while out and out[-1].strip() == "":
        out.pop()
        blanks += 1
    out.extend(f"{key} = {toml_value(v)}" for key, v in kv.items())
    out.extend([""] * blanks)
    remaining[section] = {}


def merged_settings(existing_text: str | None, opts: SetupOptions) -> dict[str, dict[str, Any]]:
    values: dict[str, dict[str, Any]] = {}
    if existing_text:
        for sec, kv in tomllib.loads(existing_text).items():
            if isinstance(kv, dict):
                values[sec] = dict(kv)
    for (sec, key), value in opts.updates().items():
        values.setdefault(sec, {})[key] = value
    return values


def write_settings(
    opts: SetupOptions, path: Path = SETTINGS_PATH, example: Path = EXAMPLE_PATH
) -> tuple[StepResult, dict[str, dict[str, Any]]]:
    existing = path.read_text(encoding="utf-8") if path.exists() else None
    try:
        values = merged_settings(existing, opts)
        text = render_settings(example.read_text(encoding="utf-8"), values)
        tomllib.loads(text)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        return StepResult("Settings", FAIL, f"{path.name}: {exc}"), {}
    if existing == text:
        return StepResult("Settings", SKIP, f"{_rel(path)} unchanged"), values
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return StepResult("Settings", OK, f"{'updated' if existing else 'created'} {_rel(path)}"), values


def current_values(path: Path = SETTINGS_PATH, example: Path = EXAMPLE_PATH) -> dict[str, dict[str, Any]]:
    """Effective settings (existing file, else the example defaults)."""
    for p in (path, example):
        if p.exists():
            try:
                return tomllib.loads(p.read_text(encoding="utf-8"))
            except tomllib.TOMLDecodeError:
                continue
    return {}


def _rel(path: Path) -> str:
    try:
        return path.relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return str(path)


# ---------------------------------------------------------------- steps


Runner = Callable[[Sequence[str]], tuple[int, str]]


def fbm_command(*args: str) -> list[str]:
    # Not `-m fbm.cli`: running cli.py as __main__ skips the command groups registered on the imported `fbm.cli`.
    return [sys.executable, "-c", "from fbm.cli import app; app()", *args]


def default_runner(cmd: Sequence[str]) -> tuple[int, str]:
    """Run a command (output captured). Returns (returncode, combined output)."""
    try:
        proc = subprocess.run(
            list(cmd), cwd=PROJECT_ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace"
        )
    except OSError as exc:
        return 127, str(exc)
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def _missing_command(output: str) -> bool:
    return "No such command" in output


def _tail(output: str, n: int = 300) -> str:
    text = " ".join(output.split())
    return text[-n:]


def check_prerequisites(agent: str, which: Callable[[str], str | None] = shutil.which) -> list[StepResult]:
    uv = which("uv") or (os.environ.get("UV") if os.environ.get("UV") and Path(os.environ["UV"]).exists() else None)
    results = [
        StepResult(
            "Python >= 3.12",
            OK if sys.version_info >= (3, 12) else FAIL,
            sys.version.split()[0],
        ),
        StepResult("uv", OK if uv else FAIL, uv or "not on PATH: https://docs.astral.sh/uv/"),
        StepResult("git", OK if which("git") else FAIL, which("git") or "not on PATH: https://git-scm.com/"),
    ]
    clis = AGENT_CLIS.get(agent, ())
    if clis:
        found = next((which(c) for c in clis if which(c)), None)
        results.append(
            StepResult(
                f"{agent} CLI",
                OK if found else SKIP,
                found or f"`{clis[0]}` not on PATH (only needed for terminal/headless use)",
            )
        )
    return results


def setup_memory(memory_dir: Path = MEMORY_DIR, template_dir: Path | None = None) -> StepResult:
    from fbm.gm import memory

    created = memory.ensure_memory(memory_dir, template_dir or memory.TEMPLATE_DIR)
    if created:
        return StepResult("Memory", OK, f"created {', '.join(created)}")
    return StepResult("Memory", SKIP, "memory/ already present")


def prior_season_loaded(db_path: Path = DATA_DIR / "fbm.db") -> bool:
    if not db_path.exists():
        return False
    try:
        from datetime import date

        from fbm.data.espn import current_season
        from fbm.data.queries import store_status
        from fbm.store import db as store

        prior = current_season(date.today()) - 1
        seasons = store_status(store.connect())["seasons"]
        return any(int(s["season"]) == prior and (s["boxed"] or 0) > 0 for s in seasons)
    except Exception:  # noqa: BLE001 — any store problem just means "run the backfill"
        return False


def run_data(runner: Runner, skip: bool, backfilled: bool) -> list[StepResult]:
    if skip:
        return [StepResult("NBA data", SKIP, "--skip-data (run `fbm data backfill` and `fbm data sync` later)")]
    results = []
    if backfilled:
        results.append(StepResult("NBA backfill", SKIP, "prior season already loaded"))
    else:
        code, out = runner(fbm_command("data", "backfill"))
        results.append(
            StepResult("NBA backfill", OK if code == 0 else FAIL, "prior season" if code == 0 else _tail(out))
        )
    code, out = runner(fbm_command("data", "sync"))
    results.append(StepResult("NBA sync", OK if code == 0 else FAIL, "up to date" if code == 0 else _tail(out)))
    return results


def run_demo(
    runner: Runner, source: str, demo_file: Path = DATA_DIR / "demo_league.json", skip_data: bool = False
) -> StepResult:
    if source != "demo":
        return StepResult("Demo league", SKIP, "league source is yahoo")
    if demo_file.exists():
        return StepResult("Demo league", SKIP, "demo league already built")
    if skip_data:
        return StepResult("Demo league", SKIP, "needs NBA data: run `fbm data backfill`, then `fbm demo build`")
    code, out = runner(fbm_command("demo", "build"))
    return StepResult("Demo league", OK if code == 0 else FAIL, "built" if code == 0 else _tail(out))


def run_optional(runner: Runner, name: str, args: Sequence[str], ok_detail: str) -> StepResult:
    """Run a sibling command group that may not exist in older checkouts."""
    code, out = runner(fbm_command(*args))
    if code == 0:
        return StepResult(name, OK, ok_detail)
    if _missing_command(out):
        return StepResult(name, SKIP, f"`fbm {' '.join(args[:2])}` not available in this version")
    return StepResult(name, FAIL, _tail(out))


def run_agent_install(runner: Runner, agent: str) -> StepResult:
    return run_optional(runner, "Agent files", ["agent", "install", "--agent", agent], f"installed for {agent}")


def run_automation_install(runner: Runner, mode: str, jobs: Sequence[str], dry_run: bool = False) -> StepResult:
    if mode == "none":
        return StepResult("Automation", SKIP, "mode none (run the GM on demand from your agent)")
    args = ["automation", "install", "--mode", mode]
    if jobs:
        args += ["--jobs", ",".join(jobs)]
    if dry_run:
        args.append("--dry-run")
    return run_optional(runner, "Automation", args, f"{mode}: {', '.join(jobs) or 'default jobs'}")


def yahoo_status(source: str, approved: bool, league_toml: Path) -> list[StepResult]:
    if source == "yahoo" and league_toml.exists():
        return [StepResult("Yahoo league", OK, "connected (config/league.toml)")]
    if source == "yahoo":
        return [StepResult("Yahoo league", PENDING, "source is yahoo but no league selected: run `fbm league select`")]
    if approved:
        return [StepResult("Yahoo league", PENDING, "access approved: follow the Yahoo steps below")]
    return [StepResult("Yahoo league", PENDING, "using the demo league until Yahoo API access is approved")]


def run_health(runner: Runner) -> StepResult:
    code, out = runner(fbm_command("health"))
    if code == 0:
        return StepResult("Health", OK, "no FAIL checks (see `fbm health` for WARNs)")
    return StepResult("Health", FAIL, "some checks FAIL: run `fbm health` for fixes")


@dataclass
class SetupReport:
    results: list[StepResult] = field(default_factory=list)
    settings: dict[str, dict[str, Any]] = field(default_factory=dict)

    @property
    def failed(self) -> bool:
        return any(r.status == FAIL for r in self.results)

    def next_steps(self) -> list[str]:
        steps: list[str] = []
        source = self.settings.get("league", {}).get("source", "demo")
        if any(r.status == FAIL for r in self.results):
            steps.append("Fix the FAIL steps above, then re-run `fbm setup --check`.")
        if source != "yahoo" or any(r.step == "Yahoo league" and r.status == PENDING for r in self.results):
            steps.extend(YAHOO_GUIDE)
        if self.settings.get("notify", {}).get("email_enabled"):
            steps.append("Run `fbm notify setup` to store the Gmail app password (prompted, never shown).")
        steps.append(
            "Open your AI agent in this folder (restart it so it loads the FBM MCP server) and ask: "
            '"Open the GM office".'
        )
        return steps


def check(
    runner: Runner = default_runner,
    settings_path: Path = SETTINGS_PATH,
    memory_dir: Path = MEMORY_DIR,
    league_toml: Path | None = None,
    which: Callable[[str], str | None] = shutil.which,
) -> SetupReport:
    """Verification only: no files written, no installs."""
    from fbm.config import LEAGUE_CONFIG_PATH

    values = current_values(settings_path)
    agent = values.get("agent", {}).get("name", "generic")
    report = SetupReport(settings=values)
    report.results.extend(check_prerequisites(agent, which))
    report.results.append(
        StepResult("Settings", OK, _rel(settings_path))
        if settings_path.exists()
        else StepResult("Settings", FAIL, "config/settings.toml missing: run `fbm setup`")
    )
    report.results.append(
        StepResult("Memory", OK, "present")
        if (memory_dir / "playbook.md").exists()
        else StepResult("Memory", FAIL, "memory/ missing: run `fbm setup`")
    )
    report.results.append(run_optional(runner, "Agent files", ["agent", "status"], "see `fbm agent status`"))
    source = values.get("league", {}).get("source", "demo")
    report.results.extend(yahoo_status(source, False, league_toml or LEAGUE_CONFIG_PATH))
    report.results.append(run_health(runner))
    return report


def run_setup(
    opts: SetupOptions,
    runner: Runner = default_runner,
    settings_path: Path = SETTINGS_PATH,
    example_path: Path = EXAMPLE_PATH,
    memory_dir: Path = MEMORY_DIR,
    template_dir: Path | None = None,
    league_toml: Path | None = None,
    demo_file: Path = DATA_DIR / "demo_league.json",
    backfilled: Callable[[], bool] = prior_season_loaded,
    which: Callable[[str], str | None] = shutil.which,
    on_step: Callable[[StepResult], None] | None = None,
) -> SetupReport:
    """Run every setup step in order. Each step is idempotent and reported OK/SKIP/FAIL/PENDING."""
    from fbm.config import LEAGUE_CONFIG_PATH

    report = SetupReport()

    def add(*results: StepResult) -> None:
        for r in results:
            report.results.append(r)
            if on_step:
                on_step(r)

    settings_result, values = write_settings(opts, settings_path, example_path)
    report.settings = values or current_values(settings_path, example_path)
    agent = report.settings.get("agent", {}).get("name", "generic")
    add(*check_prerequisites(agent, which))
    add(settings_result)
    add(setup_memory(memory_dir, template_dir))
    add(*run_data(runner, opts.skip_data, False if opts.skip_data else backfilled()))
    source = report.settings.get("league", {}).get("source", "demo")
    add(run_demo(runner, source, demo_file, opts.skip_data))
    add(run_agent_install(runner, agent))
    automation = report.settings.get("automation", {})
    add(run_automation_install(runner, automation.get("mode", "none"), automation.get("jobs", [])))
    add(*yahoo_status(source, opts.yahoo_approved, league_toml or LEAGUE_CONFIG_PATH))
    add(run_health(runner))
    return report
