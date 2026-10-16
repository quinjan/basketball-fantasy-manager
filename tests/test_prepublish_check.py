# prepublish: ignore-file (this file deliberately contains fake secrets to test the scanner)
import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "prepublish_check", Path(__file__).resolve().parents[1] / "scripts" / "prepublish_check.py"
)
check = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(check)


def test_flags_secrets_and_masks(tmp_path):
    secret = "a" * 40
    (tmp_path / "bad.py").write_text(
        f'client_secret = "{secret}"\nowner = "someone@gmail.com"\np = r"C:\\Users\\jdoe\\x"\n', encoding="utf-8"
    )
    findings = check.scan_files(tmp_path, ["bad.py"])
    kinds = {f.split(": ")[1] for f in findings}
    assert {"client-secret", "email", "user-path"} <= kinds
    assert all(secret not in f for f in findings)


def test_clean_and_allowed(tmp_path):
    (tmp_path / "ok.md").write_text(
        "Co-authored-by: X <1+x@users.noreply.github.com>\npath C:\\Users\\<you>\\repo\n", encoding="utf-8"
    )
    assert check.scan_files(tmp_path, ["ok.md"]) == []


def test_personal_paths_flagged(tmp_path):
    files = ["memory/playbook.md", "config/settings.toml", "reports/daily/.gitkeep", "config/settings.example.toml"]
    findings = check.scan_files(tmp_path, files)
    assert len(findings) == 2


def test_file_references_and_ignore_marker(tmp_path):
    (tmp_path / "gen.py").write_text('content = "<!-- x -->\\n@AGENTS.md\\n"\n', encoding="utf-8")
    (tmp_path / "fixture.py").write_text(
        f"# {check.IGNORE_FILE_MARKER}\nowner = 'someone@gmail.com'\n", encoding="utf-8"
    )
    assert check.scan_files(tmp_path, ["gen.py", "fixture.py"]) == []
