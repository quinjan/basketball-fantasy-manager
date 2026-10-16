from datetime import UTC, date, datetime

import pytest

from fbm import automation as auto
from fbm.automation import Trigger
from fbm.settings import JOBS, Settings


def test_catalog_covers_jobs_and_roles():
    assert tuple(auto.CATALOG) == JOBS
    assert auto.CATALOG["lineup-guard"].role == "routine" and auto.CATALOG["health"].role == "routine"
    for j in ("daily-gm", "weekly-retro", "trade-scan"):
        assert auto.CATALOG[j].role == "gm"
    hours = [t.hour for t in auto.CATALOG["lineup-guard"].et_triggers]
    assert min(hours) <= 12 and max(hours) >= 22  # early weekend tips through the late slate
    assert auto.CATALOG["weekly-retro"].et_triggers[0].days == (1,)
    assert len(auto.CATALOG["trade-scan"].et_triggers[0].days) == 2


def test_convert_trigger_dst_and_day_shift():
    # Manila (UTC+8, no DST): ET 03:00 = 15:00 in EDT (Oct), 16:00 in EST (Dec).
    assert auto.convert_trigger(Trigger(3, 0), "Asia/Manila", date(2026, 10, 16)) == Trigger(15, 0)
    assert auto.convert_trigger(Trigger(3, 0), "Asia/Manila", date(2026, 12, 16)) == Trigger(16, 0)
    # Monday 22:00 ET -> Tuesday in Manila; Sunday wraps to Monday.
    assert auto.convert_trigger(Trigger(22, 0, (1, 7)), "Asia/Manila", date(2026, 10, 16)) == Trigger(10, 0, (1, 2))
    # Westward: Monday 01:30 ET -> Sunday 22:30 Los Angeles.
    assert auto.convert_trigger(Trigger(1, 30, (1,)), "America/Los_Angeles", date(2026, 10, 16)) == Trigger(
        22, 30, (7,)
    )
    assert auto.convert_trigger(Trigger(5, 0), "America/New_York") == Trigger(5, 0)


def test_next_offset_change():
    assert auto.next_offset_change("Asia/Manila", date(2026, 10, 16)) == date(2026, 11, 1)
    # London and New York both observe DST but switch on different dates.
    assert auto.next_offset_change("Europe/London", date(2026, 10, 16)) == date(2026, 10, 25)
    assert auto.next_offset_change("America/Chicago", date(2026, 10, 16)) is None


def test_validate():
    assert auto.validate("copilot", "scheduler", ["daily-gm"]) == []
    assert auto.validate("generic", "engine-only", ["health"]) == []
    assert auto.validate("generic", "agent-app", ["health"]) == []
    assert "engine-only" in auto.validate("generic", "scheduler", ["health"])[0]
    assert auto.validate("copilot", "cron", ["nope"]) and len(auto.validate("copilot", "cron", ["nope"])) == 2


@pytest.mark.parametrize(
    ("agent", "exe", "must"),
    [
        ("copilot", "copilot", ["-p", "-s", "--no-ask-user", "--allow-tool=fbm", "--deny-tool=shell"]),
        ("claude-code", "claude", ["-p", "--allowedTools=mcp__fbm,Read,Grep,Glob"]),
        ("codex", "codex", ["exec", "--sandbox", "read-only"]),
        ("cursor", "cursor-agent", ["-p", "--approve-mcps"]),
        ("gemini", "gemini", ["--allowed-mcp-server-names", "fbm", "-p"]),
    ],
)
def test_agent_command(agent, exe, must):
    cmd = auto.agent_command(agent, "PROMPT", "m1")
    assert cmd[0] == exe and "PROMPT" in cmd
    assert cmd[cmd.index("--model") + 1] == "m1"
    for flag in must:
        assert flag in cmd
    joined = " ".join(cmd)
    for bad in ("--allow-all", "--yolo", "--force", "dangerously", "danger-full-access", "approval-mode yolo"):
        assert bad not in joined
    assert "--model" not in auto.agent_command(agent, "PROMPT", "")


def test_agent_command_generic_raises():
    with pytest.raises(ValueError):
        auto.agent_command("generic", "x")


def test_plan_run_models_and_modes():
    s = Settings(agent="claude-code", gm_model="big", routine_model="small", automation_mode="scheduler")
    assert auto.plan_run("daily-gm", s)[0][auto.plan_run("daily-gm", s)[0].index("--model") + 1] == "big"
    lg = auto.plan_run("lineup-guard", s)[0]
    assert lg[lg.index("--model") + 1] == "small" and "lineup-guard skill" in " ".join(lg)
    assert auto.plan_run("data-sync", s)[0][-2:] == ["data", "sync"]  # no skill -> deterministic even in scheduler
    eng = auto.plan_run("weekly-retro", s, "engine-only")
    assert eng[0][1:] == ["-m", "fbm.cli", "learn", "retro"]
    assert auto.plan_run("trade-scan", s, "engine-only") == []
    assert "approval" in auto.job_prompt("trade-scan")


def test_task_xml():
    xml = auto.task_xml(
        "trade-scan",
        [Trigger(22, 0, (2, 5)), Trigger(9, 30)],
        ["C:\\py.exe", "-m", "fbm.cli"],
        "C:\\r",
        date(2026, 10, 16),
    )
    assert "<StartBoundary>2026-10-16T22:00:00</StartBoundary>" in xml
    assert "<Tuesday /><Friday />" in xml and "<ScheduleByDay>" in xml
    assert "<Command>C:\\py.exe</Command><Arguments>-m fbm.cli</Arguments>" in xml
    assert "<WorkingDirectory>C:\\r</WorkingDirectory>" in xml and "IgnoreNew" in xml


def test_launchd_plist():
    p = auto.launchd_plist("weekly-retro", [Trigger(17, 0, (1, 7))], ["/v/python", "-m", "fbm.cli"], "/repo")
    assert "<string>com.fbm.weekly-retro</string>" in p
    assert "<key>Weekday</key><integer>1</integer>" in p and "<key>Weekday</key><integer>0</integer>" in p
    assert "<string>/v/python</string><string>-m</string>" in p and "<string>/repo</string>" in p


def test_cron_lines_and_merge():
    lines = auto.cron_lines("health", [Trigger(15, 30), Trigger(4, 0, (7,))], ["/v/py", "-m", "fbm.cli"], "/my repo")
    assert lines[0].startswith("30 15 * * * cd '/my repo' && /v/py -m fbm.cli")
    assert lines[1].startswith("0 4 * * 0 ") and lines[1].endswith("# fbm-health")
    user = "MAILTO=me\n0 1 * * * backup.sh\n"
    merged = auto.merge_crontab(user, lines)
    assert merged.startswith(user) and auto.crontab_block(merged) == lines
    replaced = auto.merge_crontab(merged + "5 5 * * * other.sh\n", lines[:1])
    assert auto.crontab_block(replaced) == lines[:1] and "other.sh" in replaced and "backup.sh" in replaced
    assert replaced.count(auto.CRON_BEGIN) == 1
    removed = auto.merge_crontab(replaced, [])
    assert auto.CRON_BEGIN not in removed and "backup.sh" in removed and "other.sh" in removed
    assert auto.merge_crontab("", []) == ""


def test_plan_entries_and_render(tmp_path):
    s = Settings(owner_timezone="Asia/Manila")
    entries = auto.plan_entries(s, ["health", "trade-scan"], "engine-only", date(2026, 10, 16))
    assert [e.job for e in entries] == ["health"]
    assert entries[0].command[-3:] == ("automation", "run", "health")
    assert entries[0].triggers == (Trigger(15, 30),)
    for backend in ("schtasks", "launchd", "cron"):
        out = auto.render_plan(entries, backend, tmp_path, date(2026, 10, 16))
        assert "health" in out
    text = auto.agent_app_instructions(
        Settings(agent="copilot", owner_timezone="Asia/Manila"), ["daily-gm", "data-sync"]
    )
    assert "Copilot app" in text and "Run the daily-gm skill" in text and "engine-only" in text


def _settings():
    return Settings(agent="codex", automation_mode="engine-only")


def test_run_job_logs_and_stops_on_failure(tmp_path):
    calls = []

    def runner(cmd, cwd):
        calls.append(cmd)
        return (1, "boom") if "snapshot" in cmd else (0, "ok\n")

    now = datetime(2026, 10, 16, 8, 0, tzinfo=UTC)
    code, log = auto.run_job("daily-gm", _settings(), runner=runner, log_dir=tmp_path, now=now)
    assert code == 1 and len(calls) == 1
    assert log.name == "daily-gm-20261016T080000Z.log"
    text = log.read_text(encoding="utf-8")
    assert "boom" in text and "exit=1" in text and "result=1" in text
    assert not (tmp_path / "daily-gm.lock").exists()

    code, _ = auto.run_job("health", _settings(), runner=lambda c, w: (0, "fine"), log_dir=tmp_path, now=now)
    assert code == 0


def test_run_job_lock(tmp_path):
    (tmp_path / "health.lock").write_text("123")
    code, log = auto.run_job("health", _settings(), runner=lambda c, w: (0, ""), log_dir=tmp_path)
    assert code == 3 and "skipped" in log.read_text(encoding="utf-8")


def test_run_job_stale_lock_and_invalid(tmp_path):
    import os

    lock = tmp_path / "health.lock"
    lock.write_text("1")
    os.utime(lock, (0, 0))
    assert auto.run_job("health", _settings(), runner=lambda c, w: (0, ""), log_dir=tmp_path)[0] == 0
    assert auto.run_job("nope", _settings(), log_dir=tmp_path) == (2, None)
    bad = Settings(agent="generic", automation_mode="scheduler")
    assert auto.run_job("daily-gm", bad, log_dir=tmp_path) == (2, None)


def test_run_job_scheduler_invokes_agent(tmp_path):
    seen = []
    s = Settings(agent="copilot", automation_mode="scheduler", routine_model="cheap")
    auto.run_job("lineup-guard", s, runner=lambda c, w: (seen.append(c), (0, ""))[1], log_dir=tmp_path)
    assert seen[0][0] == "copilot" and "cheap" in seen[0]
