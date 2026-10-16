import tomllib
import zipfile
from pathlib import Path

from fbm import backup as backup_mod
from fbm import setup_wizard as wiz
from fbm.config import CONFIG_DIR, PROJECT_ROOT
from fbm.gm import memory
from fbm.settings import load_settings

EXAMPLE = CONFIG_DIR / "settings.example.toml"
TEMPLATES = PROJECT_ROOT / "templates" / "memory"


class FakeRunner:
    def __init__(self, fail: tuple[str, ...] = (), missing: tuple[str, ...] = ()):
        self.calls: list[list[str]] = []
        self.fail, self.missing = fail, missing

    def __call__(self, cmd):
        args = list(cmd)[3:]  # drop [python, -m, fbm.cli]
        self.calls.append(args)
        if args and args[0] in self.missing:
            return 2, "Error: No such command"
        if args and args[0] in self.fail:
            return 1, "boom"
        return 0, ""


def _which(name):
    return f"/bin/{name}"


def _run(tmp_path: Path, opts: wiz.SetupOptions, runner=None, backfilled=False):
    runner = runner or FakeRunner()
    report = wiz.run_setup(
        opts,
        runner=runner,
        settings_path=tmp_path / "config" / "settings.toml",
        example_path=EXAMPLE,
        memory_dir=tmp_path / "memory",
        template_dir=TEMPLATES,
        league_toml=tmp_path / "config" / "league.toml",
        demo_file=tmp_path / "demo_league.json",
        backfilled=lambda: backfilled,
        which=_which,
    )
    return report, runner


def test_timezone_validation():
    assert wiz.valid_timezone("America/New_York")
    assert wiz.valid_timezone("UTC")
    assert not wiz.valid_timezone("UTC+8")
    assert not wiz.valid_timezone("Mars/Olympus")
    assert not wiz.valid_timezone("../etc/passwd")
    assert wiz.valid_timezone(wiz.detect_timezone())


def test_validate_flags():
    assert wiz.validate(wiz.SetupOptions(agent="codex", timezone="Europe/London", jobs=["health"])) == []
    errors = wiz.validate(
        wiz.SetupOptions(agent="x", timezone="Nowhere", automation="cron", jobs=["bad"], quiet_hours="late")
    )
    assert len(errors) == 5
    assert wiz.parse_jobs("data-sync, health,") == ["data-sync", "health"]
    assert wiz.parse_jobs("none") == []


def test_render_keeps_comments_and_values():
    opts = wiz.SetupOptions(agent="claude-code", gm_model="big", timezone="Asia/Tokyo", jobs=["health"], email=True)
    text = wiz.render_settings(EXAMPLE.read_text(encoding="utf-8"), wiz.merged_settings(None, opts))
    data = tomllib.loads(text)
    assert data["agent"]["name"] == "claude-code"
    assert data["agent"]["gm_model"] == "big"
    assert data["owner"]["timezone"] == "Asia/Tokyo"
    assert data["automation"]["jobs"] == ["health"]
    assert data["notify"]["email_enabled"] is True
    assert data["yahoo"]["api_writes"] is False
    assert "# decisions: a frontier model" in text


def test_write_settings_merges_existing(tmp_path):
    path = tmp_path / "settings.toml"
    wiz.write_settings(wiz.SetupOptions(agent="codex", gm_model="m1", quiet_hours="23:00-07:00"), path, EXAMPLE)
    path.write_text(path.read_text(encoding="utf-8") + '\n[review]\nlegacy = "x"\n', encoding="utf-8")
    result, _ = wiz.write_settings(wiz.SetupOptions(reviewer_model="m2"), path, EXAMPLE)
    assert result.status == wiz.OK
    s = load_settings(path)
    assert (s.agent, s.gm_model, s.reviewer_model, s.quiet_hours) == ("codex", "m1", "m2", "23:00-07:00")
    assert tomllib.loads(path.read_text(encoding="utf-8"))["review"]["legacy"] == "x"
    again, _ = wiz.write_settings(wiz.SetupOptions(), path, EXAMPLE)
    assert again.status == wiz.SKIP


def test_memory_templates(tmp_path):
    created = memory.ensure_memory(tmp_path / "memory", TEMPLATES)
    assert {"strategy.md", "strategy.json", "playbook.md", "managers/README.md", "journal/.gitkeep"} <= set(created)
    assert "Owner rules" in (tmp_path / "memory" / "playbook.md").read_text(encoding="utf-8")
    assert '"punts": []' in (tmp_path / "memory" / "strategy.json").read_text(encoding="utf-8")
    (tmp_path / "memory" / "playbook.md").write_text("mine", encoding="utf-8")
    assert memory.ensure_memory(tmp_path / "memory", TEMPLATES) == []
    assert (tmp_path / "memory" / "playbook.md").read_text(encoding="utf-8") == "mine"


def test_non_interactive_flow(tmp_path):
    opts = wiz.SetupOptions(agent="gemini", timezone="Europe/Paris", automation="engine-only", jobs=["health"])
    report, runner = _run(tmp_path, opts)
    assert not report.failed
    assert ["data", "backfill"] in runner.calls
    assert ["data", "sync"] in runner.calls
    assert ["demo", "build"] in runner.calls
    assert ["agent", "install", "--agent", "gemini"] in runner.calls
    assert ["automation", "install", "--mode", "engine-only", "--jobs", "health"] in runner.calls
    assert runner.calls[-1] == ["health"]
    assert load_settings(tmp_path / "config" / "settings.toml").owner_timezone == "Europe/Paris"
    assert (tmp_path / "memory" / "playbook.md").exists()
    assert any("Open the GM office" in s for s in report.next_steps())


def test_idempotent_and_missing_commands(tmp_path):
    (tmp_path / "demo_league.json").write_text("{}", encoding="utf-8")
    runner = FakeRunner(missing=("agent", "automation"))
    report, runner = _run(tmp_path, wiz.SetupOptions(automation="scheduler", skip_data=True), runner)
    status = {r.step: r.status for r in report.results}
    assert status["NBA data"] == status["Demo league"] == wiz.SKIP
    assert status["Agent files"] == status["Automation"] == wiz.SKIP
    assert not any(c[0] in ("data", "demo") for c in runner.calls)


def test_failures_reported(tmp_path):
    report, _ = _run(tmp_path, wiz.SetupOptions(), FakeRunner(fail=("health", "data")), backfilled=True)
    status = {r.step: r.status for r in report.results}
    assert status["NBA backfill"] == wiz.SKIP
    assert status["NBA sync"] == status["Health"] == wiz.FAIL
    assert report.failed


def test_check_writes_nothing(tmp_path):
    report = wiz.check(
        FakeRunner(),
        settings_path=tmp_path / "settings.toml",
        memory_dir=tmp_path / "memory",
        league_toml=tmp_path / "league.toml",
        which=_which,
    )
    assert report.failed
    assert not (tmp_path / "settings.toml").exists()
    assert not (tmp_path / "memory").exists()


def test_backup_contents(tmp_path):
    (tmp_path / "memory" / "journal").mkdir(parents=True)
    (tmp_path / "memory" / "playbook.md").write_text("p", encoding="utf-8")
    (tmp_path / "memory" / "journal" / "d.md").write_text("j", encoding="utf-8")
    (tmp_path / "memory" / ".env").write_text("SECRET=1", encoding="utf-8")
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "settings.toml").write_text("[owner]\n", encoding="utf-8")
    (tmp_path / "config" / "constants.toml").write_text("", encoding="utf-8")
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "fbm.db").write_bytes(b"db")
    zpath = backup_mod.backup(root=tmp_path)
    assert zpath.parent == tmp_path / "data" / "backups"
    names = set(zipfile.ZipFile(zpath).namelist())
    assert names == {"memory/playbook.md", "memory/journal/d.md", "config/settings.toml", "data/fbm.db"}
